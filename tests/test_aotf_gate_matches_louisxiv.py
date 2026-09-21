"""Our AOTF blanking gate against LouisXIV's own exported waveform.

`tests/data/louisxiv_full_waveform_2026-09-08.txt` is the file LouisXIV
writes from the "Full Waveform" save button on its Waveforms tab, taken on
the rig on 2026-09-08 at these live settings:

    X range 100 um / 60 pixels, exposure 0.100 s, cycle time 125.159 ms,
    Fractional Flyback 0.15, X Single Direction, 201 slices,
    AOTF cycle = None, 488 nm on AOTF ch 2.

Its columns are X Galvo, Z Galvo, Z Piezo, Sample Piezo, Tile, Filter
Position, AOTF (V) Ch0..Ch6, Dither | ZP2. This is the ground truth that
settled the "should the AOTF modulate?" question: `AOTF (V) Ch2` is binary
and notches once per slice, so the blanking is real AND unconditional --
it happens with AOTF cycle = None, which is what this rig runs.
"""
import csv
import pathlib

import numpy as np
import pytest

from unmscope.hardware.louisxiv_waveform import aotf_gate, build_louisxiv_waveform
from unmscope.hardware.waveform import AOTF_GATE_SLOT, unpack_words

REFERENCE = pathlib.Path(__file__).parent / "data" / "louisxiv_full_waveform_2026-09-08.txt"

#: The panel settings the reference was exported at.
SETTINGS = dict(exposure_s=0.100, cycle_s=0.125159, x_range_v=100.0, x_offset_v=0.0,
                x_pixels=60, update_rate=1, fractional_smoothing=0.0,
                fractional_flyback=0.15, x_bidirectional=False, n_slices=201,
                z_galvo_start_v=-2.48756, z_galvo_step_v=2 * 2.48756 / 200,
                z_piezo_start_v=-0.625, z_piezo_step_v=2 * 0.625 / 200)

AOTF_CH2_COLUMN = 8

#: The rig runs "None" -- and blanks anyway. That is the whole point.
AOTF_CYCLE = "None"


@pytest.fixture(scope="module")
def louisxiv_gate():
    with REFERENCE.open(newline="", encoding="utf-8-sig") as f:
        rows = csv.reader(f, delimiter="\t")
        header = next(rows)
        data = np.array([[float(x) for x in r] for r in rows if r and r[0].strip()])
    assert header[AOTF_CH2_COLUMN] == "AOTF (V) Ch2", header[AOTF_CH2_COLUMN]
    col = data[:, AOTF_CH2_COLUMN]
    # Binary: the per-point AOTF datum is a gate, not a level.
    assert len(np.unique(col)) == 2, np.unique(col)
    return (col > 0).astype(int)


@pytest.fixture(scope="module")
def ours():
    return build_louisxiv_waveform(**SETTINGS)


def test_the_reference_is_60_high_9_low_once_per_slice(louisxiv_gate):
    """What LouisXIV actually exports, stated plainly so a change in the
    reference file is loud rather than silent."""
    g = louisxiv_gate
    assert len(g) == 13869 == 69 * 201
    runs = []
    cur, n = g[0], 1
    for v in g[1:]:
        if v == cur:
            n += 1
        else:
            runs.append((int(cur), n)); cur, n = v, 1
    runs.append((int(cur), n))
    assert [r for r in runs if r[0] == 1] == [(1, 60)] * 201
    assert [r for r in runs if r[0] == 0] == [(0, 9)] * 201
    assert g.mean() == pytest.approx(60 / 69)


def test_our_line_geometry_matches(ours):
    """60 sweep points + 9 flyback at the rig's own Fractional Flyback."""
    line = ours.line
    assert (line.total_points, line.on_points) == (69, 60)


def test_our_gate_matches_louisxiv_point_for_point(ours, louisxiv_gate):
    gate = aotf_gate(ours.line, AOTF_CYCLE)
    assert np.array_equal(np.tile(gate.astype(int), SETTINGS["n_slices"]), louisxiv_gate)


def test_the_gate_is_streamed_not_just_displayed(ours, louisxiv_gate):
    """It has to be IN the Wvfrm2 words -- as the AO DMA cluster's per-point
    'AOTF on?' bit -- or the card never sees it."""
    streamed = unpack_words(ours.scan.words)
    assert np.array_equal(streamed[AOTF_GATE_SLOT].astype(int), louisxiv_gate)


def test_the_gate_does_not_disturb_the_other_slots(ours):
    """The galvo/piezo slots must be untouched by carrying the gate."""
    streamed = unpack_words(ours.scan.words)
    other = "prefix1" if AOTF_GATE_SLOT == "prefix0" else "prefix0"
    assert (streamed[other] == 0).all()
    for name in ("X Galvo", "Z Galvo", "Z Piezo"):
        assert streamed[name].min() != streamed[name].max(), name


def test_a_bidirectional_line_is_never_blanked():
    """Both sweeps expose, so there is no return move to blank."""
    bidi = build_louisxiv_waveform(**{**SETTINGS, "x_bidirectional": True, "n_slices": 2})
    assert bidi.line.bidirectional
    assert aotf_gate(bidi.line, AOTF_CYCLE).all()
