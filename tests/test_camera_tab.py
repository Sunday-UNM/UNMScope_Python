"""The Camera tab's ROI behaviour (headless Qt), on the simulated camera."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from unmscope.gui.camera_tab import CameraTab
from unmscope.hardware.camera import SimulatedCamera
from unmscope.hardware.roi import Roi, full_roi

PIX = 6.5 / 30.0     # 0.2167 um at binning 1


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def make_tab(app, camera=None):
    logs = []
    tab = CameraTab(get_camera=lambda: camera, pixel_size_um=lambda b=1: PIX * b, log=logs.append)
    return tab, logs


def roi_fields(tab):
    return (tab.roi_left.value(), tab.roi_top.value(), tab.roi_right.value(), tab.roi_bottom.value())


def test_defaults_match_the_louisxiv_panel(app):
    tab, _ = make_tab(app)
    assert roi_fields(tab) == (1, 1, 2048, 2048)
    assert (tab.pix_x.value(), tab.pix_y.value()) == (2048, 2048)
    assert tab.fov_x.text() == "443.7 um" or tab.fov_x.text() == "443.8 um"   # 2048 * 6.5/30
    assert tab.sensor_mode() == "Normal Scan"
    assert not tab.dual_view_combo.isEnabled() and not tab.split_pix_spin.isEnabled()
    assert not tab.subrois.isEnabled()


def test_presets_and_centre_buttons(app):
    tab, _ = make_tab(app)
    tab.on_preset(1024)
    assert roi_fields(tab) == (513, 513, 1536, 1536)
    assert (tab.pix_x.value(), tab.pix_y.value()) == (1024, 1024)
    assert tab.fov_x.text() == f"{1024 * PIX:.1f} um"
    tab.on_preset(512)
    assert tab.roi.width == 512 and tab.roi.center == (1024.5, 1024.5)
    tab.roi_center_x.setValue(600)
    tab.roi_center_y.setValue(700)
    tab.on_center_roi_at()
    # DCAM snaps Left/Top down to the 4-px position unit: centre within one unit
    assert tab.roi.width == 512 and abs(tab.roi.center[0] - 600) <= 4 and abs(tab.roi.center[1] - 700) <= 4
    tab.on_center_roi()
    assert tab.roi.center == (1024.5, 1024.5)
    tab.on_use_all_pixels()
    assert roi_fields(tab) == (1, 1, 2048, 2048)


def test_editing_pixels_resizes_about_the_centre(app):
    tab, _ = make_tab(app)
    tab.on_preset(1024)
    tab.pix_x.setValue(1224)                       # HHMI - Adjust ROI: +100 each side
    assert (tab.roi.left, tab.roi.right) == (413, 1636)
    assert (tab.roi.top, tab.roi.bottom) == (513, 1536)
    tab.pix_y.setValue(512)
    assert (tab.roi.top, tab.roi.bottom) == (769, 1280)


def test_editing_a_field_is_coerced_like_dcam(app):
    tab, _ = make_tab(app)
    tab.roi_left.setValue(6)                       # position unit 4: 0-based 5 -> 4 -> 1-based 5
    assert tab.roi_left.value() == 5
    tab.roi_right.setValue(1030)                   # size rounded down to the unit
    assert tab.roi.width % 4 == 0 and tab.roi.right == 1028


def test_camera_receives_the_roi_and_sensor_mode(app):
    cam = SimulatedCamera()
    cam.connect()
    tab, logs = make_tab(app, cam)
    tab.refresh_from_camera()
    assert tab.camera_name_field.text() == "Simulated Camera"
    assert tab.actual_exposure.text().endswith(" ms") and tab.actual_rate.text().endswith(" Hz")
    tab.on_preset(512)
    assert cam.get_roi() == Roi(769, 769, 1280, 1280)          # applied at once: the camera is idle
    assert (cam.info.width, cam.info.height) == (512, 512)
    tab.sensor_mode_combo.setCurrentText("Split View")
    assert cam.get_sensor_mode() == "Split View"
    assert tab.dual_view_combo.isEnabled()
    # armed sequence: deferred to Acquire, then applied by apply_to_camera
    cam.start_sequence(None)
    tab.on_use_all_pixels()
    assert cam.get_roi() != full_roi(2048, 2048) and tab.pending_apply
    assert any("next Acquire" in m for m in logs)
    assert tab.apply_to_camera(cam) is True
    assert cam.get_roi() == full_roi(2048, 2048) and not cam.is_sequence_running()
    assert tab.apply_to_camera(cam) is False                    # nothing left to push
