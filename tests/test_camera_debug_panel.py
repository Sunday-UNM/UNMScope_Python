"""Camera Debug Panel: a read-only status window fed by the main window's
counters (headless Qt)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from unmscope.gui import main_window as mw
from unmscope.gui.camera_debug_panel import COLUMNS, CameraDebugPanel, CameraDebugStatus


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_panel_shows_the_provider_counters(app):
    st = CameraDebugStatus(exposures_downloaded=7, total_expected=10, exposures_acquired=8, download_backlog=1,
                           image_buffer_slots=200, dcam_api=True, error_text="", connected=True)
    panel = CameraDebugPanel(lambda: st)
    assert len(COLUMNS) == 7 and len(panel._cells) == 7
    assert [c[0].text() for c in panel._cells] == ["7", "8", "10", "1", "0", "200", "0"]
    assert panel.err_code.text() == "0"
    st.error_text = "Camera poll FAILED: boom"
    panel.refresh()
    assert panel.err_code.text() == "1" and "boom" in panel.err_source.text()
    assert panel.windowTitle() == "Debug Panel" and panel.width() == 677 and panel.height() == 510
    panel.close()


def test_provider_exception_does_not_break_the_panel(app):
    def bad():
        raise RuntimeError("driver gone")
    panel = CameraDebugPanel(bad)
    assert "driver gone" in panel.err_source.text()
    panel.close()


@pytest.fixture
def window(app, monkeypatch, tmp_path):
    monkeypatch.setattr(mw.WaveformConfig, "save", lambda self, path=None: tmp_path / "wc.json")
    w = mw.MainWindow()
    yield w
    w.close()


def test_window_status_and_utilities_button(window):
    st = window._camera_debug_status()
    assert st.connected is False and st.dcam_api is False and st.exposures_downloaded == 0
    window.backend_combo.setCurrentText("Simulated")
    window.on_connect_clicked()
    st = window._camera_debug_status()
    assert st.connected is True and st.dcam_api is False           # simulated camera: no DCAM
    assert st.image_buffer_slots == window.camera.buffer_capacity()[1]
    window._log("Camera poll FAILED: TypeError: x")
    assert "FAILED" in window._camera_debug_status().error_text
    btn = window.utilities_tab.buttons["Camera Debug Panel"]
    assert btn.isEnabled()
    btn.click()
    assert window.camera_debug_panel is not None and window.camera_debug_panel.isVisible()
    window.camera_debug_panel.close()
