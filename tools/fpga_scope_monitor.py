"""FPGA Scope monitor -- the software oscilloscope, in a terminal.

FPGA ONLY (no camera, no galvo). Connects to RIO0, streams the 'AI data'
FIFO through FpgaScope and, by default, also free-runs the camera trigger
so there is something to look at. Once a second it prints the DIO4
trigger period/jitter measured on the FPGA's own sample clock, the Int
Sync high time, the analog inputs in volts, and the stream health.

    python tools/fpga_scope_monitor.py                       # 10 Hz trigger, 100 kS/s x 16 ch, until Ctrl-C
    python tools/fpga_scope_monitor.py --seconds 10 --trigger-period 0.05
    python tools/fpga_scope_monitor.py --no-trigger          # just watch the inputs
    python tools/fpga_scope_monitor.py --period-ticks 2000   # 20 kS/s

All AO channels stay at zero throughout.
"""
from __future__ import annotations

import argparse
import sys
import time

import numpy as np

from unmscope.hardware.fpga_scope import AI_VOLTS_PER_COUNT, IDX_DIO4, IDX_INT_SYNC, FpgaScope, measure_period
from unmscope.hardware.fpga_trigger import FpgaTriggerController


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=0.0, help="run time (0 = until Ctrl-C)")
    ap.add_argument("--channels", type=int, default=16)
    ap.add_argument("--period-ticks", type=int, default=400, help="AI sample period (400 = 100 kS/s, 2000 = 20 kS/s)")
    ap.add_argument("--trigger-period", type=float, default=0.100)
    ap.add_argument("--exposure", type=float, default=0.080, help="only sets the Int Sync high time (period-exposure)/2")
    ap.add_argument("--no-trigger", action="store_true")
    args = ap.parse_args()

    ctrl = FpgaTriggerController()
    print("Connecting FPGA (reset+run, safe state)...")
    ctrl.connect()
    scope = FpgaScope(ctrl, channels=args.channels, period_ticks=args.period_ticks, buffer_seconds=5.0)
    try:
        scope.start()
        print(f"Scope streaming {scope.channels} channels at {scope.fs_hz:,.0f} S/s "
              f"({scope.fs_hz * scope.channels:,.0f} I16/s), ring {scope.ring.capacity} frames.")
        if not args.no_trigger:
            ok = ctrl.start_free_run(args.trigger_period, args.exposure)
            if not ok:
                print("ARM FAILED:", ctrl.last_error)
                return 1
            print(f"Trigger free-running at {args.trigger_period * 1e3:.3f} ms "
                  f"(Cycle(Ticks) = {round(args.trigger_period * 40e6)}). Ctrl-C to stop.\n")
        t0 = time.perf_counter()
        next_print = 1.0
        while args.seconds <= 0 or time.perf_counter() - t0 < args.seconds:
            time.sleep(0.05)
            if time.perf_counter() - t0 < next_print:
                continue
            next_print += 1.0
            snap = scope.snapshot(seconds=1.0)
            if len(snap.frames) == 0:
                print("  (no frames yet)")
                continue
            line = f"t={time.perf_counter() - t0:5.1f}s  frames {scope.ring.total:>8,}  backlog max {scope.backlog_max:>6}"
            err = [k for k, v in scope.ai_error.items() if v]
            line += f"  AI err {','.join(err) if err else '-'}"
            if scope.channels > IDX_DIO4:
                st = measure_period(snap.frames[:, IDX_DIO4], scope.fs_hz)
                if st:
                    line += (f" | DIO4: {st.edges:3d} edges, period {st.period_ms:8.4f} ms "
                             f"(sd {st.period_sd_ms:.4f}, spread {st.period_max_ms - st.period_min_ms:.4f}), "
                             f"high {st.high_ms:.3f} ms")
                else:
                    line += " | DIO4: no edges"
            if scope.channels > IDX_INT_SYNC:
                st2 = measure_period(snap.frames[:, IDX_INT_SYNC], scope.fs_hz)
                if st2:
                    line += f" | IntSync high {st2.high_ms:.3f} ms"
            ai = snap.frames[:, :8].astype(np.float64).mean(axis=0) * AI_VOLTS_PER_COUNT * 1e3
            line += " | AI0-7 mV: " + " ".join(f"{v:+.1f}" for v in ai)
            print(line, flush=True)
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        if ctrl.free_run_active:
            final = ctrl.stop_free_run()
            print(f"Trigger stopped after {final} pulses.")
        scope.stop()
        if scope.last_error:
            print("scope reader error:", scope.last_error)
        ctrl.close()
        print("FPGA safe, session closed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
