"""Measure REAL trigger-to-trigger jitter on DIO4, and decompose where it
comes from.

WHY THIS EXISTS
---------------
The user observed on a physical oscilloscope that the spacing between
DIO4 trigger pulses "occasionally changes ever so slightly". This script
quantifies that from the host side and, more importantly, splits each
cycle into its parts so we can see WHICH part is varying.

The hypothesis it is built to test:

    FpgaTriggerController.start_continuous() compensates its sleep so
    that LOOP ITERATIONS are evenly spaced -- but the pulse is not
    emitted at the top of the iteration. It is emitted after the arming
    step, whose duration varies (notably the "AO wvfrm ready" poll,
    which sleeps in 10 ms steps). So pulse-to-pulse spacing is
    period + (arm_delay[n+1] - arm_delay[n]), and arming jitter passes
    straight through to the scope.

If that is right, `arm_ms` below will be strongly bimodal/quantized in
~10 ms steps and `pulse_interval_ms` will vary by about the same amount.

WHAT IT DOES TO THE HARDWARE
----------------------------
Exactly what normal continuous acquisition does -- the same validated
register sequence as fire_single_trigger(), copied here verbatim with
timestamps added. It does NOT modify the shipped module. Channels stay
at STATIC_ZERO throughout, same as normal operation. On exit it returns
the FPGA to the safe state and closes the session, including on Ctrl-C.

USAGE
-----
    python spikes/16_trigger_jitter_measure.py                 # 100 pulses @ 10 Hz
    python spikes/16_trigger_jitter_measure.py -n 300 -p 0.15  # 300 pulses @ ~6.7 Hz
    python spikes/16_trigger_jitter_measure.py --csv out.csv   # also dump raw rows

Nothing else may hold RIO0 while this runs -- close the UNMScope GUI's
FPGA connection and make sure LouisXIV.exe is not running.
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
import time

import nifpga

from unmscope.hardware.fpga_trigger import (
    AO_MODE_SET_AO,
    AO_MODE_START_RUN_WVFRM,
    AO_POINTS_PER_TRIGGER,
    AO_TICKS_BETWEEN_POINTS,
    BITFILE,
    CYCLE_TICKS,
    ENABLE_HOLD_S,
    HOLDOFF_TICKS,
    RESOURCE,
    STATIC_ZERO,
    WVFRM_SEED_WORDS,
)

#: How often to re-read "AO wvfrm ready" while waiting for the waveform
#: engine to arm. The shipped code uses 0.01 (10 ms), which quantizes the
#: arming delay into 10 ms buckets. Exposed here so we can measure the
#: difference a finer poll makes.
DEFAULT_POLL_S = 0.01


def fire_one_instrumented(session, poll_s: float, wait_ready_timeout_s: float = 3.0) -> dict:
    """One pulse, using the exact validated sequence from
    fire_single_trigger(), with timestamps around each stage.

    Returns a dict of per-stage durations plus `t_pulse`, the monotonic
    timestamp of the register write that actually emits the pulse. That
    timestamp -- not the top of the loop -- is what the oscilloscope
    sees, so it is what the interval statistics are built from.
    """
    regs = session.registers
    t_begin = time.perf_counter()

    # -- Stage 1: setup register writes (~20 PCIe round trips) ---------
    regs["Cam Trigger delay (ticks)"].write(0)
    regs["# of triggers"].write(1)
    regs["Continuous Mode"].write(False)
    regs["Cycle(Ticks)"].write(CYCLE_TICKS)
    regs["Trigger up (ticks)"].write(HOLDOFF_TICKS)
    regs["AO Trigger delay (ticks)"].write(0)
    regs["Free run"].write(False)
    regs["AI # of channels"].write(0)
    regs["AI loop period (ticks)"].write(AO_TICKS_BETWEEN_POINTS)
    regs["AO # of points per trigger"].write(AO_POINTS_PER_TRIGGER)
    regs["AO ticks between points"].write(AO_TICKS_BETWEEN_POINTS)
    regs['Shutter Ticks "on" (Ticks)'].write(0)
    on_off_one = {"# on": 1, "# off": 0}
    regs["Trigger #s"].write(on_off_one)
    regs["Trigger stack #s"].write(on_off_one)
    regs["Trigger blast #s"].write(on_off_one)
    regs["Set F.P. (T)"].write(True)
    t_setup_done = time.perf_counter()

    # -- Stage 2: FIFO seed --------------------------------------------
    fifo = session.fifos["Wvfrm2"]
    fifo.stop()
    fifo.start()
    fifo.write(WVFRM_SEED_WORDS, timeout_ms=2000)
    t_fifo_done = time.perf_counter()

    # -- Stage 3: arm, then poll until the waveform engine is ready ----
    # This is the prime suspect. `polls` counts how many times we had to
    # look; with a 10 ms sleep between looks, polls==1 and polls==2 differ
    # by a full 10 ms of delay before the pulse.
    regs["AO Mode"].write(AO_MODE_START_RUN_WVFRM)
    regs["Set F.P. (T)"].write(True)
    t0 = time.perf_counter()
    ready = False
    polls = 0
    while time.perf_counter() - t0 < wait_ready_timeout_s:
        ready = regs["AO wvfrm ready"].read()
        polls += 1
        if ready:
            break
        time.sleep(poll_s)
    t_ready = time.perf_counter()

    # -- Stage 4: THE PULSE --------------------------------------------
    # t_pulse is the moment the scope sees an edge. Everything above is
    # jitter that shifts this timestamp around within the cycle.
    t_pulse = None
    if ready:
        t_pulse = time.perf_counter()
        regs["Trigger Enable?"].write(True)
        regs["Set F.P. (T)"].write(True)
        time.sleep(ENABLE_HOLD_S)
        regs["Trigger Enable?"].write(False)
        regs["Set F.P. (T)"].write(True)
    t_fired = time.perf_counter()

    # -- Stage 5: disarm + return to static zero ------------------------
    fifo.stop()
    regs["AO Mode"].write(AO_MODE_SET_AO)
    regs["Static AO to set"].write(STATIC_ZERO)
    regs["Set F.P. (T)"].write(True)
    t_end = time.perf_counter()

    ms = lambda a, b: (b - a) * 1000.0  # noqa: E731
    return {
        "fired": ready,
        "t_pulse": t_pulse,
        "polls": polls,
        "setup_ms": ms(t_begin, t_setup_done),
        "fifo_ms": ms(t_setup_done, t_fifo_done),
        "arm_ms": ms(t_fifo_done, t_ready),
        "pulse_ms": ms(t_ready, t_fired),
        "teardown_ms": ms(t_fired, t_end),
        "total_ms": ms(t_begin, t_end),
        # How far into the cycle the pulse landed. Variation in THIS is
        # what turns into variation in on-scope pulse spacing.
        "offset_to_pulse_ms": ms(t_begin, t_pulse) if t_pulse else float("nan"),
    }


def describe(name: str, values: list[float], unit: str = "ms") -> str:
    if not values:
        return f"  {name:<22} (no data)"
    lo, hi = min(values), max(values)
    mean = statistics.fmean(values)
    sd = statistics.stdev(values) if len(values) > 1 else 0.0
    return (
        f"  {name:<22} mean {mean:8.3f}  sd {sd:7.3f}  "
        f"min {lo:8.3f}  max {hi:8.3f}  spread {hi - lo:7.3f} {unit}"
    )


def histogram(values: list[float], bin_ms: float = 1.0, width: int = 46) -> list[str]:
    """Bin values so quantization is visible. A 10 ms comb means the
    readiness poll; a ~15.6 ms comb means Windows timer granularity."""
    if not values:
        return ["  (no data)"]
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return [f"  all {len(values)} samples at {lo:.3f} ms"]
    nbins = max(1, min(40, int((hi - lo) / bin_ms) + 1))
    step = (hi - lo) / nbins
    counts = [0] * nbins
    for v in values:
        counts[min(nbins - 1, int((v - lo) / step))] += 1
    peak = max(counts) or 1
    out = []
    for i, c in enumerate(counts):
        if c == 0:
            continue
        edge = lo + i * step
        bar = "#" * max(1, round(c / peak * width))
        out.append(f"  {edge:8.3f} - {edge + step:8.3f} ms | {c:4d} {bar}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", "--count", type=int, default=100, help="pulses to fire (default 100)")
    ap.add_argument("-p", "--period", type=float, default=0.1097,
                    help="requested trigger-to-trigger period in seconds "
                         "(default 0.1097 = 100 ms exposure + 9.7 ms Orca readout)")
    ap.add_argument("--poll", type=float, default=DEFAULT_POLL_S,
                    help=f"'AO wvfrm ready' poll interval in seconds (default {DEFAULT_POLL_S}, "
                         "the value the shipped code uses). Try 0.0002 to test the poll hypothesis.")
    ap.add_argument("--csv", type=str, default=None, help="write raw per-pulse rows to this CSV")
    args = ap.parse_args()

    print(f"Connecting to FPGA ({RESOURCE})...")
    print(f"  bitfile: {BITFILE}")
    session = nifpga.Session(bitfile=BITFILE, resource=RESOURCE)
    rows: list[dict] = []
    try:
        session.reset()
        time.sleep(0.3)
        session.run()
        time.sleep(0.3)
        regs = session.registers
        regs["AO Mode"].write(AO_MODE_SET_AO)
        regs["Static AO to set"].write(STATIC_ZERO)
        regs["Set F.P. (T)"].write(True)
        print("Connected, FPGA in safe state.\n")

        print(f"Firing {args.count} pulses, requested period {args.period * 1000:.2f} ms "
              f"({1.0 / args.period:.2f} Hz), readiness poll {args.poll * 1000:.2f} ms.")
        print("Watch the scope. Ctrl-C stops early and still reports.\n")

        for i in range(args.count):
            cycle_start = time.perf_counter()
            r = fire_one_instrumented(session, poll_s=args.poll)
            r["i"] = i
            rows.append(r)
            if not r["fired"]:
                print(f"  pulse {i}: NEVER ARMED (AO wvfrm ready stayed False) -- stopping")
                break
            if (i + 1) % 20 == 0:
                print(f"  ...{i + 1}/{args.count}")
            if i < args.count - 1:
                remaining = args.period - (time.perf_counter() - cycle_start)
                if remaining > 0:
                    time.sleep(remaining)
    except KeyboardInterrupt:
        print("\nInterrupted -- reporting what we have.")
    finally:
        try:
            regs = session.registers
            regs["Trigger Enable?"].write(False)
            regs["AO Mode"].write(AO_MODE_SET_AO)
            regs["Static AO to set"].write(STATIC_ZERO)
            regs["Set F.P. (T)"].write(True)
            session.fifos["Wvfrm2"].stop()
        except Exception as e:
            print(f"  (safe-state cleanup raised: {e})")
        session.close()
        print("FPGA returned to safe state, session closed.\n")

    fired = [r for r in rows if r["fired"]]
    if len(fired) < 2:
        print("Not enough pulses fired to compute intervals.")
        return 1

    # THE headline number: real pulse-to-pulse spacing, measured at the
    # register write that emits the edge -- the same event the scope sees.
    intervals = [(b["t_pulse"] - a["t_pulse"]) * 1000.0 for a, b in zip(fired, fired[1:])]

    print("=" * 74)
    print(f"RESULTS  ({len(fired)} pulses, {len(intervals)} intervals, "
          f"requested period {args.period * 1000:.2f} ms)")
    print("=" * 74)
    print("\nPULSE-TO-PULSE INTERVAL  <-- this is what the oscilloscope shows")
    print(describe("interval", intervals))
    print(f"  achieved rate          {1000.0 / statistics.fmean(intervals):.3f} Hz "
          f"(requested {1.0 / args.period:.3f} Hz)")
    print("\n  histogram:")
    for line in histogram(intervals):
        print(line)

    print("\nPER-STAGE BREAKDOWN  <-- which stage is varying")
    for key, label in [
        ("setup_ms", "setup writes"),
        ("fifo_ms", "FIFO seed"),
        ("arm_ms", "arming + ready poll"),
        ("pulse_ms", "pulse hold"),
        ("teardown_ms", "teardown"),
        ("total_ms", "total fire call"),
        ("offset_to_pulse_ms", "cycle start -> pulse"),
    ]:
        print(describe(label, [r[key] for r in fired]))

    polls = [r["polls"] for r in fired]
    print(f"\n  'AO wvfrm ready' reads per pulse: min {min(polls)} max {max(polls)} "
          f"mean {statistics.fmean(polls):.2f}")
    if max(polls) > min(polls):
        print(f"  -> the poll count VARIES. With a {args.poll * 1000:.2f} ms sleep between reads,")
        print(f"     that alone injects up to {(max(polls) - min(polls)) * args.poll * 1000:.2f} ms "
              "of jitter into when the pulse fires.")

    print("\n  arming-delay histogram (a comb here = quantized waiting):")
    for line in histogram([r["arm_ms"] for r in fired]):
        print(line)

    print("\nINTERPRETATION")
    spread = max(intervals) - min(intervals)
    arm_spread = max(r["arm_ms"] for r in fired) - min(r["arm_ms"] for r in fired)
    print(f"  interval spread    {spread:7.3f} ms")
    print(f"  arming spread      {arm_spread:7.3f} ms")
    if arm_spread > 0.5 * spread:
        print("  -> Arming variation accounts for most of the interval spread.")
        print("     CONFIRMS the hypothesis: the pulse is emitted after a")
        print("     variable-length arming step, so arming jitter passes")
        print("     straight through to on-scope pulse spacing.")
    else:
        print("  -> Arming does NOT explain the spread; look at OS sleep")
        print("     granularity, GIL/GC contention, or PCIe latency instead.")

    if args.csv:
        with open(args.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=[k for k in rows[0] if k != "t_pulse"])
            w.writeheader()
            for r in rows:
                w.writerow({k: v for k, v in r.items() if k != "t_pulse"})
        print(f"\nRaw rows written to {args.csv}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
