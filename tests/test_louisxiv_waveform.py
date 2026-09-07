"""LouisXIV's Linear waveform, VI by VI: the cubic ramp, the line, the
S-curve, the rate rules, the max-rate check, the DMA tick rounding."""
import math

import numpy as np
import pytest

from unmscope.hardware.fpga_trigger import TICKS_PER_S
from unmscope.hardware.louisxiv_waveform import (
    MAX_AO_RATE_KHZ, add_hold_counts, ao_rate_for_line, build_louisxiv_waveform, check_ao_rate,
    cubic_portion, cubic_ramp_coeffs, dma_ticks, extra_counts_for_delays, fast_axis_line,
    min_rate_for_flyback, s_curve, s_curve_points,
)


def test_cubic_ramp_coeffs_and_overshoot():
    a2, a3, tau_elem = cubic_ramp_coeffs(xrange=1.2, tau=1.0, update_rate=1, xpix=60)
    assert a2 == pytest.approx(1.2) and a3 == pytest.approx(-0.4) and tau_elem == 60
    assert a2 + a3 == pytest.approx(2 * 1.0 * 1.2 / 3)            # cubic(1) == overshoot
    c = cubic_portion(a2, a3, tau_elem)
    assert c.size == 60 and c[-1] == pytest.approx(0.8) and c[0] > 0
    assert cubic_ramp_coeffs(1.0, 0.25, 2, 10)[2] == 2 * math.ceil(0.25 * 10)   # update_rate * ceil(tau*xpix)


def test_fast_axis_line_shape_and_counts():
    ln = fast_axis_line(xrange=2.0, offset=0.5, xpix=20, update_rate=1, tau=0.5, flyback=0.1)
    tau_elem, n_lin = 10, 20
    tau2, retn = math.ceil(0.1 * 0.5 * 20), math.ceil(0.1 * 20)     # 1, 2
    assert (ln.tau_elem, ln.linear_points, ln.tau2_elem, ln.ret_elem) == (tau_elem, n_lin, tau2, retn)
    assert ln.total_points == 2 * tau_elem + n_lin + 2 * tau2 + retn
    assert ln.return_points == 2 * tau2 + retn and ln.on_points == 2 * tau_elem + n_lin
    p = ln.positions - 0.5
    ov = ln.overshoot
    assert ov == pytest.approx(2 * 0.5 * 2.0 / 3)
    assert p[tau_elem - 1] == pytest.approx(-1.0)                    # accel ends at -xrange/2
    assert p[tau_elem + n_lin - 1] == pytest.approx(+1.0)            # linear ends at +xrange/2
    assert p[2 * tau_elem + n_lin - 1] == pytest.approx(1.0 + ov)    # decel ends at rest (overshoot)
    assert p[-1] == pytest.approx(-1.0 - ov)                         # return ends at the start of the next line
    # the linear part is a straight line of slope xrange / N
    lin = p[tau_elem:tau_elem + n_lin]
    assert np.allclose(np.diff(lin), 2.0 / n_lin)
    # the accel starts almost at rest (cubic: zero slope at x = 0)
    assert abs(p[1] - p[0]) < abs(lin[1] - lin[0])


def test_bidirectional_line_is_mirrored_forward():
    ln = fast_axis_line(xrange=1.0, offset=0.0, xpix=8, update_rate=2, tau=0.5, flyback=0.2, bidirectional=True)
    half = ln.total_points // 2
    assert ln.return_points == 0 and ln.tau2_elem == 0 and ln.ret_elem == 0
    assert np.allclose(ln.positions[half:], ln.positions[:half][::-1])
    assert ln.min_points == half


def test_s_curve():
    assert s_curve_points(7) == 2 and s_curve_points(2) == 0
    s = s_curve(0.0, 1.0, 5)
    assert s.size == 5 and s[-1] == pytest.approx(1.0) and np.all(np.diff(s) >= 0) and s[0] > 0.0
    assert s_curve(3.0, 3.0, 4).tolist() == [3.0] * 4
    assert s_curve(0.0, 1.0, 0).size == 0


def test_rate_rules():
    """Both rules divide by the CYCLE time -- 'Min AO rate needed' leaves the
    timing cluster's 'Exposure (sec)' unwired and feeds 'Cycle (sec)' into
    '# that Ramp needs' instead. CONFIRMED against the running LouisXIV
    2026-09-07: its X Waveform axis ends at 125.159 ms for this rig's
    settings, which is (n-1)/rate at the cycle-derived 543.307 Hz to six
    digits; the exposure rule would have put it at 113.381 ms."""
    ln = fast_axis_line(xrange=1.0, offset=0.0, xpix=60, update_rate=1, tau=1.0, flyback=0.1)
    # exposure_s is accepted but unused -- a wildly different one changes nothing
    r = ao_rate_for_line(ln, exposure_s=0.1, cycle_s=1.0, update_rate=1)
    r_other = ao_rate_for_line(ln, exposure_s=0.007, cycle_s=1.0, update_rate=1)
    assert r.ao_rate_hz == pytest.approx(r_other.ao_rate_hz)
    assert r.rate_for_exposure_hz == pytest.approx(ln.on_points / 1.0)      # the CYCLE, not 0.1
    assert r.option == "A" and r.rate_for_flyback_hz == pytest.approx(ln.min_points / 1.0)
    # and since on_points <= min_points, rule 2's Option A always wins
    assert r.ao_rate_hz == pytest.approx(ln.min_points / 1.0, rel=1e-3)
    assert ln.total_points / r.ao_rate_hz <= 1.0 + 1e-9                     # whole line fits the cycle
    # a shorter cycle scales it: the whole line still has to fit
    r2 = ao_rate_for_line(ln, exposure_s=0.1, cycle_s=0.1, update_rate=1)
    assert r2.ao_rate_hz >= ln.min_points / 0.1 * 0.999
    assert ln.total_points / r2.ao_rate_hz <= 0.1 + 1e-9
    assert r2.pixel_per_ms == pytest.approx(r2.ao_rate_hz / 1000.0)


def test_rate_matches_the_running_louisxiv():
    """The 6-digit check that settled which quantity rule 1 reads. LouisXIV's
    own settings on this rig: 60 px, Fract. Smoothing 0, Fractional Flyback
    0.15, cycle 0.127 s -> a 69-point line whose last AO point its X
    Waveform graph puts at 125.159 ms."""
    ln = fast_axis_line(xrange=101.695, offset=0.0, xpix=60, update_rate=1, tau=0.0, flyback=0.15)
    assert (ln.total_points, ln.tau_elem, ln.on_points) == (69, 0, 60)      # Tao = 0, as its panel reads
    r = ao_rate_for_line(ln, exposure_s=0.100042, cycle_s=0.127, update_rate=1)
    assert r.ao_rate_hz == pytest.approx(543.307, abs=0.05)
    last_point_ms = (ln.total_points - 1) / r.ao_rate_hz * 1000.0
    assert last_point_ms == pytest.approx(125.159, abs=0.01)


def test_flyback_rule_z_settle():
    # X flyback long enough for the Z settle -> Option A, nothing extra
    rate, opt, extra, imgs = min_rate_for_flyback(min_points=200, return_points=20, cycle_s=0.1, z_settle_ms=1.0)
    assert opt == "A" and rate == pytest.approx(2000.0) and (extra, imgs) == (0, 0)
    # Z settle longer than the X flyback: Option B, faster, with extra AO counts
    rate, opt, extra, imgs = min_rate_for_flyback(200, 20, 0.1, z_settle_ms=50.0)
    assert opt == "B" and rate == pytest.approx(180 / 0.05) and extra > 0 and imgs == 0
    # "Skip imgs": keep Option A and skip images instead
    rate, opt, extra, imgs = min_rate_for_flyback(200, 20, 0.1, z_settle_ms=50.0, skip_images=True)
    assert opt == "A" and extra == 0 and imgs >= 1


def test_check_ao_rate_caps_at_1000_khz_with_even_updates():
    assert check_ao_rate(25.6, 1) == (pytest.approx(25.6), 1)
    rate, ur = check_ao_rate(300.0, 5)                    # 1500 kHz > 1000: floor(1000/300)=3 -> even 2
    assert ur == 2 and rate == pytest.approx(600.0)
    with pytest.raises(ValueError):
        check_ao_rate(600.0, 2)                            # floor(1000/600) = 1 -> even 0: LouisXIV's dead end


def test_dma_ticks_round_to_the_25ns_tick():
    ticks, real = dma_ticks(25.6e3)
    assert ticks == 1562 and real == pytest.approx(TICKS_PER_S / 1562)
    assert dma_ticks(10e3) == (4000, pytest.approx(10e3))


def test_build_louisxiv_waveform_packs_one_line_per_trigger():
    wf = build_louisxiv_waveform(exposure_s=0.1, cycle_s=0.1, x_range_v=1.0, x_offset_v=0.0, x_pixels=60,
                                 n_slices=3, z_piezo_start_v=1.0, z_piezo_step_v=0.1,
                                 z_galvo_start_v=0.2, z_galvo_step_v=0.05)
    n = wf.line.total_points
    assert wf.scan.points_per_trigger == n and wf.scan.n_slices == 3
    assert wf.scan.channels["X Galvo"].size == 3 * n
    zp = wf.scan.channels["Z Piezo"]
    assert zp[0] == zp[n // 2] and zp[n] > zp[0]                     # constant, then stepped
    assert wf.s_curve_points == wf.line.return_points // 3
    if wf.s_curve_points:
        assert zp[n - 1] == zp[n]                                    # the S-curve lands on the next slice value
    assert wf.scan.ticks_between_points == wf.rate.ticks_between_points
    assert wf.scan.points_per_trigger * wf.scan.ticks_between_points <= 0.1 * TICKS_PER_S + wf.scan.ticks_between_points


# -- channel delays ----------------------------------------------------------
# `HHMI - SPIM Number of extrac counts needed for galvo inertia shift.vi` (sic)
# and `HHMI - Add counts to waveforms at beginning and end.vi`.

def test_a_delay_under_half_a_sample_rounds_away():
    """This rig's own defaults land here: X galvo 0.02 us at 1000 kHz."""
    d = extra_counts_for_delays(0.02, 0.0, 0.0, 1_000_000.0)
    assert d.total == 0 and not d.any_shift


def test_the_lag_is_the_largest_delay_in_samples():
    d = extra_counts_for_delays(5.0, 2.0, 0.0, 1_000_000.0)   # 1 us per sample
    assert d.total == 5


def test_each_channel_is_padded_by_its_own_share_of_the_lag():
    """end_i = round((1 - d_i/max) * total); begin_i = total - end_i."""
    d = extra_counts_for_delays(5.0, 2.0, 0.0, 1_000_000.0)
    assert d.x_galvo == (5, 0)      # largest delay: all padding at the front
    assert d.z_galvo == (2, 3)      # 2/5 of the way
    assert d.z_piezo == (0, 5)      # no delay: starts at once
    assert d.aotf == (5, 0)         # LouisXIV wires begin=total, end=0
    # every channel grows by the same total, which is what keeps them aligned
    for pair in (d.x_galvo, d.z_galvo, d.z_piezo, d.aotf):
        assert sum(pair) == d.total


def test_equal_delays_shift_everything_together():
    d = extra_counts_for_delays(3.0, 3.0, 3.0, 1_000_000.0)
    assert d.x_galvo == d.z_galvo == d.z_piezo == (3, 0)


def test_negative_delays_are_refused_rather_than_silently_misaligned():
    """LouisXIV's formula gives a negative delay a LONGER block than the rest.

    X = -4 us alone yields X (-4, 8) against Z (0, 4): 8 extra samples on one
    channel and 4 on the others, so they no longer line up. Downstream that
    truncates instead of failing, so this refuses.
    """
    with pytest.raises(ValueError, match="negative channel delay"):
        extra_counts_for_delays(-4.0, 0.0, 0.0, 1_000_000.0)


def test_padding_holds_the_edge_values():
    block = np.array([1.0, 2.0, 3.0, 4.0])
    np.testing.assert_array_equal(add_hold_counts(block, 2, 1),
                                  [1.0, 1.0, 1.0, 2.0, 3.0, 4.0, 4.0])
    np.testing.assert_array_equal(add_hold_counts(block, 0, 0), block)


def test_delays_keep_every_channel_the_same_length_and_the_block_in_its_cycle():
    kw = dict(exposure_s=0.01, cycle_s=0.02, x_range_v=1.0, x_offset_v=0.0, x_pixels=50)
    base = build_louisxiv_waveform(**kw)
    # the lag is round(largest delay x AO rate) samples -- derived from the
    # rate rather than hard-coded, so it does not quietly encode which
    # quantity rule 1 happens to read
    w = build_louisxiv_waveform(**kw, x_galvo_delay_us=300.0, z_galvo_delay_us=120.0)
    lag = round(300e-6 * base.rate.ao_rate_hz)
    assert lag >= 1, "pick a delay long enough to shift at least one sample"
    assert w.scan.points_per_trigger == base.scan.points_per_trigger + lag
    assert len({len(c) for c in w.scan.channels.values()}) == 1, "channels must stay aligned"
    # the block still fits the same cycle: LouisXIV lengthens Time Per WvFrm
    # instead, which our fixed-period free run cannot do
    def block_s(x):
        return x.scan.points_per_trigger * x.scan.ticks_between_points / TICKS_PER_S
    assert block_s(w) == pytest.approx(block_s(base), rel=1e-3)
    assert any("Channel delays" in n for n in w.notes)


def test_no_delay_changes_nothing():
    kw = dict(exposure_s=0.01, cycle_s=0.02, x_range_v=1.0, x_offset_v=0.0, x_pixels=50)
    a = build_louisxiv_waveform(**kw)
    b = build_louisxiv_waveform(**kw, x_galvo_delay_us=0.0, z_galvo_delay_us=0.0)
    assert a.scan.points_per_trigger == b.scan.points_per_trigger
    assert a.scan.words == b.scan.words
