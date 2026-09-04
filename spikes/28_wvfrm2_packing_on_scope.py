"""Reverse-engineer the Wvfrm2 word packing with the FPGA Scope.

FPGA ONLY, nothing connected to the AO BNCs. The AO limits are set to
+-328 counts (+-100 mV) for this test so the FPGA passes the small test
values through to the AO columns the scope samples (they are sampled
AFTER the range check; at the usual 0-count clamp they would read 0).

METHOD: stream a repeating pattern of four I64 words, each packing four
distinct small I16 values (11,22,33,44 / 55,66,77,88 / 111.. / 155..),
free-run 5 triggers at 100 ms with 'AO # of points per trigger' = 200 and
'AO ticks between points' = 4000 (100 us), and read the scope's AO columns
(8..13) around each DIO4 edge. Whatever value sequence a column shows
tells which word slot feeds which AO channel and how many words the
engine consumes per point (10 scope samples at 100 kS/s per 100 us point).

    python -u spikes/28_wvfrm2_packing_on_scope.py
"""
from __future__ import annotations

import sys
import time

import numpy as np

from unmscope.hardware.fpga_scope import AI_CHANNEL_NAMES, IDX_DIO4, FpgaScope, digital_edges
from unmscope.hardware.fpga_trigger import FpgaTriggerController, pack_ao_word

PATTERN_FIELDS = [(11, 22, 33, 44), (55, 66, 77, 88), (111, 122, 133, 144), (155, 166, 177, 188)]
CLAMP_COUNTS = 328          # +-100 mV
POINTS_PER_TRIGGER = 200    # 20 ms of points per 100 ms cycle
TICKS_BETWEEN_POINTS = 4000  # 100 us -> 10 scope samples per point at 100 kS/s
N_TRIGGERS = 5


def runs(seq):
    """Run-length encode a 1-D sequence -> list of (value, length)."""
    out = []
    for v in seq:
        if out and out[-1][0] == v:
            out[-1][1] += 1
        else:
            out.append([int(v), 1])
    return [(v, n) for v, n in out]


def main() -> int:
    words = [pack_ao_word(*f) for f in PATTERN_FIELDS]
    print("pattern words:", [hex(w & 0xFFFFFFFFFFFFFFFF) for w in words])
    lookup = {}
    for wi, f in enumerate(PATTERN_FIELDS):
        for slot, v in enumerate(f):
            lookup[v] = f"word{wi}.slot{slot}"

    ctrl = FpgaTriggerController()
    ctrl.connect()
    scope = FpgaScope(ctrl, channels=16, period_ticks=400, buffer_seconds=4.0)
    try:
        scope.start()
        print(f"AO clamp +-{CLAMP_COUNTS} counts:", ctrl.set_ao_clamp(True, CLAMP_COUNTS), ctrl._last_ao_limits[0])
        ok = ctrl.start_free_run(0.100, 0.080, n_triggers=N_TRIGGERS,
                                 ao_points_per_trigger=POINTS_PER_TRIGGER,
                                 ao_ticks_between_points=TICKS_BETWEEN_POINTS,
                                 clamp_ao=True, clamp_counts=CLAMP_COUNTS, ao_words=words)
        if not ok:
            print("ARM FAILED:", ctrl.last_error)
            return 1
        time.sleep(N_TRIGGERS * 0.1 + 0.5)
        st = ctrl.last_status
        final = ctrl.stop_free_run()
        time.sleep(0.2)
        snap = scope.snapshot(seconds=3.0)
        print(f"triggers {final}, AO points generated {st.ao_generated if st else '?'}, "
              f"refill words {st.refill_words_written if st else '?'}, AO DMA error {st.ao_dma_error if st else '?'}")
    finally:
        try:
            ctrl.stop_free_run()
        except Exception:
            pass
        scope.stop()
        ctrl.set_ao_clamp(True, 0)          # back to frozen
        ctrl.close()
        print("FPGA safe (AO clamp back to 0), session closed.")

    frames = snap.frames
    fs = snap.fs_hz
    rising, _ = digital_edges(frames[:, IDX_DIO4])
    print(f"\n{len(frames)} frames at {fs:,.0f} S/s; DIO4 rising edges at samples {rising.tolist()}")
    if len(rising) == 0:
        print("no trigger edges captured")
        return 1
    win = int(0.030 * fs)      # 30 ms after each edge covers the 20 ms of points + a bit
    for k, edge in enumerate(rising[:2]):
        print(f"\n--- trigger #{k + 1}: AO columns for {win / fs * 1e3:.0f} ms after the edge "
              f"(run-length coded: value x samples; 1 point = {TICKS_BETWEEN_POINTS / 40e6 * fs:.0f} samples) ---")
        seg = frames[edge - 5: edge + win]
        for col in range(8, 14):
            rl = runs(seg[:, col])
            shown = ", ".join(f"{v}x{n}" for v, n in rl[:14]) + (" ..." if len(rl) > 14 else "")
            vals = sorted({v for v, _ in rl if v != 0})
            src = ", ".join(f"{v}={lookup.get(v, '?')}" for v in vals[:8])
            print(f"  col {col:2d} {AI_CHANNEL_NAMES[col]:<20} {shown}")
            if src:
                print(f"         values seen: {src}")
    # summary: for each AO column, the set of pattern slots it showed
    print("\nSUMMARY (which pattern slot each AO column carries):")
    edge = rising[0]
    seg = frames[edge: edge + win]
    for col in range(8, 14):
        vals = sorted({int(v) for v in np.unique(seg[:, col]) if v != 0})
        slots = sorted({lookup[v].split('.')[1] for v in vals if v in lookup})
        wordsn = sorted({lookup[v].split('.')[0] for v in vals if v in lookup})
        print(f"  col {col} {AI_CHANNEL_NAMES[col]:<20} slots {slots} words {wordsn} values {vals}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
