"""HW Config settings (config/hw_config.py): the owned ini copy, value
encoding, and the line-preserving writer -- no Qt, no hardware."""
from pathlib import Path

import pytest

from unmscope.config import hw_config as hc

# A LouisXIV-style fixture: CRLF, '#'-prefixed key, four trailing spaces in
# [Misc Settings], a foreign section with True/False spelling, no final newline.
FIXTURE = (
    "[Sample stage]\r\nAngle between stage and bessel beam (deg) = 45.000000\r\n\r\n"
    "[Rotation Stage (PI U651) Settings]\r\nEnable? = FALSE\r\nSimulate = FALSE\r\nSerial Number = \"\"\r\n"
    "Speed (deg/s) = 72\r\nSettling Time (ms) = 100\r\n\r\n"
    "[Cam1.Camera Settings]\r\nEnabled = TRUE\r\nSimulate = FALSE\r\nModel = 3\r\nSerial Number = \"102668\"\r\n"
    "Save Index = 0\r\nSync Readout = TRUE\r\nImage Transform = 0\r\nRemote = FALSE\r\nRemoteIP = \"\"\r\n"
    "DCAM Port = 3365\r\nCmd Port = 2222\r\nBinning = 1\r\n\r\n"
    + "".join(
        f"[Cam{n}.Camera Settings]\r\nEnabled = FALSE\r\nSimulate = TRUE\r\nModel = 3\r\nSerial Number = \"\"\r\n"
        f"Save Index = {n - 1}\r\nSync Readout = TRUE\r\nImage Transform = 0\r\nRemote = FALSE\r\nRemoteIP = \"\"\r\n"
        f"DCAM Port = 3365\r\nCmd Port = 2222\r\nBinning = 1\r\n\r\n" for n in (2, 3, 4, 5))
    + "[Imagine Optics Settings]\r\nEnable = FALSE\r\nSimulate = FALSE\r\n# Pts (Default) = 5\r\nDefault Camera = 0\r\n"
    "Default Analyis Method = 0\r\nMatlab Script Directory = \"\"\r\n\r\n"
    "[Imagine Optics Settings.Controller]\r\n"
    "Wavefront Corrector Setup File = \"/C/Users/ALSM/Desktop/WaveFrontCorrector.dat\"\r\n"
    "HasoConfigFile = \"/C/Users/ALSM/Desktop/HASO4.dat\"\r\n"
    "Correction Interaction Matrix File = \"\"\r\nDefault Wavefront Positions File = \"\"\r\n"
    "Flat Wavefront Positions File = \"\"\r\nSleep after command apply (ms) = 20\r\n\r\n"
    "[Misc Settings]\r\nZ um/px source = 1    \r\nDisable Xg-Zg-Zp Calibrations = FALSE    \r\n"
    "Enable X Galvo Correction LUT = TRUE    \r\nEnable Z Piezo 2 (Dither Chnl.) = FALSE    \r\n"
    "Use Z Galvo and Dither as Alternating Galvo Channels = FALSE    \r\n"
    "Galvo 1 Alternating First (V) = 5    \r\nGalvo 1 Alternating Second (V) = 5    \r\n"
    "Galvo 2 Alternating First (V) = 0    \r\nGalvo 2 Alternating Second (V) = 0    \r\n\r\n"
    "[Thorlabs FW103 Filter Settings]\r\nSerial Number = \"40213924\"\r\nSimulate = True\r\nMove time (ms) = 1000"
)
_cam3 = FIXTURE[FIXTURE.index("[Cam3."):FIXTURE.index("[Cam4.")]
FIXTURE_NO_CAM3 = FIXTURE.replace(_cam3, "")


@pytest.fixture
def source(tmp_path):
    src = tmp_path / "louisxiv" / "SPIMProject.ini"
    src.parent.mkdir()
    src.write_bytes(FIXTURE.encode())
    return src


@pytest.fixture
def owned(tmp_path, source, monkeypatch):
    """The owned copy in a fake home; the real ~/.unmscope is never touched."""
    monkeypatch.setattr(hc, "USER_INI", tmp_path / "home" / ".unmscope" / "SPIMProject.ini")
    return hc.user_ini_path(None, source=source)


def test_first_use_copies_louisxiv_file_byte_for_byte_then_leaves_it_alone(tmp_path, source, monkeypatch):
    monkeypatch.setattr(hc, "USER_INI", tmp_path / "home" / ".unmscope" / "SPIMProject.ini")
    p = hc.user_ini_path(None, source=source)
    assert p == hc.USER_INI and p.read_bytes() == source.read_bytes()
    p.write_bytes(b"[X]\r\ny = 1")
    assert hc.user_ini_path(None, source=source).read_bytes() == b"[X]\r\ny = 1"      # existing copy is kept
    with pytest.raises(FileNotFoundError):
        hc.user_ini_path(tmp_path / "nowhere.ini", source=tmp_path / "missing.ini")


def test_load_reads_every_section_with_louisxiv_encoding(owned):
    cfg = hc.load_hw_config(owned)
    c1 = cfg.cameras[0]
    assert (c1.enabled, c1.simulate, c1.model, c1.serial_number, c1.save_index) == (True, False, 3, "102668", 0)
    assert (c1.sync_readout, c1.image_transform, c1.remote, c1.remote_ip) == (True, 0, False, "")
    assert (c1.dcam_port, c1.cmd_port, c1.binning) == (3365, 2222, 1)
    assert c1.model_name == "Orca4.0" and c1.image_transform_name == "None"
    assert cfg.cameras[1].simulate and cfg.cameras[1].save_index == 1
    assert cfg.cameras[4].save_index == 4
    owned.write_bytes(FIXTURE_NO_CAM3.encode())
    assert hc.load_hw_config(owned).cameras[2] == hc.CameraSettings()     # section absent -> panel defaults
    io = cfg.imagine_optics
    assert io.n_pts_default == 5 and io.default_camera == 0 and io.default_analysis_method == 0
    assert io.controller.haso_config_file == "/C/Users/ALSM/Desktop/HASO4.dat"
    assert io.controller.sleep_after_command_apply_ms == 20
    rs = cfg.rotation_stage
    assert (rs.enable, rs.simulate, rs.serial_number, rs.speed_deg_s, rs.settling_time_ms) == (False, False, "", 72.0, 100.0)
    m = cfg.misc
    assert m.z_um_px_source == 1 and m.enable_x_galvo_correction_lut and not m.enable_z_piezo_2
    assert (m.galvo1_alternating_first_v, m.galvo2_alternating_first_v) == (5.0, 0.0)


def test_save_without_edits_is_byte_identical(owned):
    before = owned.read_bytes()
    hc.save_hw_config(hc.load_hw_config(owned), owned)
    assert owned.read_bytes() == before


def test_save_rewrites_only_the_edited_values(owned):
    before = owned.read_bytes().decode().split("\r\n")
    cfg = hc.load_hw_config(owned)
    cfg.cameras[0].serial_number = "999"
    cfg.cameras[0].sync_readout = False
    cfg.cameras[1].binning = 4
    cfg.misc.galvo1_alternating_first_v = 2.5
    cfg.misc.disable_xg_zg_zp_calibrations = True
    cfg.rotation_stage.enable = True
    cfg.imagine_optics.n_pts_default = 7
    cfg.imagine_optics.controller.haso_config_file = hc.windows_path_to_labview(r"C:\cfg\new haso.dat")
    hc.save_hw_config(cfg, owned)
    raw = owned.read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n") and not raw.endswith(b"\n")     # CRLF kept, no final newline added
    after = raw.decode().split("\r\n")
    changed = [(a, b) for a, b in zip(before, after) if a != b]
    assert len(before) == len(after)
    assert changed == [
        ("Enable? = FALSE", "Enable? = TRUE"),
        ('Serial Number = "102668"', 'Serial Number = "999"'),
        ("Sync Readout = TRUE", "Sync Readout = FALSE"),
        ("Binning = 1", "Binning = 4"),
        ("# Pts (Default) = 5", "# Pts (Default) = 7"),
        ('HasoConfigFile = "/C/Users/ALSM/Desktop/HASO4.dat"', 'HasoConfigFile = "/C/cfg/new haso.dat"'),
        ("Disable Xg-Zg-Zp Calibrations = FALSE    ", "Disable Xg-Zg-Zp Calibrations = TRUE    "),   # trailing spaces kept
        ("Galvo 1 Alternating First (V) = 5    ", "Galvo 1 Alternating First (V) = 2.5    "),
    ]
    assert hc.load_hw_config(owned) == cfg


def test_missing_keys_and_sections_are_added_like_louisxivs_read_then_write(owned):
    owned.write_bytes(FIXTURE_NO_CAM3.encode())
    cfg = hc.load_hw_config(owned)
    cfg.cameras[2].serial_number = "C3"             # [Cam3.Camera Settings] does not exist in this file
    hc.save_hw_config(cfg, owned)
    text = owned.read_bytes().decode()
    assert "\r\n\r\n[Cam3.Camera Settings]\r\nEnabled = FALSE\r\n" in text
    assert 'Serial Number = "C3"\r\n' in text and text.endswith("Binning = 1\r\n")
    cams = text.split("[Cam3.Camera Settings]")[1]
    assert [ln.split(" = ")[0] for ln in cams.strip().split("\r\n")] == [k for _, k, _ in hc.CameraSettings.KEYS]
    assert "[Thorlabs FW103 Filter Settings]\r\nSerial Number = \"40213924\"\r\nSimulate = True" in text   # foreign section untouched
    assert hc.load_hw_config(owned).cameras[2].serial_number == "C3"


def test_ini_text_editor_primitives():
    t = hc.IniText("[A]\r\nx = 1\r\n# Pts = 2\r\n\r\n[B]\r\ny = 2")
    assert t.get("A", "# Pts") == "2" and t.get("B", "y") == "2" and t.get("B", "zz") is None
    t.set("A", "z", "3")
    t.set("B", "y", "9")
    t.set("C", "q", '"s"')
    assert t.text() == '[A]\r\nx = 1\r\n# Pts = 2\r\nz = 3\r\n\r\n[B]\r\ny = 9\r\n\r\n[C]\r\nq = "s"\r\n'
    lf = hc.IniText("[A]\nx = 1\n")
    lf.set("A", "x", "2")
    assert lf.text() == "[A]\nx = 2\n"


def test_value_encoding_is_louisxivs():
    assert hc.format_value(True, "bool") == "TRUE" and hc.format_value(False, "bool") == "FALSE"
    assert hc.format_value("abc", "str") == '"abc"' and hc.format_value("", "path") == '""'
    assert hc.format_value(3, "int") == "3" and hc.format_value(72.0, "float") == "72"
    assert hc.format_value(0.25, "float") == "0.25"
    assert hc.parse_value("True", "bool") is True and hc.parse_value("FALSE", "bool") is False
    assert hc.parse_value('"102668"', "str") == "102668" and hc.parse_value("bare", "str") == "bare"
    assert hc.parse_value("2.000000", "int") == 2 and hc.parse_value("45.000000", "float") == 45.0


def test_labview_path_form():
    assert hc.labview_path_to_windows("/C/Users/ALSM/x.dat") == r"C:\Users\ALSM\x.dat"
    assert hc.windows_path_to_labview(r"D:\a\b c.wcs") == "/D/a/b c.wcs"
    assert hc.labview_path_to_windows("") == "" and hc.windows_path_to_labview("") == ""
    assert hc.labview_path_to_windows(r"C:\already") == r"C:\already"


def test_enum_tables_are_the_typedefs():
    assert hc.CAMERA_MODELS == ("Andor", "Andor sCMOS", "Orca2.8", "Orca4.0")
    assert hc.IMAGE_TRANSFORMS == ("None", "Transpose", "V Flip", "H Flip", "Diag Flip", "Rot 90", "Rot 180", "Rot 270")
    assert hc.BINNINGS == (1, 2, 4) and hc.BINNING_LABELS == ("1x1", "2x2", "4x4")
    assert hc.Z_UM_PX_SOURCES == ("Z Galvo", "Z Piezo")
    assert hc.ANALYSIS_METHODS == ("RMS Contrast", "Peak Intensity", "ModulationDepth (Matlab)")
    assert hc.CAMERA_IDS == ("Cam1", "Cam2", "Cam3", "Cam4", "Cam5")
    assert [s for s, _ in hc.HwConfig().sections()] == [
        "Cam1.Camera Settings", "Cam2.Camera Settings", "Cam3.Camera Settings", "Cam4.Camera Settings",
        "Cam5.Camera Settings", "Imagine Optics Settings", "Imagine Optics Settings.Controller",
        "Rotation Stage (PI U651) Settings", "Misc Settings"]


@pytest.mark.skipif(not hc.LOUISXIV_INI.exists(), reason="LouisXIV's SPIMProject.ini not on this machine")
def test_real_louisxiv_file_round_trips_byte_for_byte(tmp_path):
    p = hc.user_ini_path(tmp_path / "SPIMProject.ini")            # a copy; LouisXIV's file is read only
    before = p.read_bytes()
    cfg = hc.load_hw_config(p)
    assert cfg.cameras[0].serial_number == "102668" and cfg.cameras[0].model_name == "Orca4.0"
    hc.save_hw_config(cfg, p)
    assert p.read_bytes() == before == hc.LOUISXIV_INI.read_bytes()
