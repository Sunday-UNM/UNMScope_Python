"""Wvfrm2 packing / waveform construction (no hardware). Layout per
spikes/28: 2 words per point, high slot first: [p0, p1, X, Z | ZP, Dither, Tiling, Filter]."""
import numpy as np
import pytest

from unmscope.hardware.fpga_trigger import pack_ao_word
from unmscope.hardware.waveform import (
    COUNTS_PER_VOLT, ScanWaveform, block_fits_period, build_scan_waveform, counts_to_volts,
    pack_points, unpack_words, volts_to_counts,
)


def test_pack_matches_the_measured_slot_assignment():
    # spikes/28: even word slot1 -> X Galvo, slot0 -> Z Galvo; odd word slot3 -> Z Piezo,
    # slot2 -> Dither, slot1 -> Tiling, slot0 -> Filter; prefixes in the even word's high slots.
    words = pack_points(x_galvo=[22], z_galvo=[11], z_piezo=[88], dither=[77], tiling=[66], filt=[55],
                        prefix0=[44], prefix1=[33])
    assert words == [pack_ao_word(11, 22, 33, 44), pack_ao_word(55, 66, 77, 88)]


def test_unpack_roundtrip_with_negative_values():
    x = np.array([-32767, -1, 0, 1, 32767])
    words = pack_points(x_galvo=x, z_galvo=-x, z_piezo=7, dither=-7, tiling=3, filt=-3, prefix0=1, prefix1=-2)
    assert len(words) == 2 * len(x)
    ch = unpack_words(words)
    np.testing.assert_array_equal(ch["X Galvo"], x)
    np.testing.assert_array_equal(ch["Z Galvo"], -x)
    assert set(ch["Z Piezo"]) == {7} and set(ch["Dither Galvo"]) == {-7}
    assert set(ch["Tiling"]) == {3} and set(ch["Filter"]) == {-3}
    assert set(ch["prefix0"]) == {1} and set(ch["prefix1"]) == {-2}


def test_volt_conversion():
    assert volts_to_counts(10.0) == 32767
    assert volts_to_counts(-10.0) == -32767
    assert volts_to_counts(11.0) == 32767            # clipped
    assert counts_to_volts(COUNTS_PER_VOLT) == pytest.approx(1.0)


def test_scan_waveform_shape_and_steps():
    wf = build_scan_waveform(exposure_s=0.010, x_range_v=1.0, n_slices=3, z_galvo_start_v=0.1, z_galvo_step_v=0.05,
                             ticks_between_points=4000)
    assert wf.point_period_s == pytest.approx(100e-6)
    assert wf.points_per_trigger == 100 + 10          # 10 ms sweep at 100 us + 10% flyback
    assert wf.n_slices == 3
    assert len(wf.words) == 2 * 3 * wf.points_per_trigger
    ch = unpack_words(wf.words)
    x = counts_to_volts(ch["X Galvo"]).reshape(3, -1)
    assert x[0, 0] == pytest.approx(-0.5, abs=1e-3) and x[0, 99] == pytest.approx(0.5, abs=1e-3)
    assert np.all(np.diff(x[0, :100]) > 0)           # monotonic sweep
    zg = counts_to_volts(ch["Z Galvo"]).reshape(3, -1)
    assert np.allclose(zg[:, 0], [0.1, 0.15, 0.2], atol=1e-3)
    assert np.all(zg == zg[:, :1])                    # constant within a slice


def test_block_fits_period():
    wf = build_scan_waveform(exposure_s=0.080, x_range_v=1.0, ticks_between_points=4000)   # 88 ms of points
    assert block_fits_period(wf, period_s=0.100)
    assert not block_fits_period(wf, period_s=0.088)


def test_block_shortened_to_fit_period_in_syncreadout():
    # SYNCREADOUT: period == exposure (100 ms). Sweep + flyback must still fit.
    wf = build_scan_waveform(exposure_s=0.100, x_range_v=1.0, period_s=0.100, ticks_between_points=4000)
    assert block_fits_period(wf, period_s=0.100, margin_s=0.0)
    assert wf.points_per_trigger <= 980                    # 100 ms - 2 ms margin at 100 us
    assert wf.points_per_trigger >= 900
    x = counts_to_volts(unpack_words(wf.words)["X Galvo"])
    assert x[0] == pytest.approx(-0.5, abs=1e-3)           # sweep still spans the full range
    assert x.max() == pytest.approx(0.5, abs=1e-3)
