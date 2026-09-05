"""The HW Config dialog (gui/hw_config_dialog.py), headless: greying rules,
camera switching, Apply / Save / Revert / Close semantics, and that only the
owned ini copy is ever written."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from unmscope.config import hw_config as hc
from unmscope.gui.hw_config_dialog import HwConfigDialog, show_hw_config_dialog

from test_hw_config import FIXTURE


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def ini(tmp_path, monkeypatch):
    """Owned copy made from a fake LouisXIV file; ~/.unmscope is never used."""
    monkeypatch.setattr(hc, "USER_INI", tmp_path / "home" / ".unmscope" / "SPIMProject.ini")
    src = tmp_path / "louisxiv.ini"
    src.write_bytes(FIXTURE.encode())
    p = hc.user_ini_path(None, source=src)
    return p, src


def make(app, ini):
    return HwConfigDialog(ini[0])


def enabled_map(d):
    return {a: w.isEnabled() for a, w in d._cam_bind.items()}


def test_dialog_shows_the_ini_and_is_non_modal(app, ini):
    d = make(app, ini)
    assert not d.isModal() and d.size().width() == 486 and d.size().height() == 490
    assert [d.tabs.tabText(i) for i in range(d.tabs.count())] == ["Cameras", "Imagine Optics", "Rotation Stage", "Misc."]
    assert d.camera_combo.currentText() == "Cam1"
    assert d.cam_enabled.isChecked() and d.cam_serial.text() == "102668" and d.cam_sync_readout.isChecked()
    assert d.cam_model.currentText() == "Orca4.0" and d.cam_binning.currentText() == "1x1"
    assert d.cam_dcam_port.value() == 3365 and d.cam_cmd_port.value() == 2222
    assert d.io_n_pts.value() == 5 and d.io_default_camera.currentText() == "Cam1"
    assert d._io_paths["haso_config_file"].text() == r"C:\Users\ALSM\Desktop\HASO4.dat"       # shown in Windows form
    assert d.rs_speed.text() == "72" and d.rs_settling.text() == "100"
    assert d.misc_z_source.currentText() == "Z Piezo" and d._misc_bind["enable_x_galvo_correction_lut"].isChecked()
    assert not d.enter_values_btn.isEnabled()          # X Galvo LUT editor not ported


def test_property_nodes_greying_rules(app, ini):
    d = make(app, ini)
    m = enabled_map(d)                                # Cam1: Enabled, not Remote
    assert all(m[a] for a in ("model", "serial_number", "image_transform", "sync_readout", "simulate", "binning", "save_index", "remote"))
    assert not any(m[a] for a in ("remote_ip", "dcam_port", "cmd_port"))
    d.cam_remote.setChecked(True)
    m = enabled_map(d)
    assert all(m[a] for a in ("remote_ip", "dcam_port", "cmd_port", "save_index", "remote"))
    assert not any(m[a] for a in ("model", "serial_number", "image_transform", "sync_readout", "simulate", "binning"))
    d.cam_enabled.setChecked(False)                    # 'disable all but Enabled switch'
    m = enabled_map(d)
    assert d.cam_enabled.isEnabled() and not any(v for a, v in m.items() if a != "enabled")
    assert d.config.cameras[0].remote and not d.config.cameras[0].enabled
    d.camera_combo.setCurrentIndex(1)                  # Cam2: Enabled=FALSE in the file
    assert not d.cam_serial.isEnabled() and not d.cam_enabled.isChecked()


def test_camera_combo_switches_cluster_without_losing_edits(app, ini):
    d = make(app, ini)
    d.cam_serial.setText("999")
    d.cam_serial.textEdited.emit("999")
    d.cam_image_transform.setCurrentIndex(5)
    d.cam_binning.setCurrentIndex(2)
    d.camera_combo.setCurrentIndex(1)
    assert d.cam_serial.text() == "" and d.cam_image_transform.currentText() == "None" and d.cam_binning.currentText() == "1x1"
    d.cam_save_index.setValue(7)
    d.camera_combo.setCurrentIndex(0)
    assert d.cam_serial.text() == "999" and d.cam_image_transform.currentText() == "Rot 90" and d.cam_binning.currentText() == "4x4"
    assert d.config.cameras[0].serial_number == "999" and d.config.cameras[0].image_transform == 5
    assert d.config.cameras[0].binning == 4 and d.config.cameras[1].save_index == 7


def test_other_pages_edit_their_clusters(app, ini):
    d = make(app, ini)
    d.io_enable.setChecked(True)
    d.io_analysis.setCurrentIndex(2)
    e = d._io_paths["correction_interaction_matrix_file"]
    e.setText(r"D:\ao\matrix.aoc"); e.textEdited.emit(e.text())
    d.io_sleep_ms.setValue(35)
    d.rs_serial.setText("0123"); d.rs_serial.textEdited.emit("0123")
    d.rs_speed.setText("36.5"); d.rs_speed.editingFinished.emit()
    d._misc_bind["enable_z_piezo_2"].setChecked(True)
    d.misc_z_source.setCurrentIndex(0)
    f = d._misc_bind["galvo2_alternating_first_v"]
    f.setText("abc"); f.editingFinished.emit()          # bad input: value kept, text restored
    assert f.text() == "0" and d.config.misc.galvo2_alternating_first_v == 0.0
    f.setText("-1.25"); f.editingFinished.emit()
    io, rs, m = d.config.imagine_optics, d.config.rotation_stage, d.config.misc
    assert io.enable and io.default_analysis_method == 2 and io.controller.sleep_after_command_apply_ms == 35
    assert io.controller.correction_interaction_matrix_file == "/D/ao/matrix.aoc"    # stored in LabVIEW path form
    assert rs.serial_number == "0123" and rs.speed_deg_s == 36.5
    assert m.enable_z_piezo_2 and m.z_um_px_source == 0 and m.galvo2_alternating_first_v == -1.25


def test_apply_writes_the_owned_copy_only_and_emits(app, ini):
    p, src = ini
    d = make(app, ini)
    d.show()
    got = []
    d.applied.connect(got.append)
    d.cam_serial.setText("999"); d.cam_serial.textEdited.emit("999")
    d.apply_btn.click()
    assert d.isVisible()                               # Apply keeps the window open
    assert len(got) == 1 and got[0].cameras[0].serial_number == "999" and got[0] is not d.config
    assert 'Serial Number = "999"' in p.read_bytes().decode()
    assert src.read_bytes() == FIXTURE.encode()        # LouisXIV's file untouched
    assert hc.load_hw_config(p).cameras[0].serial_number == "999"


def test_save_is_apply_plus_close(app, ini):
    p, _ = ini
    d = make(app, ini)
    d.show()
    got = []
    d.applied.connect(got.append)
    d.cam_sync_readout.setChecked(False)
    d.save_btn.click()
    assert not d.isVisible() and d.result() == QDialog.Accepted and len(got) == 1
    assert "Sync Readout = FALSE" in p.read_bytes().decode()


def test_revert_reloads_from_disk_and_close_discards(app, ini):
    p, _ = ini
    before = p.read_bytes()
    d = make(app, ini)
    d.show()
    d.cam_serial.setText("999"); d.cam_serial.textEdited.emit("999")
    d.rs_enable.setChecked(True)
    d.revert_btn.click()
    assert d.cam_serial.text() == "102668" and not d.rs_enable.isChecked()
    assert d.config.cameras[0].serial_number == "102668" and not d.config.rotation_stage.enable
    d.cam_serial.setText("777"); d.cam_serial.textEdited.emit("777")
    d.close_btn.click()
    assert not d.isVisible() and p.read_bytes() == before        # Close: edits dropped, nothing written
    assert HwConfigDialog(p).cam_serial.text() == "102668"


def test_show_helper_reuses_an_open_dialog(app, ini):
    p, _ = ini
    d1 = show_hw_config_dialog(None, p)
    d2 = show_hw_config_dialog(None, p, existing=d1)
    assert d2 is d1 and d1.isVisible()
    d1.close()
    d3 = show_hw_config_dialog(None, p, existing=d1)
    assert d3 is not d1 and d3.isVisible()
    d3.close()
