"""Host-timed AOTF digital gate -- verify on real hardware.

FPGA ONLY -- no camera. Galvo/piezo clamped so nothing moves; only the
AOTF channels (0-3, AO5/AO6/AO7/AO3) are allowed to move. Tests
``FpgaTriggerController.start_aotf_digital_gate()`` (hardware/fpga_trigger.py),
built to the user's explicit direction (2026-09-15):

    "I want digital signals coming out of the AOTF channels. Irrespective
    of what the mechanism listed in the bitfile [is]. The signals should
    have a voltage of 3.3 volts in the on state and 0V in the off state.
    It should come up about 25ms before the camera trigger signal ...
    adaptive."

There is no FPGA engine behind this (docs/aotf.md: the AOTF clock loop is
not instantiated in the deployed bitfile) -- the gate is a background
thread on the HOST re-writing 'AOTF ch (V)' on a schedule, timed off
time.perf_counter() with the same _wait_until() primitive start_continuous()
already uses (measured +0.4 ms mean / 0.7 ms spread with this process's
timer resolution raised).

Two cases, because the actual numbers matter for whether host timing can
resolve a real off-edge at all:

  A. Generous timing (period 100 ms, exposure 30 ms -> 70 ms gap). Every
     AOTF channel should show a clean square wave: 0 V/3.3 V, leading each
     DIO4 trigger by ~2.5 ms, staying high through the exposure.
  B. This rig's OWN currently-saved camera settings, read through the same
     VI formulas main_window.py uses (camera_cycle_s + engine_times,
     docs/louisxiv_cycle_time_semantics.md): SyncReadout, full 2048x2048
     ROI, ~9.998 ms exposure -> ~10.155 ms period -> a ~150 us gap. This is
     narrower than the measured host-timing precision, so the gate is
     expected to degrade to effectively DC high -- this case exists to
     honestly confirm that degradation on the real hardware rather than
     assume it.

    python -u spikes/39_aotf_digital_gate.py
"""
from __future__ import annotations

import sys
import time

import numpy as np

from unmscope.config.waveform_config import camera_cycle_s, engine_times
from unmscope.hardware.fpga_scope import AI_CHANNEL_NAMES, FpgaScope, digital_edges
from unmscope.hardware.fpga_trigger import FpgaTriggerController, volts_to_aotf_counts

CLAMP = 328                 # +-100 mV on the galvo/piezo channels (content is 0 anyway)
ON_VOLTS = 3.3
DIO4_COL = 15
AOTF_COLS = {0: 16, 1: 17, 2: 19, 3: 20}


def analyze(snap, ch, period_s, exposure_s, lead_used_s, label):
    frames = snap.frames
    fs_hz = snap.fs_hz
    if frames is None or not len(frames):
        print(f"    ch{ch}: no frames captured")
        return
    dio4 = frames[:, DIO4_COL]
    aotf = frames[:, AOTF_COLS[ch]]
    on_counts = volts_to_aotf_counts(ON_VOLTS)
    dio4_rises, _ = digital_edges(dio4)
    aotf_rises, aotf_falls = digital_edges(aotf, threshold=on_counts / 2)
    lo, hi = int(aotf.min()), int(aotf.max())
    duty = float(np.mean(aotf > on_counts / 2))
    print(f"    ch{ch}: range {lo}..{hi} counts ({lo / 3276.7:.3f}..{hi / 3276.7:.3f} V), "
          f"{len(aotf_rises)} rise(s)/{len(aotf_falls)} fall(s), duty high {duty * 100:.1f} %")
    if len(dio4_rises) and len(aotf_rises):
        leads = []
        for d in dio4_rises:
            prior = aotf_rises[aotf_rises <= d]
            if len(prior):
                leads.append((d - prior[-1]) / fs_hz)
        if leads:
            print(f"          lead AOTF-on -> DIO4-on: mean {np.mean(leads) * 1e3:.3f} ms "
                  f"(requested {lead_used_s * 1e3:.3f} ms), n={len(leads)}")
    if len(aotf_rises) and len(aotf_falls):
        ons = []
        for r in aotf_rises:
            after = aotf_falls[aotf_falls > r]
            if len(after):
                ons.append((after[0] - r) / fs_hz)
        if ons:
            print(f"          on-duration: mean {np.mean(ons) * 1e3:.3f} ms "
                  f"(expected {(lead_used_s + exposure_s) * 1e3:.3f} ms), n={len(ons)}")
    if len(aotf_rises) >= 2:
        periods = np.diff(aotf_rises) / fs_hz
        print(f"          rise-to-rise period: mean {np.mean(periods) * 1e3:.3f} ms "
              f"(expected {period_s * 1e3:.3f} ms), spread {np.std(periods) * 1e3:.3f} ms, n={len(periods)}")
    if duty > 0.98:
        print(f"          -> effectively DC high for this gap (as expected when gap is too tight)")


def run_case(ctrl, scope, label, period_s, exposure_s, poll_s):
    gap_ms = (period_s - exposure_s) * 1e3
    print(f"\n=== {label} ===")
    print(f"    period {period_s * 1e3:.4f} ms, exposure {exposure_s * 1e3:.4f} ms, gap {gap_ms:.4f} ms")
    armed = ctrl.start_free_run(period_s, exposure_s, n_triggers=None,
                                clamp_ao=True, clamp_counts=CLAMP)
    if not armed:
        print(f"    ARM FAILED: {ctrl.last_error}")
        return
    lead_used = ctrl.start_aotf_digital_gate(period_s, exposure_s, channels=(0, 1, 2, 3),
                                             on_volts=ON_VOLTS)
    print(f"    gate started: lead used {lead_used * 1e3:.4f} ms "
          f"(gap available {ctrl.aotf_gate_gap_s * 1e3:.4f} ms)")
    scope.clear()
    time.sleep(poll_s + 0.1)          # let poll_s of REAL data accumulate before asking for it
    snap = scope.snapshot(seconds=poll_s)
    ctrl.stop_aotf_digital_gate()
    ctrl.stop_free_run()
    for ch in (0, 1, 2, 3):
        analyze(snap, ch, period_s, exposure_s, lead_used, label)


def main() -> int:
    ctrl = FpgaTriggerController()
    ctrl.connect()
    scope = FpgaScope(ctrl, channels=24, period_ticks=400, buffer_seconds=4.0)
    scope.start()
    try:
        run_case(ctrl, scope, "A: generous timing (100 ms / 30 ms)",
                 period_s=0.100, exposure_s=0.030, poll_s=1.0)

        exposure_s = 0.009998
        vsize = 2048
        cam_cycle = camera_cycle_s(exposure_s, vsize, sync_readout=True)
        _, cycle_s = engine_times(exposure_s, cam_cycle, typed_cycle_s=0.0, custom_cycle_time=False)
        period_s = max(cycle_s, cam_cycle)
        run_case(ctrl, scope, "B: this rig's actual saved settings (SyncReadout, full ROI)",
                 period_s=period_s, exposure_s=exposure_s, poll_s=0.5)
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
