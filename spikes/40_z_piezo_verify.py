"""Verify the Z Piezo channel (AO2) actually drives the physical output --
the user has now physically connected the piezo hardware and wants the
backend path confirmed live (2026-09-15).

FPGA ONLY -- no camera. X/Z Galvo, Dither stay at 0 throughout (only Z
Piezo is commanded). Uses the REAL streamed path -- start_free_run(ao_words=...)
with points packed by hardware/waveform.py's pack_points() -- because that
is what main_window.py actually drives Z Piezo through during a Z-stack
(zp_start_v/zp_step_v -> build_louisxiv_waveform() -> wf.words -> ao_words;
zp_moves gated by WaveformConfig.z_axes_moving()). An earlier version of
this spike used the 'Static AO to set' / 'Set AO' path instead and every
non-zero value came back as 0 on both the 'AO DMA' register and the AI
scope -- i.e. 'Set AO' does not appear to reach the physical pins on this
bitfile the way the streamed path does (mirrors the AOTF static-vs-stream
split; needs its own investigation if 'Set AO' is ever relied on
elsewhere). This version tests the path that is ACTUALLY used, and only
that path.

Reads the physical pin via the AI scope's 'Z Piezo (AO)' column (index 10,
fpga_scope.py) while free-running with a clamped X/Z Galvo and Dither
(the streamed points still carry them, at 0).

    python -u spikes/40_z_piezo_verify.py
"""
from __future__ import annotations

import sys
import time

import numpy as np

from unmscope.hardware.fpga_scope import FpgaScope
from unmscope.hardware.fpga_trigger import FpgaTriggerController
from unmscope.hardware.waveform import pack_points

Z_PIEZO_AI_COL = 10
COUNTS_PER_VOLT = 3276.7
# A representative sweep spanning the calibrated Z Piezo limits (-2.5..10 V
# per the rig's ini, config/calibration.py) without going near either rail.
TEST_VOLTS = [0.0, 1.0, 2.5, 5.0, 0.0, -1.0, 0.0]
PERIOD_S, EXPOSURE_S = 0.050, 0.020
HOLD_S = 0.35
AO_TICKS_BETWEEN_POINTS = 4000
AO_POINTS_PER_TRIGGER = 4


def volts_to_counts(v: float) -> int:
    return int(round(v * COUNTS_PER_VOLT))


def main() -> int:
    ctrl = FpgaTriggerController()
    ctrl.connect()
    scope = FpgaScope(ctrl, channels=16, period_ticks=400, buffer_seconds=2.0)
    scope.start()
    results = []
    try:
        for v in TEST_VOLTS:
            counts = volts_to_counts(v)
            words = pack_points(x_galvo=np.zeros(AO_POINTS_PER_TRIGGER, dtype=int),
                                z_galvo=0, z_piezo=counts, dither=0)
            scope.clear()
            armed = ctrl.start_free_run(PERIOD_S, EXPOSURE_S, n_triggers=None,
                                        ao_points_per_trigger=AO_POINTS_PER_TRIGGER,
                                        ao_ticks_between_points=AO_TICKS_BETWEEN_POINTS,
                                        ao_words=words)
            if not armed:
                print(f"  commanded {v:+6.2f} V: ARM FAILED: {ctrl.last_error}")
                results.append(False)
                continue
            time.sleep(0.05)
            snap = scope.snapshot(seconds=HOLD_S)
            ctrl.stop_free_run()
            col = snap.frames[:, Z_PIEZO_AI_COL] if snap.frames is not None and len(snap.frames) else np.array([0])
            measured_v = float(np.median(col)) / COUNTS_PER_VOLT
            ok = abs(measured_v - v) < 0.05
            results.append(ok)
            print(f"  commanded {v:+6.2f} V ({counts:+6d} counts) -> "
                  f"scope col10 median {measured_v:+.4f} V (range {col.min()/COUNTS_PER_VOLT:+.4f}"
                  f"..{col.max()/COUNTS_PER_VOLT:+.4f} V)  {'OK' if ok else 'MISMATCH'}")
    finally:
        print("\nrestoring safe state (Z Piezo -> 0 V) ...")
        try:
            if ctrl._free_run_active:
                ctrl.stop_free_run()
            ctrl.safe_state()
        except Exception as e:                                  # noqa: BLE001
            print("  restore failed:", type(e).__name__, e)
        ctrl.close()

    print("\n=== SUMMARY ===")
    if results and all(results):
        print(f"Z Piezo (AO2), streamed path: PASS -- all {len(results)} commanded voltages "
              "measured on the physical pin within 50 mV.")
        return 0
    print(f"Z Piezo (AO2), streamed path: FAIL -- {results.count(False)}/{len(results)} commanded "
          "voltages did not appear on the physical pin as expected.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
