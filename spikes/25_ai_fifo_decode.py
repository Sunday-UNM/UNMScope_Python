"""FPGA Scope, step 1: capture the FPGA's 'AI data' DMA FIFO while the
free-run trigger runs, and work out how the stream is packed.

FPGA ONLY. No camera, no galvo, nothing else. All AO channels stay at 0.

HYPOTHESIS (from the SOURCE build's diagrams -- the deployed bitfile is a
different build, so this is tested, not trusted):
  HHMI - FPGA AI Loop.vi builds, every 'AI loop period (ticks)', ONE I16
  array of 29 elements:
     0..7   Connector0/AI0..AI7 (real analog inputs)
     8..13  AO DMA cluster values: X Galvo, Z Galvo, Z Piezo, Dither Galvo,
            AOTF0, Filter desired
    14      Int Sync (Cycle Only)      <- digital, bit-shifted by 12 (0/4096)
    15      Cam Ext Trigger Out DO     <- DIO4 read back, 0/4096
    16..22  AOTF0..AOTF6 (digital), 23 Perfusion?, 24 Shutter,
    25..28  Channel Shutter 0..3
  HHMI - Send AI data to DMA.vi then writes the FIRST 'AI # of channels'
  elements of that array to the FIFO, so a frame = N consecutive I16s.
  LouisXIV's AI buffer shows rows (8,14,15,16,17,9,10,11).

REFERENCE SIGNAL: the free-run trigger. Cycle(Ticks) = 100 ms, DIO4 high
4000 ticks = 100 us (a ~2-sample blip at 20 kS/s), Int Sync high =
Trigger up = (period - exposure)/2 = 10 ms with exposure 80 ms.

WHAT IT REPORTS: per column min/max/mean/unique count; which columns are
binary and their period/duty (in samples and ms); the column whose period
is 100 ms with a ~100 us high time is DIO4; the FPGA-measured trigger
period from those edges is the software-oscilloscope result.

    python -u spikes/25_ai_fifo_decode.py                 # 29 ch, 20 kS/s, 3 s
    python -u spikes/25_ai_fifo_decode.py -n 40           # probe beyond 29
    python -u spikes/25_ai_fifo_decode.py --period-ticks 400 --seconds 2   # 100 kS/s
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time

import nifpga
import numpy as np

from unmscope.hardware.fpga_trigger import FpgaTriggerController, TICKS_PER_S

SOURCE_NAMES = (["AI0", "AI1", "AI2", "AI3", "AI4", "AI5", "AI6", "AI7",
                 "X Galvo(AO)", "Z Galvo(AO)", "Z Piezo(AO)", "Dither Galvo(AO)", "AOTF0(AO)", "Filter desired",
                 "Int Sync (Cycle Only)", "Cam Ext Trigger Out DO (DIO4)",
                 "AOTF0", "AOTF1", "AOTF2", "AOTF3", "AOTF4", "AOTF5", "AOTF6",
                 "Perfusion?", "Shutter", "Channel Shutter 0", "Channel Shutter 1",
                 "Channel Shutter 2", "Channel Shutter 3"])


def edges(col: np.ndarray, thresh: float):
    hi = col > thresh
    rising = np.flatnonzero(~hi[:-1] & hi[1:]) + 1
    falling = np.flatnonzero(hi[:-1] & ~hi[1:]) + 1
    return rising, falling


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", "--channels", type=int, default=29, help="'AI # of channels' (elements per frame)")
    ap.add_argument("--period-ticks", type=int, default=2000, help="'AI loop period (ticks)' (2000 = 20 kS/s)")
    ap.add_argument("--seconds", type=float, default=3.0)
    ap.add_argument("--trigger-period", type=float, default=0.100)
    ap.add_argument("--exposure", type=float, default=0.080, help="sets Trigger up = (period-exposure)/2 = Int Sync high time")
    ap.add_argument("--no-free-run", action="store_true", help="leave the AI 'Free run' flag False")
    ap.add_argument("--save", type=str, default=None, help="save the raw capture (.npy) here")
    args = ap.parse_args()

    n = args.channels
    fs = TICKS_PER_S / args.period_ticks
    print(f"AI # of channels = {n}, AI loop period = {args.period_ticks} ticks -> {fs:,.0f} S/s per channel, "
          f"{fs * n:,.0f} I16/s total; capture {args.seconds} s; trigger {args.trigger_period*1e3:.1f} ms period")

    ctrl = FpgaTriggerController()
    ctrl.connect()
    session = ctrl._session
    regs = session.registers
    fifo = session.fifos["AI data"]
    print(f"FIFO 'AI data': datatype {fifo.datatype}")

    samples = []
    stats = {"reads": 0, "remaining_max": 0, "timeouts": 0}
    ai_err_seen = None
    try:
        fifo.stop()
        fifo.configure(4_000_000)
        fifo.start()
        ok = ctrl.start_free_run(args.trigger_period, args.exposure, n_triggers=None,
                                 ai_channels=n, ai_period_ticks=args.period_ticks,
                                 ai_free_run=not args.no_free_run)
        if not ok:
            print("ARM FAILED:", ctrl.last_error)
            return 1
        print(f"Armed. 'AI Error' = {dict(regs['AI Error'].read())}  'Free run' = {regs['Free run'].read()}")
        t0 = time.perf_counter()
        chunk = n * max(1, int(fs * 0.05))          # ~50 ms of frames per read
        while time.perf_counter() - t0 < args.seconds:
            avail = fifo.read(0, timeout_ms=0).elements_remaining
            take = min(avail - (avail % n), chunk)
            if take <= 0:
                time.sleep(0.005)
                continue
            r = fifo.read(take, timeout_ms=100)
            samples.append(np.asarray(r.data, dtype=np.int16))
            stats["reads"] += 1
            stats["remaining_max"] = max(stats["remaining_max"], r.elements_remaining)
        ai_err_seen = dict(regs["AI Error"].read())
        st = ctrl.last_status
        print(f"Capture done: {stats['reads']} reads, max backlog {stats['remaining_max']} elements, "
              f"'AI Error' = {ai_err_seen}, FPGA triggers so far = {st.triggers_read if st else '?'}")
    finally:
        try:
            ctrl.stop_free_run()
        except Exception as e:
            print("stop_free_run raised:", e)
        try:
            fifo.stop()
        except Exception:
            pass
        try:
            regs["AI # of channels"].write(0)
            regs["Free run"].write(False)
            regs["Set F.P. (T)"].write(True)
        except Exception:
            pass
        ctrl.close()
        print("FPGA safe, session closed.")

    if not samples:
        print("\nNO DATA arrived on 'AI data'. Try --period-ticks larger, or check 'Free run' / 'AI acq' gating.")
        return 1
    raw = np.concatenate(samples)
    frames = raw[: len(raw) - (len(raw) % n)].reshape(-1, n)
    dt_ms = 1000.0 / fs
    print(f"\n{len(raw):,} I16 words = {frames.shape[0]:,} frames of {n} ({frames.shape[0] * dt_ms / 1000:.2f} s of data at {fs:,.0f} S/s)")
    if args.save:
        np.save(args.save, frames)
        print(f"raw frames saved to {args.save}")

    print("\n" + "=" * 100)
    print(f"{'col':>3} {'source-build name':<32} {'min':>7} {'max':>7} {'mean':>9} {'std':>8} {'uniq':>5}  binary? period/duty")
    print("=" * 100)
    binary_cols = {}
    for c in range(n):
        col = frames[:, c].astype(np.int32)
        uniq = np.unique(col)
        name = SOURCE_NAMES[c] if c < len(SOURCE_NAMES) else "(beyond source array)"
        note = ""
        if len(uniq) == 2 or (len(uniq) <= 3 and 0 in uniq and uniq.max() >= 1024):
            lo, hi = int(uniq.min()), int(uniq.max())
            thresh = (lo + hi) / 2.0
            rising, falling = edges(col, thresh)
            duty = float(np.mean(col > thresh))
            if len(rising) >= 2:
                per = np.diff(rising) * dt_ms
                highs = []
                for r_ in rising:
                    f_ = falling[falling > r_]
                    if len(f_):
                        highs.append((f_[0] - r_) * dt_ms)
                note = (f"BINARY {lo}/{hi}  {len(rising)} rising edges, period {per.mean():.3f} ms "
                        f"(sd {per.std():.3f}, min {per.min():.3f}, max {per.max():.3f}), "
                        f"high {statistics.fmean(highs) if highs else float('nan'):.3f} ms, duty {duty*100:.2f}%")
                binary_cols[c] = {"lo": lo, "hi": hi, "period_ms": float(per.mean()), "period_sd_ms": float(per.std()),
                                  "high_ms": float(statistics.fmean(highs)) if highs else None, "edges": int(len(rising))}
            else:
                note = f"BINARY {lo}/{hi}  {len(rising)} rising edges, duty {duty*100:.2f}%"
        elif len(uniq) == 1:
            note = "CONSTANT"
        print(f"{c:>3} {name:<32} {col.min():>7} {col.max():>7} {col.mean():>9.1f} {col.std():>8.1f} {len(uniq):>5}  {note}")

    print("\nINTERPRETATION")
    want = args.trigger_period * 1e3
    dio4 = [c for c, b in binary_cols.items() if b["high_ms"] is not None and b["high_ms"] < 1.0 and abs(b["period_ms"] - want) < 0.05 * want]
    sync = [c for c, b in binary_cols.items() if b["high_ms"] is not None and b["high_ms"] >= 1.0 and abs(b["period_ms"] - want) < 0.05 * want]
    print(f"  columns pulsing at the trigger period with a ~100 us high (DIO4 candidates): {dio4}")
    print(f"  columns pulsing at the trigger period with a long high (Int Sync candidates): {sync}")
    for c in dio4:
        b = binary_cols[c]
        print(f"  -> col {c}: FPGA-clocked trigger period {b['period_ms']:.4f} ms (sd {b['period_sd_ms']:.4f} ms, "
              f"one AI sample = {dt_ms:.4f} ms) vs requested {want:.4f} ms")
    json.dump({"channels": n, "period_ticks": args.period_ticks, "binary_cols": binary_cols,
               "dio4_candidates": dio4, "sync_candidates": sync},
              open("docs/ai_fifo_decode_result.json", "w"), indent=2)
    print("  summary written to docs/ai_fifo_decode_result.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
