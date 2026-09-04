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
