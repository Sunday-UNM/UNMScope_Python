"""First REAL waveform content, observed on the FPGA Scope. FPGA ONLY,
AO clamp +-328 counts (+-100 mV), nothing connected to the BNCs.

build_scan_waveform(): per trigger an X-galvo linear sweep over the
exposure + a flyback, Z galvo stepping once per slice. 5 slices, bounded
to 5 triggers at 100 ms with 80 ms exposure -> 800 sweep points + 80
flyback points at 100 us. The scope's X Galvo (col 8) and Z Galvo (col 9)
columns must show exactly that.

    python -u spikes/29_scan_waveform_on_scope.py
"""
from __future__ import annotations

import sys
import time

import numpy as np

from unmscope.hardware.fpga_scope import IDX_DIO4, FpgaScope, digital_edges
from unmscope.hardware.fpga_trigger import FpgaTriggerController
from unmscope.hardware.waveform import block_fits_period, build_scan_waveform, counts_to_volts

CLAMP_COUNTS = 328
PERIOD = 0.100
EXPOSURE = 0.080
N_SLICES = 5
X_RANGE_V = 0.18            # +-0.09 V = +-295 counts, inside the clamp
Z_STEP_V = 0.015            # 49 counts per slice


def main() -> int:
    wf = build_scan_waveform(EXPOSURE, X_RANGE_V, n_slices=N_SLICES, z_galvo_start_v=0.0, z_galvo_step_v=Z_STEP_V)
    print(f"waveform: {wf.points_per_trigger} points/trigger x {wf.n_slices} slices = {len(wf.words)} words; "
          f"point period {wf.point_period_s*1e6:.0f} us; block {wf.points_per_trigger*wf.point_period_s*1e3:.1f} ms "
          f"fits {PERIOD*1e3:.0f} ms period: {block_fits_period(wf, PERIOD)}")
    x_sent = counts_to_volts(wf.channels["X Galvo"]).reshape(N_SLICES, -1)
    z_sent = counts_to_volts(wf.channels["Z Galvo"]).reshape(N_SLICES, -1)

    ctrl = FpgaTriggerController()
    ctrl.connect()
    scope = FpgaScope(ctrl, channels=16, period_ticks=400, buffer_seconds=4.0)
    try:
        scope.start()
        ok = ctrl.start_free_run(PERIOD, EXPOSURE, n_triggers=N_SLICES,
                                 ao_points_per_trigger=wf.points_per_trigger,
                                 ao_ticks_between_points=wf.ticks_between_points,
                                 clamp_ao=True, clamp_counts=CLAMP_COUNTS, ao_words=wf.words)
        if not ok:
            print("ARM FAILED:", ctrl.last_error)
            return 1
        time.sleep(N_SLICES * PERIOD + 0.4)
        st = ctrl.last_status
        final = ctrl.stop_free_run()
        time.sleep(0.2)
        snap = scope.snapshot(seconds=3.0)
        print(f"triggers {final}; AO DMA error {st.ao_dma_error}; refill {st.refill_words_written} words; "
              f"AO points generated (monitor) {st.ao_generated}")
    finally:
        try:
            ctrl.stop_free_run()
        except Exception:
            pass
        scope.stop()
        ctrl.set_ao_clamp(True, 0)
        ctrl.close()
        print("FPGA safe (clamp 0), closed.")

    fs = snap.fs_hz
    frames = snap.frames
    rising, _ = digital_edges(frames[:, IDX_DIO4])
    # The AI stream only starts flowing when the trigger is armed, so the
    # first pulse sits at the very start of the capture with no rising
    # edge to detect: treat a high first sample as an edge at 0.
    if frames[0, IDX_DIO4] > 2048 and (len(rising) == 0 or rising[0] > 50):
        rising = np.concatenate(([0], rising))
    print(f"\nDIO4 edges at {rising.tolist()} (samples @ {fs:,.0f} S/s)")
    if len(rising) < N_SLICES:
        print("not enough triggers captured")
        return 1
    ok_all = True
    spp = int(round(wf.point_period_s * fs))                  # scope samples per AO point (10)
    for k in range(N_SLICES):
        e = rising[k]
        # skip the ~60 us trigger->first-point latency, then sample each point's centre
        start = e + 8
        idx = start + np.arange(wf.points_per_trigger) * spp + spp // 2
        idx = idx[idx < len(frames)]
        x_meas = counts_to_volts(frames[idx, 8])
        z_meas = counts_to_volts(frames[idx, 9])
        n = len(idx)
        x_err = np.abs(x_meas - x_sent[k, :n])
        z_err = np.abs(z_meas - z_sent[k, :n])
        # X: sweep from -0.09 to +0.09 V over 800 points, then flyback
        sweep = x_meas[:800]
        mono = np.all(np.diff(sweep) >= -1e-6)
        slice_ok = mono and x_err.max() < 0.002 and z_err.max() < 0.002
        ok_all &= slice_ok
        print(f"  slice {k + 1}: X {sweep[0]:+.4f} -> {sweep[-1]:+.4f} V monotonic={mono}, max|dX|={x_err.max()*1e3:.2f} mV; "
              f"Z = {z_meas.mean():+.4f} V (sent {z_sent[k, 0]:+.4f}), max|dZ|={z_err.max()*1e3:.2f} mV; "
              f"points checked {n} -> {'OK' if slice_ok else 'MISMATCH'}")
    # after the block: does the engine hold the last point until the next trigger?
    e0, e1 = rising[0], rising[1]
    hold = frames[e0 + wf.points_per_trigger * spp + 20: e1 - 5, 8]
    print(f"  between blocks X holds at {counts_to_volts(hold).mean():+.4f} V "
          f"(last flyback point sent {x_sent[0, -1]:+.4f} V), spread {np.ptp(hold)} counts")
    print("\nVERDICT:", "PASS" if ok_all else "FAIL")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
