"""Wvfrm2 packing, part 2: what do the two PREFIX slots drive?

spikes/28 established: one AO point = two I64 words; splitting high-slot
first gives the 8-channel point [p0, p1, X Galvo, Z Galvo, Z Piezo,
Dither Galvo, Tiling, Filter]. p0/p1 (bits 63..48 and 47..32 of the even
word) were not visible on the six AO columns. LabVIEW's Setup AO DMA
buffer.vi has "# of prefix columns" and "Add Prefix?" -- per-point flags.
Here the whole 29-column scope stream is captured while p0/p1 cycle
through distinct values, so any digital column (AOTF0-6, Perfusion,
Shutter, Channel Shutter 0-3) or AO column that follows them shows up.

FPGA ONLY; AO clamp +-328 counts (+-100 mV); nothing connected.

    python -u spikes/28b_wvfrm2_prefix_slots.py
"""
from __future__ import annotations

import sys
import time

import numpy as np

from unmscope.hardware.fpga_scope import AI_CHANNEL_NAMES, IDX_DIO4, FpgaScope, digital_edges
from unmscope.hardware.fpga_trigger import FpgaTriggerController, pack_ao_word

CLAMP_COUNTS = 328
POINTS_PER_TRIGGER = 200
TICKS_BETWEEN_POINTS = 4000


def even_word(p0, p1, x, z):
    # slot3 = p0, slot2 = p1, slot1 = X Galvo, slot0 = Z Galvo (spikes/28)
    return pack_ao_word(z, x, p1, p0)


def odd_word(zp, dither, tiling, filt):
    # slot3 = Z Piezo, slot2 = Dither, slot1 = Tiling, slot0 = Filter (spikes/28)
    return pack_ao_word(filt, tiling, dither, zp)


def runs(seq):
    out = []
    for v in seq:
        if out and out[-1][0] == v:
            out[-1][1] += 1
        else:
            out.append([int(v), 1])
    return [(v, n) for v, n in out]


def main() -> int:
    # 8 points, each with a different (p0, p1); X ramps 10..80 so point boundaries are visible.
    prefixes = [(0, 0), (1, 0), (0, 1), (1, 1), (0xFF, 0), (0, 0xFF), (0x7FFF, 0), (0, 0x7FFF)]
    words = []
    for i, (p0, p1) in enumerate(prefixes):
        words.append(even_word(p0, p1, x=10 * (i + 1), z=5))
        words.append(odd_word(zp=0, dither=0, tiling=0, filt=0))
    print(f"{len(prefixes)} points x 2 words, prefixes {prefixes}")

    ctrl = FpgaTriggerController()
    ctrl.connect()
    scope = FpgaScope(ctrl, channels=29, period_ticks=400, buffer_seconds=3.0)
    try:
        scope.start()
        print("clamp:", ctrl.set_ao_clamp(True, CLAMP_COUNTS))
        before = {n: ctrl._session.registers[n].read() for n in ("AOTF ch out (V)", "# AO generated", "AOTF ch # generated")}
        ok = ctrl.start_free_run(0.100, 0.080, n_triggers=3, ao_points_per_trigger=POINTS_PER_TRIGGER,
                                 ao_ticks_between_points=TICKS_BETWEEN_POINTS, clamp_ao=True,
                                 clamp_counts=CLAMP_COUNTS, ao_words=words)
        if not ok:
            print("ARM FAILED:", ctrl.last_error)
            return 1
        time.sleep(0.8)
        regs = ctrl._session.registers
        after = {n: regs[n].read() for n in ("AOTF ch out (V)", "# AO generated", "AOTF ch # generated")}
        final = ctrl.stop_free_run()
        time.sleep(0.2)
        after_stop = {n: regs[n].read() for n in ("# AO generated",)}
        snap = scope.snapshot(seconds=2.0)
        print(f"triggers {final}; registers before {before}; while armed {after}; after stop {after_stop}")
    finally:
        try:
            ctrl.stop_free_run()
        except Exception:
            pass
        scope.stop()
        ctrl.set_ao_clamp(True, 0)
        ctrl.close()
        print("FPGA safe (clamp 0), closed.")

    frames = snap.frames
    fs = snap.fs_hz
    rising, _ = digital_edges(frames[:, IDX_DIO4])
    if len(rising) == 0:
        print("no edges")
        return 1
    edge = rising[0]
    seg = frames[edge - 2: edge + int(0.0095 * fs)]      # the first 8 points (~8 x 100 us) + a bit
    print(f"\nColumns over the first 9.5 ms after trigger #1 (run-length: value x samples; 1 point = 10 samples):")
    for col in range(8, 29):
        rl = runs(seg[:, col])
        if len(rl) == 1 and rl[0][0] == 0:
            continue
        print(f"  col {col:2d} {AI_CHANNEL_NAMES[col]:<28} " + ", ".join(f"{v}x{n}" for v, n in rl[:12]))
    quiet = [f"{c}:{AI_CHANNEL_NAMES[c]}" for c in range(14, 29) if len(runs(seg[:, c])) == 1 and c not in (14, 15)]
    print("  constant-zero digital columns:", quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
