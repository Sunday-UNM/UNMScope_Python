import textwrap

import pytest

from unmscope.config.calibration import load_calibration


def test_defaults_when_ini_missing(tmp_path):
    cal = load_calibration(tmp_path / "nope.ini")
    assert cal.source == "defaults"
    assert cal.x_galvo.um_per_volt == 2000.0
    assert cal.z_galvo.um_to_v(7.0) == pytest.approx(1.0)
    assert cal.z_piezo.v_to_um(1.0) == pytest.approx(8.0)
    assert (cal.z_piezo.v_min, cal.z_piezo.v_max) == (-2.5, 10.0)
    # Fixed in LouisXIV, not per-rig: `HHMI - Z Piezo AOTF voltage limits.vi`
    # wires literal 0 and 10 to Minimum/Maximum S Piezo Voltage, and there is
    # no ini section for it. It is 0 .. 10, never negative.
    assert (cal.sample_piezo.v_min, cal.sample_piezo.v_max) == (0.0, 10.0)


def test_reads_real_style_ini(tmp_path):
    ini = tmp_path / "SPIMProject.ini"
    ini.write_text(textwrap.dedent("""
        [Microns to Volt calibrations]
        Galvo cmd X um/X Volt = 1500.000000
        Galvo cmd Z um/Z Volt = 6.500000
        Zpiezo um/Zpiezo Volt = 9.000000
        Dither Galvo um/V = 12.000000

        [X Galvo Limits (V)]
        Min (V) = -2.000000
        Max (V) = 2.000000
    """))
    cal = load_calibration(ini)
    assert cal.source.endswith("SPIMProject.ini")
    assert cal.x_galvo.um_per_volt == 1500.0
    assert cal.x_galvo.um_to_v(150.0) == pytest.approx(0.1)
    assert cal.x_galvo.clamp_v(3.0) == 2.0
    assert cal.x_galvo.um_max == pytest.approx(3000.0)
    assert cal.z_galvo.um_per_volt == 6.5 and cal.z_piezo.um_per_volt == 9.0 and cal.dither_galvo.um_per_volt == 12.0
    assert cal.z_galvo.v_max == 2.5            # missing section -> default limit


def test_real_ini_if_present():
    cal = load_calibration()
    if cal.source == "defaults":
        pytest.skip("SPIMProject.ini not on this machine")
    assert cal.x_galvo.um_per_volt == 2000.0
    assert cal.z_galvo.um_per_volt == 7.0
    assert cal.z_piezo.um_per_volt == 8.0


def test_aotf_defaults_and_linear_power_map(tmp_path):
    cal = load_calibration(tmp_path / "nope.ini")
    a = cal.aotf
    assert a.labels == ("637", "561", "488", "405") and a.n_channels == 4
    assert (a.v_min, a.v_max) == (0.0, 5.0)
    assert a.power_pct_to_v(0) == 0.0
    assert a.power_pct_to_v(100) == pytest.approx(5.0)
    assert a.power_pct_to_v(1) == pytest.approx(0.05)        # 1 % = 50 mV = 164 counts
    assert a.power_pct_to_v(250) == pytest.approx(5.0)       # clamped
    assert a.channel_for_row(2) == 2                          # identity routing (488 nm row -> ch 2)


def test_aotf_read_from_ini(tmp_path):
    ini = tmp_path / "SPIMProject.ini"
    ini.write_text(textwrap.dedent("""
        [AOTF Settings]
        Number of channels = 2
        Labels = "561,488"

        [AOTF Limits (V)]
        Min (V) = 1.000000
        Max (V) = 3.000000
    """))
    a = load_calibration(ini).aotf
    assert a.labels == ("561", "488") and a.n_channels == 2
    assert a.power_pct_to_v(50) == pytest.approx(2.0)
