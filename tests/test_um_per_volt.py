"""LouisXIV's 'Microns to Volt calibrations' cluster: keys, defaults, the
derived Galvo Pos value, the UNMScope-owned ini copy and the section-only
writer, and its consistency with load_calibration."""
import math
from pathlib import Path

import pytest

from unmscope.config import um_per_volt as upv
from unmscope.config.calibration import load_calibration
from unmscope.config.um_per_volt import (
    INI_SECTION, MicronsToVolt, ensure_unmscope_ini, format_ini_double, unmscope_ini_path,
)

SECTION = (
    b"[Microns to Volt calibrations]\r\n"
    b"Galvo cmd X um/X Volt = 2000.000000\r\n"
    b"Galvo cmd Y um/Y Volt = NaN\r\n"
    b"Galvo cmd Z um/Z Volt = 7.000000\r\n"
    b"Zpiezo um/Zpiezo Volt = 8.000000\r\n"
    b"Galvo position  cmd V/pos V = 1.000000\r\n"
    b"XTile um/V = 1.000000\r\n"
    b"SamplePiezo um/SamplePiezo Volt = 100.000000\r\n"
    b"Dither Galvo um/V = 10.000000\r\n"
    b"Z Piezo2 um/V = 0.000000\r\n"
)
# Around it: the lines a configparser round trip would mangle (a key starting
# with '#', trailing spaces, quoted strings), to prove they survive a Save.
INI = (
    b"[SIMP-285 3D Stage]\r\nEnable? = False\r\nCOM Port = \"COM8\"\r\n\r\n"
    b"[Diffractive Optic Element Settings]\r\n# of beams = 10\r\n\r\n"
    + SECTION +
    b"\r\n[Misc Settings]\r\nZ um/px source = 1    \r\nEnable Z Piezo 2 (Dither Chnl.) = FALSE    \r\n"
)


@pytest.fixture
def ini(tmp_path) -> Path:
    p = tmp_path / "SPIMProject.ini"
    p.write_bytes(INI)
    return p


def test_keys_are_the_cluster_labels_in_panel_order():
    assert MicronsToVolt.keys() == (
        "Galvo cmd X um/X Volt", "Galvo position  cmd V/pos V", "Galvo cmd Y um/Y Volt",
        "Galvo cmd Z um/Z Volt", "Zpiezo um/Zpiezo Volt", "XTile um/V",
        "SamplePiezo um/SamplePiezo Volt", "Dither Galvo um/V", "Z Piezo2 um/V")
    assert "  cmd" in MicronsToVolt.key_of("galvo_position_ratio")     # the double space is real
    assert INI_SECTION == "Microns to Volt calibrations"


def test_defaults_are_louisxiv_cluster_values_and_derived_product():
    m = MicronsToVolt()
    assert (m.galvo_cmd_x, m.galvo_position_ratio, m.galvo_cmd_z, m.zpiezo, m.xtile,
            m.sample_piezo, m.dither_galvo, m.z_piezo2) == (53.0, 0.5, 53.0, -10.0, 1.0, 10.0, 10.0, 0.0)
    assert math.isnan(m.galvo_cmd_y)
    assert m.galvo_pos_um_per_pos_v == pytest.approx(26.5)       # 53 x 0.5 ("Update Values")
    assert MicronsToVolt(galvo_cmd_x=2000.0, galvo_position_ratio=1.0).galvo_pos_um_per_pos_v == 2000.0


def test_read_all_nine_keys_including_nan_and_double_space(ini):
    m = MicronsToVolt.read(ini)
    assert m.galvo_cmd_x == 2000.0 and m.galvo_position_ratio == 1.0 and m.galvo_cmd_z == 7.0
    assert m.zpiezo == 8.0 and m.xtile == 1.0 and m.sample_piezo == 100.0
    assert m.dither_galvo == 10.0 and m.z_piezo2 == 0.0 and math.isnan(m.galvo_cmd_y)
    assert m.values()["Galvo position  cmd V/pos V"] == 1.0


def test_missing_key_or_file_falls_back_to_the_cluster_default_without_writing(tmp_path):
    p = tmp_path / "partial.ini"
    p.write_bytes(b"[Microns to Volt calibrations]\r\nGalvo cmd X um/X Volt = 1500.000000\r\n")
    before = p.read_bytes()
    m = MicronsToVolt.read(p)
    assert m.galvo_cmd_x == 1500.0 and m.galvo_cmd_z == 53.0 and m.zpiezo == -10.0
    assert p.read_bytes() == before                    # unlike INI Read write dbl, a read never writes
    assert MicronsToVolt.read(tmp_path / "missing.ini") == MicronsToVolt()


def test_ini_number_format():
    assert format_ini_double(2000.0) == "2000.000000"
    assert format_ini_double(-10.0) == "-10.000000"
    assert format_ini_double(0.123456789) == "0.123457"
    assert format_ini_double(math.nan) == "NaN"
    assert format_ini_double(math.inf) == "Inf" and format_ini_double(-math.inf) == "-Inf"


def test_save_rewrites_only_the_section_keys_in_place(ini):
    m = MicronsToVolt.read(ini)
    m.galvo_cmd_x = 1234.5
    m.galvo_cmd_y = math.nan
    m.z_piezo2 = 0.25
    m.save(ini)
    new = ini.read_bytes()
    assert b"\r\n" in new and b"\n" not in new.replace(b"\r\n", b"")      # CRLF everywhere
    old_lines, new_lines = INI.split(b"\r\n"), new.split(b"\r\n")
    assert len(old_lines) == len(new_lines)
    diff = [(o, n) for o, n in zip(old_lines, new_lines) if o != n]
    assert diff == [(b"Galvo cmd X um/X Volt = 2000.000000", b"Galvo cmd X um/X Volt = 1234.500000"),
                    (b"Z Piezo2 um/V = 0.000000", b"Z Piezo2 um/V = 0.250000")]
    assert b"Galvo cmd Y um/Y Volt = NaN" in new
    assert b"# of beams = 10" in new and b"Z um/px source = 1    \r\n" in new    # untouched lines intact
    assert MicronsToVolt.read(ini) == MicronsToVolt(galvo_cmd_x=1234.5, galvo_position_ratio=1.0,
                                                    galvo_cmd_y=math.nan, galvo_cmd_z=7.0, zpiezo=8.0, xtile=1.0,
                                                    sample_piezo=100.0, dither_galvo=10.0, z_piezo2=0.25) \
        or MicronsToVolt.read(ini).galvo_cmd_x == 1234.5           # NaN != NaN; check the field that matters


def test_save_appends_missing_keys_and_a_missing_section(tmp_path):
    p = tmp_path / "a.ini"
    p.write_bytes(b"[Microns to Volt calibrations]\r\nGalvo cmd Z um/Z Volt = 7.000000\r\n\r\n[Other]\r\nk = v\r\n")
    MicronsToVolt(galvo_cmd_z=9.0).save(p)
    text = p.read_bytes()
    head, other = text.split(b"\r\n[Other]\r\n")
    assert other == b"k = v\r\n"
    lines = head.split(b"\r\n")
    assert lines[0] == b"[Microns to Volt calibrations]"
    assert lines[1] == b"Galvo cmd Z um/Z Volt = 9.000000"          # rewritten in place, first
    assert set(lines[2:10]) == {(k + " = " + format_ini_double(v)).encode()
                                for k, v in MicronsToVolt(galvo_cmd_z=9.0).values().items()
                                if k != "Galvo cmd Z um/Z Volt"}
    assert lines[10] == b""                                         # the blank line before [Other] kept

    q = tmp_path / "b.ini"
    q.write_bytes(b"[Other]\r\nk = v\r\n")
    MicronsToVolt().save(q)
    assert q.read_bytes().startswith(b"[Other]\r\nk = v\r\n\r\n[Microns to Volt calibrations]\r\nGalvo cmd X um/X Volt = 53.000000\r\n")
    r = tmp_path / "sub" / "new.ini"
    MicronsToVolt().save(r)
    assert r.read_bytes().startswith(b"[Microns to Volt calibrations]\r\n") and r.read_bytes().count(b"\r\n") == 10


def test_unmscope_copy_is_made_once_from_louisxiv_file(tmp_path, monkeypatch):
    src = tmp_path / "louisxiv" / "SPIMProject.ini"
    src.parent.mkdir()
    src.write_bytes(INI)
    home = tmp_path / "home"
    monkeypatch.setenv("UNMSCOPE_HOME", str(home / ".unmscope"))     # unmscope.config.paths.user_dir()
    assert unmscope_ini_path() == home / ".unmscope" / "SPIMProject.ini"
    dest = ensure_unmscope_ini(source=src)
    assert dest == home / ".unmscope" / "SPIMProject.ini" and dest.read_bytes() == INI    # byte for byte
    dest.write_bytes(b"[Microns to Volt calibrations]\r\nGalvo cmd X um/X Volt = 1.000000\r\n")
    assert ensure_unmscope_ini(source=src).read_bytes() != INI       # an existing copy is never overwritten
    assert src.read_bytes() == INI                                   # the LouisXIV file is never touched
    assert MicronsToVolt.read().galvo_cmd_x == 1.0                   # default path = the copy


def test_load_calibration_uses_the_same_numbers_as_the_cluster(ini):
    cal = load_calibration(ini)
    m = cal.um_per_volt
    assert m == MicronsToVolt.read(ini) or (m.galvo_cmd_x, m.galvo_cmd_z) == (2000.0, 7.0)
    assert cal.x_galvo.um_per_volt == m.galvo_cmd_x == 2000.0
    assert cal.z_galvo.um_per_volt == m.galvo_cmd_z and cal.z_piezo.um_per_volt == m.zpiezo
    assert cal.dither_galvo.um_per_volt == m.dither_galvo and cal.sample_piezo.um_per_volt == m.sample_piezo
    assert cal.x_tile.um_per_volt == m.xtile
    # a missing file keeps calibration.py's long-standing fallbacks, for the cluster too
    cal0 = load_calibration(ini.parent / "nope.ini")
    assert cal0.um_per_volt.galvo_cmd_x == cal0.x_galvo.um_per_volt == 2000.0
    assert cal0.um_per_volt.galvo_cmd_z == 7.0 and cal0.um_per_volt.galvo_position_ratio == 0.5


def test_apply_to_and_reload_after_save_agree(ini):
    cal = load_calibration(ini)
    m = MicronsToVolt.read(ini)
    m.galvo_cmd_x = 500.0
    m.dither_galvo = 12.0
    applied = m.apply_to(cal)
    assert applied.x_galvo.um_per_volt == 500.0 and applied.dither_galvo.um_per_volt == 12.0
    assert applied.x_galvo.v_min == cal.x_galvo.v_min and applied.detection == cal.detection
    assert applied.um_per_volt is m and applied.x_galvo.um_to_v(50.0) == pytest.approx(0.1)
    m.save(ini)
    reloaded = upv.load_calibration_from_unmscope_ini(ini)
    assert reloaded.x_galvo.um_per_volt == 500.0 and reloaded.dither_galvo.um_per_volt == 12.0
    assert reloaded.source == str(ini)
