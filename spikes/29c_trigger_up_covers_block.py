"""Hypothesis (spikes/29b): after the LAST trigger of a bounded run the AO
block is aborted when the Int-Sync pulse drops, i.e. after 'Trigger up
(ticks)' (10 ms -> 101 points). If so, a Trigger up that covers the whole
block (88 ms + 1 ms here) should let the last block complete.

FPGA only, AO clamp +-328 counts, nothing connected. Also compares every
block to the sent waveform with change-aligned sampling (the fixed grid
in spikes/29 slipped a point in the steep flyback).

    python -u spikes/29c_trigger_up_covers_block.py
"""
from __future__ import annotations

import sys
import time

import numpy as np

from unmscope.hardware.fpga_scope import IDX_DIO4, IDX_INT_SYNC, FpgaScope, digital_edges, measure_period
from unmscope.hardware.fpga_trigger import FpgaTriggerController, TICKS_PER_S
from unmscope.hardware.waveform import build_scan_waveform, counts_to_volts

CLAMP = 328
PERIOD, EXPOSURE = 0.100, 0.080
N = 5


def edges_with_start(frames):
    rising, _ = digital_edges(frames[:, IDX_DIO4])
    if frames[0, IDX_DIO4] > 2048 and (len(rising) == 0 or rising[0] > 50):
        rising = np.concatenate(([0], rising))
    return rising


def compare_block(frames, fs, edge, sent_x, sent_z, spp):
    """Change-aligned: first X change after the edge = start of point 0."""
    seg = frames[edge: edge + int(PERIOD * fs) - 20]
    x = seg[:, 8]
    ch = np.flatnonzero(np.diff(x) != 0)
    if len(ch) == 0:
        return None
    start = ch[0] + 1                      # first sample of point 0's... actually of point 1 (point 0 = hold value may equal)
    # point 0 begins spp samples before the first change if point0 != hold; be robust: fit start so that
    # the sampled staircase matches best over the first 20 points
    best = None
    for s0 in range(max(0, start - spp - 2), start + 3):
        idx = s0 + np.arange(len(sent_x)) * spp + spp // 2
        idx = idx[idx < len(seg)]
        err = np.abs(seg[idx, 8] - sent_x[: len(idx)])
        score = err[:200].mean()
        if best is None or score < best[0]:
            best = (score, s0, idx)
    _, s0, idx = best
    xm = seg[idx, 8]
    zm = seg[idx, 9]
    n = len(idx)
    changes_total = ch[-1] + 1
    return {
        "points_out": int(round(changes_total / spp)) + 1,
        "n_checked": n,
        "x_err_counts": int(np.abs(xm - sent_x[:n]).max()),
        "z_err_counts": int(np.abs(zm - sent_z[:n]).max()),
        "x_first_v": counts_to_volts(xm[0]), "x_last_sweep_v": counts_to_volts(xm[min(799, n - 1)]),
        "z_v": counts_to_volts(zm.mean()),
    }


def main() -> int:
    wf = build_scan_waveform(EXPOSURE, 0.18, n_slices=N, z_galvo_step_v=0.015)
    block_ticks = wf.points_per_trigger * wf.ticks_between_points
    up_ticks = block_ticks + 40_000      # block + 1 ms
    print(f"waveform {wf.points_per_trigger} points/trigger ({block_ticks / TICKS_PER_S * 1e3:.1f} ms), "
          f"Trigger up = {up_ticks} ticks ({up_ticks / TICKS_PER_S * 1e3:.1f} ms)")
    sent_x = wf.channels["X Galvo"].reshape(N, -1)
    sent_z = wf.channels["Z Galvo"].reshape(N, -1)

    ctrl = FpgaTriggerController()
    ctrl.connect()
    scope = FpgaScope(ctrl, channels=16, period_ticks=400, buffer_seconds=4.0)
    try:
        scope.start()
        ctrl.set_ao_clamp(True, CLAMP)
        ok = ctrl.start_free_run(PERIOD, EXPOSURE, n_triggers=N, ao_points_per_trigger=wf.points_per_trigger,
                                 ao_ticks_between_points=wf.ticks_between_points, clamp_ao=True,
                                 clamp_counts=CLAMP, ao_words=wf.words, trigger_up_ticks=up_ticks)
        assert ok, ctrl.last_error
        print(f"armed: Cycle {ctrl.cycle_ticks}, Trigger up {ctrl.trigger_up_ticks} ticks")
        time.sleep(N * PERIOD + 0.4)
        regs = ctrl._session.registers
        gen, left, state = regs["# AO generated"].read(), regs["AO # points left"].read(), regs["AO Waveform State"].read()
        final = ctrl.stop_free_run()
        time.sleep(0.3)
        snap = scope.snapshot(seconds=4.0)
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
    edges = edges_with_start(frames)
    spp = int(round(wf.point_period_s * fs))
    print(f"triggers {final}; # AO generated {gen} (+98 = {gen + 98}, expect {N * wf.points_per_trigger}); "
          f"points left {left}; state {state}")
    st = measure_period(frames[:, IDX_INT_SYNC], fs)
    print(f"Int Sync: {st.edges} edges, high {st.high_ms:.2f} ms (expect {up_ticks / TICKS_PER_S * 1e3:.1f})" if st else "Int Sync: no edges")
    print(f"DIO4 edges: {edges.tolist()}")
    ok_all = len(edges) >= N
    for k in range(min(N, len(edges))):
        r = compare_block(frames, fs, edges[k], sent_x[k], sent_z[k], spp)
        if r is None:
            print(f"  slice {k + 1}: no AO activity")
            ok_all = False
            continue
        good = r["points_out"] >= wf.points_per_trigger - 1 and r["x_err_counts"] <= 1 and r["z_err_counts"] == 0
        ok_all &= good
        print(f"  slice {k + 1}: points out {r['points_out']} (expect {wf.points_per_trigger}); "
              f"X {r['x_first_v']:+.4f} -> {r['x_last_sweep_v']:+.4f} V, max|dX| {r['x_err_counts']} counts; "
              f"Z {r['z_v']:+.4f} V (sent {counts_to_volts(sent_z[k, 0]):+.4f}), max|dZ| {r['z_err_counts']} -> {'OK' if good else 'MISMATCH'}")
    print("\nVERDICT:", "PASS" if ok_all else "FAIL")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
