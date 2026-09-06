"""The UNMScope copy of SPIMProject.ini: copy-on-first-use, format-preserving
writes, and the two stage settings sections."""
from pathlib import Path

import pytest

from unmscope.config import spim_ini
from unmscope.config.spim_ini import (
    XYZ_ASSIGNMENTS, RotationStageSettings, Simp285Settings, ensure_user_ini, read_ini, write_keys,
)

LOUISXIV = (
    "[SIMP-285 3D Stage]\r\nEnable? = False\r\nCOM Port = \"COM8\"\r\nVelocity (um/s) = 2500\r\n"
    "Settling Time (ms) = 300\r\nSimulate = FALSE\r\nXYZ Assignment = 5\r\n\r\n"
    "[Sample stage]\r\nAngle between stage and bessel beam (deg) = 45.000000\r\n\r\n"
    "[Rotation Stage (PI U651) Settings]\r\nEnable? = FALSE\r\nSimulate = FALSE\r\nSerial Number = \"\"\r\n"
    "Speed (deg/s) = 72\r\nSettling Time (ms) = 100\r\n\r\n"
    "[Misc Settings]\r\nSome Key = 1 \r\n"
).encode("utf-8")


@pytest.fixture
def ini(tmp_path, monkeypatch):
    src = tmp_path / "louisxiv" / "SPIMProject.ini"
    src.parent.mkdir()
    src.write_bytes(LOUISXIV)
    dest = tmp_path / "home" / ".unmscope" / "SPIMProject.ini"
    monkeypatch.setattr(spim_ini, "USER_INI", dest)
    return src, dest


def test_first_use_copies_louisxiv_file_byte_for_byte(ini):
    src, dest = ini
    assert ensure_user_ini(src) == dest and dest.read_bytes() == LOUISXIV
    src.write_bytes(b"[changed]\r\n")
    assert ensure_user_ini(src) == dest and dest.read_bytes() == LOUISXIV     # copied only once


def test_missing_source_gives_defaults(tmp_path, monkeypatch):
    dest = tmp_path / ".unmscope" / "SPIMProject.ini"
    monkeypatch.setattr(spim_ini, "USER_INI", dest)
    ensure_user_ini(tmp_path / "nope.ini")
    assert dest.exists() and Simp285Settings.load() == Simp285Settings()


def test_settings_read_the_louisxiv_values(ini):
    src, dest = ini
    ensure_user_ini(src)
    s = Simp285Settings.load()
    assert (s.enabled, s.com_port, s.velocity_um_s, s.settling_ms, s.simulate, s.xyz_assignment) == \
        (False, "COM8", 2500.0, 300.0, False, 5)
    assert s.assignment_name == "ZYX" and XYZ_ASSIGNMENTS[0] == "XYZ"
    r = RotationStageSettings.load()
    assert (r.enabled, r.simulate, r.serial_number, r.speed_deg_s, r.settling_ms) == (False, False, "", 72.0, 100.0)


def test_save_touches_only_its_keys_and_keeps_crlf(ini):
    src, dest = ini
    ensure_user_ini(src)
    s = Simp285Settings(enabled=True, com_port="COM3", velocity_um_s=1000, settling_ms=250.5, simulate=True,
                        xyz_assignment=1)
    s.save()
    raw = dest.read_bytes()
    assert b"\n" not in raw.replace(b"\r\n", b"")                       # CRLF everywhere
    assert b"Enable? = True\r\nCOM Port = \"COM3\"\r\nVelocity (um/s) = 1000\r\nSettling Time (ms) = 250.5\r\n" \
           b"Simulate = True\r\nXYZ Assignment = 1\r\n" in raw
    assert b"[Sample stage]\r\nAngle between stage and bessel beam (deg) = 45.000000" in raw
    assert b"[Misc Settings]\r\nSome Key = 1 \r\n" in raw                # unknown section untouched
    assert b"[Rotation Stage (PI U651) Settings]\r\nEnable? = FALSE" in raw  # other section's Enable? untouched
    assert Simp285Settings.load() == s
    assert src.read_bytes() == LOUISXIV                                # LouisXIV's file never written


def test_write_keys_appends_missing_keys_and_sections(tmp_path):
    p = tmp_path / "x.ini"
    p.write_bytes(b"[A]\r\nk1 = v1\r\n\r\n[B]\r\nk2=v2\r\n")
    write_keys(p, "A", {"k1": "new", "k3": "v3"})
    write_keys(p, "C", {"c": "1"})
    # No trailing blank line: the shared writer (config/ini_text.py) ends the
    # file after the last key it wrote, as LouisXIV's own ini does.
    assert p.read_bytes() == b"[A]\r\nk1 = new\r\nk3 = v3\r\n\r\n[B]\r\nk2=v2\r\n\r\n[C]\r\nc = 1\r\n"
    cfg = read_ini(p)
    assert cfg["A"]["k3"] == "v3" and cfg["B"]["k2"] == "v2" and cfg["C"]["c"] == "1"


def test_boolean_spellings(tmp_path):
    p = tmp_path / "b.ini"
    p.write_bytes(b"[SIMP-285 3D Stage]\r\nEnable? = TRUE\r\nSimulate = true\r\n")
    s = Simp285Settings.load(p)
    assert s.enabled and s.simulate and s.com_port == "COM8"
