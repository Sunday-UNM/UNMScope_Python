"""The Utilities tab (tool grid + Low-Level Waveform Config) inside the main
window, headless: the grid, the live config fields, the Scan Setup dither
sync, and load_stack_from_file presenting a saved stack as an acquired one."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from unmscope.fileio.tiff_stack import save_tiff_stack
from unmscope.gui import main_window as mw


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, monkeypatch, tmp_path):
    # never touch the user's ~/.unmscope from a test
    monkeypatch.setattr(mw.WaveformConfig, "save", lambda self, path=None: tmp_path / "wc.json")
    w = mw.MainWindow()
    yield w
    w.close()


def test_grid_has_the_11_kept_tools_five_wired(window):
    tab = window.utilities_tab
    assert len(tab.buttons) == 11
    for dropped in ("Align Laser", "Image Reviewer", "Calculate PSF", "Auto Background", "View TIF stack",
                    "Resave OME-XML TIFs", "Shift Vslit calibration"):
        assert dropped not in tab.buttons                                # removed 2026-09-05 (user)
    wired = [name for name, b in tab.buttons.items() if b.isEnabled()]
    assert wired == ["um per V calibration", "Sample Stage Control", "Camera Debug Panel", "FPGA Scope",
                     "Reset HW", "HW Config"]
    assert not tab.buttons["FPGA Monitor"].isEnabled()


def test_reset_hw_reopens_the_camera(window):
    window.backend_combo.setCurrentText("Simulated")
    window.on_connect_clicked()
    first = window.camera
    assert first is not None and first.is_connected
    msgs, orig = [], window._log
    window._log = lambda m: (msgs.append(m), orig(m))
    try:
        window.utilities_tab.buttons["Reset HW"].click()
    finally:
        window._log = orig
    assert window.camera is not None and window.camera.is_connected and window.camera is not first
    assert not first.is_connected
    assert any("Reset HW done" in m for m in msgs)


def test_fpga_scope_button_shows_the_waveforms_tab(window):
    window.top_tabs.setCurrentIndex(1)
    window.utilities_tab.buttons["FPGA Scope"].click()
    assert window.top_tabs.currentWidget() is window.scope_panel


def test_live_fields_and_dither_sync(window):
    panel = window.utilities_tab.waveform_panel
    assert panel.fractional_flyback.isEnabled() and not panel.duty_pct.isEnabled()
    assert not panel.z_motion.isEnabled()                       # only the default item is known
    panel.fractional_flyback.setValue(0.2)
    assert window.waveform_config.fractional_flyback == 0.2
    panel.dither_triangle_pulses.setValue(3.5)
    assert window.dg_sweeps.value() == 3.5                       # Scan Setup Dither box follows
    window.dg_flyback.setValue(0.05)
    assert panel.dither_fract_flyback.value() == 0.05            # and the other way round
    assert panel.config().dither_fract_flyback == 0.05


def test_load_stack_from_file_presents_it_as_an_acquired_stack(window, tmp_path):
    stack = (np.random.default_rng(0).integers(0, 4000, size=(5, 64, 48))).astype(np.uint16)
    path = tmp_path / "img_CH00_000000.tif"
    save_tiff_stack(path, stack)
    assert window.acquired_stack() is None
    assert window.load_stack_from_file(str(path)) is True
    got = window.acquired_stack()
    assert got.shape == (5, 64, 48) and np.array_equal(got, stack)
    assert window.calc_projections_btn.isEnabled()          # always enabled now (LouisXIV's Max Projs latch)
    assert "loaded" in window.frame_counter_label.text()
