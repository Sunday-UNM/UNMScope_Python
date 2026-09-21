"""End-to-end confirmation: the AOTF digital gate and Z Piezo stepping
running TOGETHER, through the actual production code path (main_window.py's
own build_louisxiv_waveform -> start_free_run(ao_words=...) plus
start_aotf_digital_gate()) -- not the two isolated per-feature spikes
(39, 40), which could each pass while still interfering with each other in
a real Z-stack. FPGA ONLY -- no camera.

3 slices, Z Piezo stepping 0 -> 1 -> 2 V, generous timing (100 ms period /
30 ms exposure) so the AOTF gate's on/off is unambiguous on the scope.
Confirms: (a) 'Z Piezo (AO)' (col 10) shows three distinct held levels in
step with the trigger count, (b) AOTF ch 0-3 keep gating cleanly the whole
time, unaffected by the piezo motion sharing the same Wvfrm2 stream.

    python -u spikes/41_aotf_and_zpiezo_combined.py
"""
from __future__ import annotations

import sys
import time

import numpy as np

from unmscope.hardware.fpga_scope import FpgaScope, digital_edges
from unmscope.hardware.fpga_trigger import FpgaTriggerController, volts_to_aotf_counts
from unmscope.hardware.louisxiv_waveform import build_louisxiv_waveform

Z_PIEZO_COL = 10
AOTF_COLS = {0: 16, 1: 17, 2: 19, 3: 20}
COUNTS_PER_VOLT = 3276.7
PERIOD_S, EXPOSURE_S = 0.100, 0.030
N_SLICES = 3
Z_STEP_V = 1.0
POLL_S = N_SLICES * PERIOD_S * 3 + 0.5   # a few triggers per slice


def main() -> int:
    ctrl = FpgaTriggerController()
    ctrl.connect()
    scope = FpgaScope(ctrl, channels=24, period_ticks=400, buffer_seconds=6.0)
    scope.start()
    try:
        lx = build_louisxiv_waveform(
            exposure_s=EXPOSURE_S, cycle_s=PERIOD_S, x_range_v=0.0, x_offset_v=0.0,
            x_pixels=20, n_slices=N_SLICES,
            z_piezo_start_v=0.0, z_piezo_step_v=Z_STEP_V,
            aotf_cycle="None",
        )
        wf = lx.scan
        n_triggers = N_SLICES * 3  # 3 triggers held per slice, roughly
        armed = ctrl.start_free_run(
            PERIOD_S, EXPOSURE_S, n_triggers=None,
            ao_points_per_trigger=wf.points_per_trigger,
            ao_ticks_between_points=wf.ticks_between_points, ao_words=wf.words,
        )
        if not armed:
            print(f"ARM FAILED: {ctrl.last_error}")
            return 1
        lead_used = ctrl.start_aotf_digital_gate(PERIOD_S, EXPOSURE_S, channels=(0, 1, 2, 3), on_volts=3.3)
        print(f"armed + gate started (lead {lead_used * 1e3:.2f} ms); "
              f"{N_SLICES} slices, points/trigger={wf.points_per_trigger}, "
              f"ticks_between_points={wf.ticks_between_points}")
        time.sleep(POLL_S)
        snap = scope.snapshot(seconds=POLL_S)
        ctrl.stop_aotf_digital_gate()
        ctrl.stop_free_run()

        frames = snap.frames
        fs_hz = snap.fs_hz
        zp = frames[:, Z_PIEZO_COL].astype(np.float64) / COUNTS_PER_VOLT
        # Z Piezo: report the distinct plateau levels seen (rounded to 0.1 V)
        levels = sorted(set(np.round(zp, 1)))
        print(f"\nZ Piezo (AO) levels seen: {levels} V (expected roughly "
              f"{[round(k * Z_STEP_V, 1) for k in range(N_SLICES)]} V)")
        ok_piezo = len(levels) >= N_SLICES

        on_counts = volts_to_aotf_counts(3.3)
        print("\nAOTF gate during the same run:")
        ok_aotf = True
        for ch, col in AOTF_COLS.items():
            trace = frames[:, col]
            rises, falls = digital_edges(trace, threshold=on_counts / 2)
            lo, hi = int(trace.min()), int(trace.max())
            duty = float(np.mean(trace > on_counts / 2))
            print(f"  ch{ch}: range {lo}..{hi} counts, {len(rises)} rise(s)/{len(falls)} fall(s), "
                  f"duty {duty * 100:.1f} %")
            if len(rises) < 3 or hi < on_counts * 0.9:
                ok_aotf = False

        print(f"\n=== SUMMARY === Z Piezo stepping: {'PASS' if ok_piezo else 'FAIL'}; "
              f"AOTF gate concurrent: {'PASS' if ok_aotf else 'FAIL'}")
        return 0 if (ok_piezo and ok_aotf) else 1
    finally:
        print("\nrestoring safe state ...")
        try:
            ctrl.stop_aotf_digital_gate()
            if ctrl._free_run_active:
                ctrl.stop_free_run()
            ctrl.safe_state()
        except Exception as e:                                  # noqa: BLE001
            print("  restore failed:", type(e).__name__, e)
        ctrl.close()


if __name__ == "__main__":
    sys.exit(main())
