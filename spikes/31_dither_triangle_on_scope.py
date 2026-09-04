"""The dither galvo's triangle, observed on the FPGA Scope. FPGA ONLY,
AO test clamp +-1638 counts (+-500 mV), nothing connected to the BNCs.

The Dither box's three fields (Range um, # Sweeps, Fract. Flyback) become
a triangle on AO4 / scope column 11. This streams 3 blocks with a 5.5-sweep
triangle and checks, from the scope alone: the peak-to-peak amplitude, the
number of triangle peaks per block, and that the X sweep on column 8 is
unaffected.

    python -u spikes/31_dither_triangle_on_scope.py
"""
from __future__ import annotations

import sys
import time

import numpy as np

from unmscope.hardware.fpga_scope import IDX_DIO4, FpgaScope, digital_edges
from unmscope.hardware.fpga_trigger import FpgaTriggerController
from unmscope.hardware.waveform import build_scan_waveform, counts_to_volts

CLAMP = 1638            # +-500 mV
PERIOD, EXPOSURE = 0.100, 0.080
N = 3
SWEEPS = 5.5
DITHER_RANGE_V = 0.4    # 4 um at 10 um/V
X_RANGE_V = 0.05


def edges_with_start(frames):
    rising, _ = digital_edges(frames[:, IDX_DIO4])
    if frames[0, IDX_DIO4] > 2048 and (len(rising) == 0 or rising[0] > 50):
        rising = np.concatenate(([0], rising))
    return rising


def main() -> int:
    wf = build_scan_waveform(EXPOSURE, X_RANGE_V, n_slices=N, period_s=PERIOD,
                             dither_range_v=DITHER_RANGE_V, dither_pulses=SWEEPS,
                             dither_flyback_fraction=0.10)
    d_sent = counts_to_volts(wf.channels["Dither Galvo"][: wf.points_per_trigger])
    print(f"{wf.points_per_trigger} points/trigger x {N} slices; dither {DITHER_RANGE_V * 1e3:.0f} mV pk-pk "
          f"x {SWEEPS} sweeps, sent min/max {d_sent.min() * 1e3:+.1f}/{d_sent.max() * 1e3:+.1f} mV")

    ctrl = FpgaTriggerController()
    ctrl.connect()
    scope = FpgaScope(ctrl, channels=16, period_ticks=400, buffer_seconds=4.0)
    try:
        scope.start()
        ok = ctrl.start_free_run(PERIOD, EXPOSURE, n_triggers=N, ao_points_per_trigger=wf.points_per_trigger,
                                 ao_ticks_between_points=wf.ticks_between_points, clamp_ao=True,
                                 clamp_counts=CLAMP, ao_words=wf.words,
                                 trigger_up_ticks=wf.points_per_trigger * wf.ticks_between_points + 40_000)
        assert ok, ctrl.last_error
        time.sleep(N * PERIOD + 0.4)
        final = ctrl.stop_free_run()
        time.sleep(0.2)
        snap = scope.snapshot(seconds=3.0)
    finally:
        try:
            ctrl.stop_free_run()
        except Exception:
            pass
        scope.stop()
        ctrl.set_ao_clamp(True, 0)
        ctrl.close()
        print("FPGA safe (clamp 0), closed.")

    frames, fs = snap.frames, snap.fs_hz
    edges = edges_with_start(frames)
    print(f"triggers {final}; DIO4 edges {edges.tolist()}")
    ok_all = len(edges) >= N
    win = int(wf.points_per_trigger * wf.point_period_s * fs)
    for k in range(min(N, len(edges))):
        seg = frames[edges[k] + 10: edges[k] + win]
        d = counts_to_volts(seg[:, 11])
        x = counts_to_volts(seg[:, 8])
        # count triangle peaks (coarse: local maxima of a smoothed trace)
        s = np.convolve(d, np.ones(21) / 21, mode="same")[20:-20]
        peaks = np.flatnonzero((s[1:-1] > s[:-2]) & (s[1:-1] >= s[2:])) + 1
        peaks = peaks[np.concatenate(([True], np.diff(peaks) > 50))]
        pk_pk = d.max() - d.min()
        good = (abs(pk_pk - DITHER_RANGE_V) < 0.02 and len(peaks) in (5, 6)
                and abs((x.max() - x.min()) - X_RANGE_V) < 0.005)
        ok_all &= good
        print(f"  slice {k + 1}: dither {d.min() * 1e3:+.1f}..{d.max() * 1e3:+.1f} mV "
              f"(pk-pk {pk_pk * 1e3:.1f}, expect {DITHER_RANGE_V * 1e3:.0f}), {len(peaks)} peaks (expect 5-6 for "
              f"{SWEEPS} sweeps); X pk-pk {(x.max() - x.min()) * 1e3:.1f} mV -> {'OK' if good else 'MISMATCH'}")
    print("\nVERDICT:", "PASS" if ok_all else "FAIL")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
