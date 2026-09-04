"""Free-run trigger train: arm the FPGA ONCE and let its 40 MHz counter
time DIO4 -- the LabVIEW way (docs/trigger_free_run_plan.md).

This is the first hardware test of FpgaTriggerController.start_free_run().
It does NOT need an oscilloscope to say whether it worked: the FPGA's own
'# of triggers read' counter (read while armed -- it resets on disarm) is
the accepted-trigger count, and with --camera the Orca counts the pulses
a second, independent way by delivering one frame per pulse.

WHAT IT DOES TO THE HARDWARE
----------------------------
Same register family as every verified pulse so far; the only new thing
is that 'Trigger Enable?' is held True for the whole run while a refill
thread keeps the Wvfrm2 AO DMA FIFO topped up with ZEROS, so every AO
channel stays at 0 V (same as the safe state). If the AO engine faults it
stops and reports; recovery is the usual reset()+run() at next connect.

USAGE
-----
    python -u spikes/19_free_run_trigger.py                       # 5 s continuous @ 100 ms exposure, FPGA only
    python -u spikes/19_free_run_trigger.py --seconds 10 --camera  # + the Orca counting frames
    python -u spikes/19_free_run_trigger.py --count 20             # bounded burst: FPGA self-stops after 20
    python -u spikes/19_free_run_trigger.py --exposure 0.02        # faster train (period = exposure + readout)

Nothing else may hold RIO0 (or the camera, with --camera).
"""
from __future__ import annotations

import argparse
import statistics
import sys
import threading
import time

from unmscope.hardware.fpga_trigger import (
    FpgaTriggerController, FreeRunStatus, free_run_timing, TICKS_PER_S,
)


def describe(name: str, values: list[float], unit: str = "ms") -> str:
    if len(values) < 2:
        return f"  {name:<26} (n={len(values)})"
    mean = statistics.fmean(values)
    sd = statistics.stdev(values)
    return (f"  {name:<26} n={len(values):4d}  mean {mean:8.3f}  sd {sd:6.3f}  "
            f"min {min(values):8.3f}  max {max(values):8.3f}  spread {max(values)-min(values):6.3f} {unit}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=5.0, help="how long to hold the free run (continuous mode)")
    ap.add_argument("--exposure", type=float, default=0.100, help="camera exposure in s (default 0.1)")
    ap.add_argument("--period", type=float, default=None,
                    help="trigger-to-trigger period in s (default exposure + camera ReadoutTime + margin)")
    ap.add_argument("--margin", type=float, default=0.5,
                    help="ms added on top of exposure + readout when --period is not given (default 0.5)")
    ap.add_argument("--count", type=int, default=None, help="bounded burst of N triggers instead of continuous")
    ap.add_argument("--cam-delay-ticks", type=int, default=0, help="Cam Trigger delay (ticks); LabVIEW uses 6600 for the Orca")
    ap.add_argument("--camera", action="store_true", help="also arm the real Orca in EXTERNAL trigger mode and count frames")
    ap.add_argument("--ao-points", type=int, default=1, help="AO # of points per trigger (default 1)")
    ap.add_argument("--ao-ticks", type=int, default=4000, help="AO ticks between points (default 4000 = 10 kHz)")
    ap.add_argument("--no-pb", action="store_true", help="do NOT mirror the AO timing into the '... PB' registers")
    ap.add_argument("--blast", type=str, default="1,0", help="'Trigger blast #s' as '# on,# off' (default 1,0)")
    ap.add_argument("--dma-timeout", type=int, default=100, help="'AO DMA Timeout (ticks per read)' (LabVIEW 100; bitfile default 40)")
    args = ap.parse_args()

    cam = None
    # Fallback when no camera is attached (camera.py's constant); with
    # --camera the Orca's own 'ReadoutTime' property replaces it.
    readout_ms = 33.3
    if args.camera:
        from unmscope.hardware.camera import OrcaFlash4Camera, other_camera_holders
        holders = other_camera_holders()
        if holders:
            print(f"WARNING: {holders} running -- if it holds the camera this can crash DCAM.")
        print("Connecting camera...")
        cam = OrcaFlash4Camera()
        cam.connect()
        cam.set_exposure_ms(args.exposure * 1e3)
        cam.set_trigger_source("EXTERNAL")
        cam.set_trigger_polarity("POSITIVE")
        readout_ms = cam.readout_ms()
        print(f"  {cam.info}  exposure {cam.get_exposure_ms():.3f} ms  "
              f"TRIGGER SOURCE={cam.get_property('TRIGGER SOURCE')} TriggerPolarity={cam.get_property('TriggerPolarity')}")
        print(f"  camera ReadoutTime {readout_ms:.2f} ms -> min EDGE-trigger period {cam.min_frame_period_ms():.2f} ms")
        cam.start_sequence(100000)

    period = args.period if args.period is not None else args.exposure + (readout_ms + args.margin) / 1000.0
    cycle, up = free_run_timing(period, args.exposure)
    expected_hz = 1.0 / period
    print(f"period {period*1e3:.3f} ms ({expected_hz:.3f} Hz)  exposure {args.exposure*1e3:.1f} ms  "
          f"(readout {readout_ms:.2f} ms + margin {args.margin:.2f} ms)")
    print(f"Cycle(Ticks) = {cycle}  ({cycle/TICKS_PER_S*1e3:.3f} ms)   Trigger up (ticks) = {up}  ({up/TICKS_PER_S*1e3:.3f} ms)")
    print(f"mode: {'BOUNDED, ' + str(args.count) + ' triggers' if args.count else 'CONTINUOUS, ' + str(args.seconds) + ' s'}"
          f"   camera: {'ON' if args.camera else 'off'}   AO points/trigger {args.ao_points} @ {args.ao_ticks} ticks\n")

    print("Connecting FPGA (reset+run, safe state)...")
    ctrl = FpgaTriggerController()
    ctrl.connect()

    lock = threading.Lock()
    count_samples: list[tuple[float, int]] = []   # (t since arm, triggers read) at each change
    statuses: list[FreeRunStatus] = []
    errors: list[str] = []

    def on_count(n):
        with lock:
            count_samples.append((time.perf_counter() - ctrl._free_run_t0, n))

    def on_status(st):
        with lock:
            statuses.append(st)

    def on_error(msg):
        with lock:
            errors.append(msg)
        print(f"  !! {msg}", flush=True)

    frame_times: list[float] = []
    frame_md: list[dict] = []

    def drain():
        """Pop every buffered frame; keep host arrival time + the camera's
        own per-frame metadata (the Orca stamps frames itself, which is a
        second clock for pulse spacing that needs no oscilloscope)."""
        while cam.remaining_image_count() > 0:
            d = {}
            try:
                _img, md = cam._mmc.popNextImageAndMD()
                try:
                    for k in md.Keys():
                        d[k] = md.GetSingleTag(k).GetValue()
                except Exception:
                    d = {}
            except Exception:
                cam.pop_image()
            frame_times.append(time.perf_counter())
            frame_md.append(d)

    final = -1
    t_stop = None
    try:
        if cam is not None:
            # The Orca hands over one stale frame right after the sequence
            # starts in EXTERNAL mode (seen arriving ~10 ms after arm --
            # impossible for a 100 ms exposure). Flush it BEFORE arming so
            # frames == triggers is a real check.
            time.sleep(max(0.5, 2 * period))
            drain()
            pre = len(frame_times)
            frame_times.clear(); frame_md.clear()
            print(f"Discarded {pre} pre-arm frame(s) from the camera buffer.")
        print("Arming free run...")
        ok = ctrl.start_free_run(period, args.exposure, n_triggers=args.count,
                                 cam_trigger_delay_ticks=args.cam_delay_ticks,
                                 ao_points_per_trigger=args.ao_points, ao_ticks_between_points=args.ao_ticks,
                                 write_pb_registers=not args.no_pb, ao_dma_timeout_ticks=args.dma_timeout,
                                 trigger_blast={"# on": int(args.blast.split(",")[0]), "# off": int(args.blast.split(",")[1])},
                                 on_status=on_status, on_trigger_count=on_count, on_error=on_error)
        if not ok:
            print(f"ARM FAILED: {ctrl.last_error}")
            return 1
        print(f"Armed at t=0. {'Watch the scope. ' if not cam else ''}Ctrl-C stops early.\n")
        print(f"  {'t(s)':>6} {'trig':>6} {'ign':>4} {'AOgen':>6} {'left':>4} {'rdy':>3} {'st':>2} "
              f"{'DMAerr':>6} {'fifoFree':>8} {'refillW':>8} {'rTO':>4} {'IntMism':>7}{'  frames' if cam else ''}")
        next_print = 1.0
        deadline = args.seconds if args.count is None else 1e9
        while True:
            now = time.perf_counter() - ctrl._free_run_t0
            if cam is not None:
                drain()
            if now >= next_print:
                st = ctrl.last_status
                if st is not None:
                    err = "".join("1" if v else "0" for v in st.ao_dma_error.values())
                    print(f"  {st.t:6.2f} {st.triggers_read:6d} {st.triggers_ignored:4d} {st.ao_generated:6d} "
                          f"{st.ao_points_left:4d} {int(st.ao_wvfrm_ready):3d} {st.ao_waveform_state:2d} "
                          f"{err:>6} {st.fifo_empty_remaining:8d} {st.refill_words_written:8d} "
                          f"{st.refill_timeouts:4d} {st.int_cycle_mismatches:3d}/{st.int_cycle_samples:<3d}"
                          + (f"  {len(frame_times):6d}" if cam else ""), flush=True)
                next_print += 1.0
            if errors:
                print("  stopping on error.")
                break
            if args.count is not None:
                st = ctrl.last_status
                if st is not None and st.triggers_read >= args.count:
                    time.sleep(2 * period + 0.2)   # give the FPGA a chance to (wrongly) fire more
                    if cam is not None:
                        drain()
                    break
                if now > args.count * period * 3 + 5:
                    print("  bounded burst did not reach its count in 3x the expected time.")
                    break
            elif now >= deadline:
                break
            time.sleep(0.002)
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        t_stop = time.perf_counter() - ctrl._free_run_t0
        final = ctrl.stop_free_run()
        print(f"\nDisarmed at t={t_stop:.3f}s. FPGA final '# of triggers read' = {final}")
        if cam is not None:
            time.sleep(max(0.3, 2 * period))   # let the last exposure+readout finish and land
            drain()
            overflow = cam.is_buffer_overflowed()
            cam.stop_sequence()
            try:
                cam.set_trigger_source("INTERNAL")
            except Exception:
                pass
            cam.disconnect()
            print(f"Camera released (buffer overflowed: {overflow}).")
        ctrl.close()
        print("FPGA in safe state, session closed.")

    # ------------------------------------------------------------------ report
    print("\n" + "=" * 74)
    print("RESULT")
    print("=" * 74)
    with lock:
        samples = list(count_samples)
        sts = list(statuses)
    if args.count is not None:
        print(f"  bounded burst: requested {args.count}, FPGA accepted {final}"
              f"  -> {'EXACT' if final == args.count else 'MISMATCH'}")
    else:
        # The first trigger fires at t=0 (the sync counter is reset by the
        # enable), so n triggers span n-1 periods: (n-1)P <= t_stop < nP.
        # A small first-edge delay after the enable write makes the count
        # at the run boundary ambiguous by one, so accept expected-1 too.
        expected = int(t_stop / period) + 1
        count_ok = final in (expected, expected - 1)
        print(f"  continuous: armed {t_stop:.3f} s -> expect {expected} (or {expected - 1}) triggers at {period*1e3:.3f} ms; "
              f"FPGA accepted {final}  -> {'OK' if count_ok else 'MISMATCH'}")
        if final > 1:
            print(f"  period bracket from the counter: {t_stop / final * 1e3:.2f} ms < P <= {t_stop / (final - 1) * 1e3:.2f} ms"
                  f"   (requested {period*1e3:.3f} ms)")
    if len(samples) >= 2:
        (t0, n0), (t1, n1) = samples[0], samples[-1]
        if t1 > t0 and n1 > n0:
            print(f"  counter slope (host-sampled every 50 ms): {(n1 - n0) / (t1 - t0):.3f} Hz over {n1 - n0} triggers")
        steps = [b[1] - a[1] for a, b in zip(samples, samples[1:])]
        if any(s > 1 for s in steps):
            print(f"  note: counter advanced by >1 between samples {sum(1 for s in steps if s > 1)} times "
                  "(monitor sampling, not necessarily missed pulses)")
    if sts:
        last = sts[-1]
        print(f"  triggers ignored: {last.triggers_ignored}   AO points generated: {last.ao_generated}   "
              f"AO points left: {last.ao_points_left}")
        print(f"  refill: {last.refill_words_written} words written, {last.refill_timeouts} full-buffer timeouts, "
              f"host buffer free at end: {last.fifo_empty_remaining}")
        mism = sum(s.int_cycle_mismatches for s in sts)
        tot = sum(s.int_cycle_samples for s in sts)
        print(f"  Int Cycle Trigger vs Int Cycle+Added Trigger: {mism}/{tot} samples disagreed"
              f"  -> {'added-time path is LIVE' if mism else 'no added-time modulation seen'}")
        faults = [s for s in sts if any(s.ao_dma_error.values())]
        print(f"  AO DMA faults: {len(faults)} status samples" + (f" (first at t={faults[0].t:.2f}s)" if faults else ""))
    if errors:
        print(f"  ERRORS: {errors}")
    frames_ok = True
    if cam is not None:
        nf = len(frame_times)
        print(f"\n  CAMERA: {nf} frames received vs {final} FPGA triggers -> "
              + ("MATCH" if nf == final else f"DIFF {nf - final:+d}"))
        frames_ok = nf >= final          # extras are investigated below; DROPS fail
        rel = [(t - ctrl._free_run_t0) * 1e3 for t in frame_times]
        if rel:
            head = ", ".join(f"{x:.1f}" for x in rel[:3])
            tail = ", ".join(f"{x:.1f}" for x in rel[-3:])
            print(f"  arrival times rel. to arm (ms): first [{head}]  last [{tail}]  (disarm at {t_stop*1e3:.1f})")
        if len(frame_times) >= 3:
            iv = [(b - a) * 1e3 for a, b in zip(frame_times, frame_times[1:])]
            print(describe("frame interval (host arrival)", iv))
        if frame_md and frame_md[0]:
            keys = sorted(frame_md[0].keys())
            print(f"  frame metadata keys: {keys}")
            # Any camera-side timestamp gives pulse spacing from the camera's clock.
            for key in keys:
                kl = key.lower()
                if "timestamp" in kl or "elapsedtime" in kl or kl.endswith("-time"):
                    try:
                        vals = [float(d[key]) for d in frame_md]
                    except Exception:
                        continue
                    if len(vals) < 3:
                        continue
                    scale = 1e3 if max(vals) - min(vals) < 1e3 else 1.0   # seconds -> ms, else assume ms
                    iv2 = [(b - a) * scale for a, b in zip(vals, vals[1:])]
                    print(describe(f"interval from '{key}'", iv2))
    print()
    ok = not errors and frames_ok and (final == args.count if args.count is not None else count_ok)
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
