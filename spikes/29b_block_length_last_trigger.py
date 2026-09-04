"""Does the LAST trigger of a bounded run cut its AO block short?

spikes/29: 5 slices x 880 points, bounded to 5 triggers -> '# AO
generated' + 98 = 3720 = 4 full blocks + 200 points, and the scope showed
the 5th X sweep stopping ~20 ms in. Here each block's real length is
measured on the scope (samples from the DIO4 edge until X stops changing)
for: bounded 5, bounded 6, and continuous for 0.7 s. FPGA only, AO clamp
+-328 counts, nothing connected.

    python -u spikes/29b_block_length_last_trigger.py
"""
from __future__ import annotations

import sys
import time

import numpy as np

from unmscope.hardware.fpga_scope import IDX_DIO4, FpgaScope, digital_edges
from unmscope.hardware.fpga_trigger import FpgaTriggerController
from unmscope.hardware.waveform import build_scan_waveform

CLAMP = 328
PERIOD, EXPOSURE = 0.100, 0.080


def edges_with_start(frames):
    rising, _ = digital_edges(frames[:, IDX_DIO4])
    if frames[0, IDX_DIO4] > 2048 and (len(rising) == 0 or rising[0] > 50):
        rising = np.concatenate(([0], rising))
    return rising


def block_lengths(frames, fs, edges, points_per_trigger):
    """Per trigger: samples until the X column stops changing (block end)."""
    out = []
    win = int(PERIOD * fs) - 20
    for e in edges:
        seg = frames[e: e + win, 8]
        changes = np.flatnonzero(np.diff(seg) != 0)
        if len(changes) == 0:
            out.append(0)
            continue
        last = changes[-1] + 1
        out.append(int(last))
    return out


def run(ctrl, scope, wf, label, n_triggers, hold_s):
    ok = ctrl.start_free_run(PERIOD, EXPOSURE, n_triggers=n_triggers,
                             ao_points_per_trigger=wf.points_per_trigger,
                             ao_ticks_between_points=wf.ticks_between_points,
                             clamp_ao=True, clamp_counts=CLAMP, ao_words=wf.words)
    assert ok, ctrl.last_error
    time.sleep(hold_s)
    regs = ctrl._session.registers
    gen = regs["# AO generated"].read()
    state = regs["AO Waveform State"].read()
    left = regs["AO # points left"].read()
    final = ctrl.stop_free_run()
    time.sleep(0.3)
    snap = scope.snapshot(seconds=4.0)
    edges = edges_with_start(snap.frames)
    lens = block_lengths(snap.frames, snap.fs_hz, edges, wf.points_per_trigger)
    spp = wf.point_period_s * snap.fs_hz
    pts = [round(l / spp) for l in lens]
    print(f"  [{label:<22}] triggers {final}; before stop: # AO generated {gen} (+98 = {gen + 98}), "
          f"state {state}, points left {left}")
    print(f"     block lengths (points, expect {wf.points_per_trigger}): {pts}")
    scope.clear()
    return pts


def main() -> int:
    wf = build_scan_waveform(EXPOSURE, 0.18, n_slices=5, z_galvo_step_v=0.015)
    print(f"waveform {wf.points_per_trigger} points/trigger x {wf.n_slices} slices")
    ctrl = FpgaTriggerController()
    ctrl.connect()
    scope = FpgaScope(ctrl, channels=16, period_ticks=400, buffer_seconds=4.0)
    try:
        scope.start()
        ctrl.set_ao_clamp(True, CLAMP)
        run(ctrl, scope, wf, "bounded 5", 5, 5 * PERIOD + 0.4)
        run(ctrl, scope, wf, "bounded 6", 6, 6 * PERIOD + 0.4)
        run(ctrl, scope, wf, "bounded 2", 2, 2 * PERIOD + 0.4)
        run(ctrl, scope, wf, "continuous 0.75 s", None, 0.75)
    finally:
        try:
            ctrl.stop_free_run()
        except Exception:
            pass
        scope.stop()
        ctrl.set_ao_clamp(True, 0)
        ctrl.close()
        print("FPGA safe (clamp 0), closed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
