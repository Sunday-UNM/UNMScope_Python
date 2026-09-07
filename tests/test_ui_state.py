"""Settings the user chose are carried into the next session (2026-09-07).

"why do the software keep going to default options that you have stored.
Why not keep the last checked options given [by] the user as the starting
point for the next session. Same for other places."

Every session used to start from hard-coded defaults -- DEFAULT_ACTIVE for
the scope ticks, 488 nm for the laser, 20 ms/div for the timebase -- so any
arrangement built up in one session was gone by the next.

conftest redirects UNMSCOPE_HOME to a tmp dir for every test, so none of
this touches the real ~/.unmscope.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QPushButton, QSlider, QSpinBox,
)

from unmscope.config import ui_state  # noqa: E402
from unmscope.gui import main_window as mw  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


# -- the store itself ------------------------------------------------------

def test_round_trips_every_widget_type(app, tmp_path):
    chk, combo, spin, dspin, slider = QCheckBox(), QComboBox(), QSpinBox(), QDoubleSpinBox(), QSlider()
    combo.addItems(["a", "b", "c"])
    spin.setRange(0, 100)
    dspin.setRange(0.0, 10.0)
    dspin.setDecimals(2)
    slider.setRange(0, 1000)
    widgets = {"chk": chk, "combo": combo, "spin": spin, "dspin": dspin, "slider": slider}

    chk.setChecked(True); combo.setCurrentText("c"); spin.setValue(42)
    dspin.setValue(3.25); slider.setValue(700)
    ui_state.save(ui_state.collect(widgets), tmp_path / "s.json")

    for w in widgets.values():                       # wipe them back to nothing
        (w.setChecked(False) if isinstance(w, QCheckBox)
         else w.setCurrentIndex(0) if isinstance(w, QComboBox) else w.setValue(0))
    ui_state.apply(widgets, ui_state.load(tmp_path / "s.json"))

    assert chk.isChecked() and combo.currentText() == "c" and spin.value() == 42
    assert dspin.value() == pytest.approx(3.25) and slider.value() == 700


def test_a_setting_that_no_longer_fits_is_ignored_not_fatal(app, tmp_path):
    """A file written by an older build must never stop the GUI starting."""
    combo, spin = QComboBox(), QSpinBox()
    combo.addItems(["a", "b"])
    spin.setRange(0, 10)
    state = {"combo": "a-mode-that-was-removed", "spin": "not a number",
             "gone": 1, "spin_too_big": 9999}
    done = ui_state.apply({"combo": combo, "spin": spin, "spin_too_big": spin}, state)

    assert "combo" not in done and "spin" not in done      # rejected, nothing changed
    assert combo.currentText() == "a"
    assert "spin_too_big" in done and spin.value() == 10    # clamped into range, not rejected


def test_a_corrupt_or_missing_file_is_a_fresh_start(app, tmp_path):
    assert ui_state.load(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json at all", encoding="utf-8")
    assert ui_state.load(bad) == {}
    bad.write_text('["a list, not a dict"]', encoding="utf-8")
    assert ui_state.load(bad) == {}


def test_unstorable_widget_types_are_skipped(app):
    """A plain button has no setting; storing one would write junk."""
    assert ui_state.widget_value(QPushButton("go")) is None
    assert ui_state.collect({"btn": QPushButton("go")}) == {}


# -- the window ------------------------------------------------------------

def test_a_first_run_still_gets_the_defaults(app):
    w = mw.MainWindow()
    try:
        assert not ui_state.default_path().exists()
        assert w.excitation_rows[2][0].isChecked()          # 488 nm, the shipped default
        assert w.scope_panel.tdiv_combo.currentText() == "20 ms"
    finally:
        w.close()


def test_the_next_session_starts_where_the_last_one_finished(app):
    w = mw.MainWindow()
    w.excitation_rows[2][0].setChecked(False)               # not 488 this time
    w.excitation_rows[0][0].setChecked(True)                # 637
    w.excitation_rows[0][2].setValue(42.5)
    for c, chk in w.scope_panel.channel_checks.items():
        chk.setChecked(c in (10, 19))                       # only Z Piezo and AOTF 2
    w.scope_panel.tdiv_combo.setCurrentText("500 ms")
    w.scope_panel.overlay_btn.setChecked(True)
    w.mode_combo.setCurrentText(mw.MODE_ZSTACK)
    w.z_end_spin.setValue(37.5)
    w.xg_pixels.setValue(120)
    w.close()                                               # closeEvent saves
    assert ui_state.default_path().exists()

    w2 = mw.MainWindow()
    try:
        assert w2.excitation_rows[0][0].isChecked() and not w2.excitation_rows[2][0].isChecked()
        assert w2.excitation_rows[0][2].value() == pytest.approx(42.5)
        assert [c for c, k in w2.scope_panel.channel_checks.items() if k.isChecked()] == [10, 19]
        assert w2.scope_panel.tdiv_combo.currentText() == "500 ms"
        assert w2.scope_panel.overlay_btn.isChecked()
        assert w2.mode_combo.currentText() == mw.MODE_ZSTACK
        assert w2.z_end_spin.value() == pytest.approx(37.5)
        assert w2.xg_pixels.value() == 120
    finally:
        w2.close()


def test_the_camera_settings_come_back_too(app):
    """"Same for other places" -- not just the Waveforms tab."""
    w = mw.MainWindow()
    w.camera_tab.exposure_spin.setValue(77.0)
    w.camera_tab.sync_readout_chk.setChecked(False)
    w.camera_tab.roi_bottom.setValue(1024)
    w.close()

    w2 = mw.MainWindow()
    try:
        assert w2.camera_tab.exposure_spin.value() == pytest.approx(77.0)
        assert not w2.camera_tab.sync_readout_chk.isChecked()
        assert w2.camera_tab.roi_bottom.value() == 1024
    finally:
        w2.close()


def test_hold_and_geometry_are_deliberately_not_remembered(app):
    """Coming up frozen reads as broken, not as restored; and the window is
    pixel-matched to LouisXIV's panel, so a stale saved size would silently
    break the match that CLAUDE.md requires."""
    w = mw.MainWindow()
    w.scope_panel.hold_btn.setChecked(True)
    size_before = (w.width(), w.height())
    w.resize(900, 700)
    w.close()

    saved = ui_state.load()
    assert not any("hold" in k.lower() or "geometry" in k.lower() or "size" in k.lower()
                   for k in saved), sorted(saved)

    w2 = mw.MainWindow()
    try:
        assert not w2.scope_panel.hold_btn.isChecked()
        assert (w2.width(), w2.height()) == size_before
    finally:
        w2.close()


def test_saving_is_not_confused_by_the_shutdown(app):
    """The teardown drives controls (the camera tab is written back from the
    device), so the state has to be captured before any of that runs."""
    w = mw.MainWindow()
    w.backend_combo.setCurrentText("Simulated")
    w.on_connect_clicked()
    assert w.camera is not None
    w.camera_tab.exposure_spin.setValue(63.0)
    w.close()
    assert ui_state.load().get("cam_exposure_ms") == pytest.approx(63.0)
