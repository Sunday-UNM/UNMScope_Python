"""The Connect crash of 2026-09-04 05:47 (docs/known_issues.md).

A cold Orca spends ~10 s inside DCAM's initializeDevice. The driver pumps
the native message queue while it blocks, so Qt delivered the second click
the user made at a button that still looked live: on_connect_clicked() was
re-entered from inside the first call, opened a second DCAM session, and
the access violation fired when control unwound into the half-initialised
first open.

These tests inject the re-entrant click from inside connect() -- exactly
where the driver delivered the real one. No hardware, offscreen Qt.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from unmscope.gui import main_window as mw  # noqa: E402
from unmscope.hardware.camera import SimulatedCamera  # noqa: E402
from unmscope.hardware.fake_fpga import FakeFpgaTriggerController  # noqa: E402


def monkeypatch_instance(obj, name, fn):
    """Bind a replacement onto one instance (pytest's monkeypatch.setattr on a
    fixture-owned object outlives the test otherwise)."""
    setattr(obj, name, fn)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, monkeypatch):
    monkeypatch.setattr(mw.MainWindow, "fpga_controller_factory", FakeFpgaTriggerController)
    monkeypatch.setattr(mw.QMessageBox, "warning", lambda *a, **k: None)
    w = mw.MainWindow()
    w.logs = []
    w._log = w.logs.append
    w.backend_combo.setCurrentText("Simulated")
    yield w
    w._blocking_op = None          # so teardown runs even if a test left it set
    if w.camera is not None:
        w.on_disconnect_clicked()
    if w.fpga is not None:
        w.on_fpga_disconnect_clicked()


def test_second_connect_click_during_a_blocking_open_is_ignored(window, monkeypatch):
    built, opened = [], []

    class ReentrantCamera(SimulatedCamera):
        def __init__(self):
            super().__init__()
            built.append(self)

        def connect(self):
            window.on_connect_clicked()      # what the native pump delivered
            opened.append(self)
            super().connect()

    monkeypatch.setattr(mw, "SimulatedCamera", ReentrantCamera)
    window.on_connect_clicked()

    assert len(built) == 1, "the re-entered click constructed a second camera"
    assert len(opened) == 1, "the re-entered click opened a second device"
    assert window.camera is built[0]
    assert window.camera.is_connected
    assert any("ignored" in m for m in window.logs)
    assert window._blocking_op is None
    assert QApplication.overrideCursor() is None


def test_camera_is_not_published_until_the_open_returns(window, monkeypatch):
    seen = []

    class SlowCamera(SimulatedCamera):
        def connect(self):
            seen.append(window.camera)       # what anything re-entrant finds
            super().connect()

    monkeypatch.setattr(mw, "SimulatedCamera", SlowCamera)
    window.on_connect_clicked()

    assert seen == [None], "a half-initialised camera was visible on the window"
    assert window.camera is not None and window.camera.is_connected


def test_controls_are_dead_before_the_open_starts(window, monkeypatch):
    # "Disable the button BEFORE the blocking call" is half the fix: a
    # live-looking button for ten seconds is what makes people click twice.
    state = {}

    class InspectingCamera(SimulatedCamera):
        def connect(self):
            state["connect_btn"] = window.connect_btn.isEnabled()
            state["backend_combo"] = window.backend_combo.isEnabled()
            state["fpga_connect_btn"] = window.fpga_connect_btn.isEnabled()
            state["acquire_btn"] = window.acquire_btn.isEnabled()
            state["cursor"] = QApplication.overrideCursor() is not None
            super().connect()

    monkeypatch.setattr(mw, "SimulatedCamera", InspectingCamera)
    window.on_connect_clicked()

    assert state == {"connect_btn": False, "backend_combo": False,
                     "fpga_connect_btn": False, "acquire_btn": False,
                     "cursor": True}
    assert window.connect_btn.isEnabled() is False   # connected now
    assert window.disconnect_btn.isEnabled() is True
    assert QApplication.overrideCursor() is None


def test_failed_connect_restores_the_buttons(window, monkeypatch):
    class FailingCamera(SimulatedCamera):
        def connect(self):
            raise RuntimeError("DCAM: no device")

    monkeypatch.setattr(mw, "SimulatedCamera", FailingCamera)
    window.on_connect_clicked()

    assert window.camera is None
    assert window.connect_btn.isEnabled(), "a failed connect left its button dead"
    assert not window.disconnect_btn.isEnabled()
    assert window.backend_combo.isEnabled()
    assert window._blocking_op is None
    assert QApplication.overrideCursor() is None


def test_second_fpga_connect_click_is_ignored(window, monkeypatch):
    opened = []

    class ReentrantFpga(FakeFpgaTriggerController):
        def connect(self):
            window.on_fpga_connect_clicked()
            opened.append(self)
            super().connect()

    monkeypatch.setattr(mw.MainWindow, "fpga_controller_factory", ReentrantFpga)
    window.on_fpga_connect_clicked()

    assert len(opened) == 1, "the re-entered click opened a second FPGA session"
    assert window.fpga is not None and window.fpga.is_connected


def test_an_fpga_click_during_a_camera_open_is_ignored(window, monkeypatch):
    # The guard is one flag for the whole GUI thread, not one per button:
    # any driver call re-entered from inside another is the same fault.
    class ReentrantCamera(SimulatedCamera):
        def connect(self):
            window.on_fpga_connect_clicked()
            super().connect()

    monkeypatch.setattr(mw, "SimulatedCamera", ReentrantCamera)
    window.on_connect_clicked()

    assert window.fpga is None, "the FPGA was opened from inside the camera open"
    assert window.camera is not None and window.camera.is_connected


def test_close_during_a_blocking_open_is_refused(window, monkeypatch):
    outcome = []

    class ClosingCamera(SimulatedCamera):
        def connect(self):
            outcome.append(window.close())   # the pump delivers WM_CLOSE
            super().connect()

    monkeypatch.setattr(mw, "SimulatedCamera", ClosingCamera)
    window.on_connect_clicked()

    assert outcome == [False], "the window closed while the driver was mid-open"
    assert window.camera is not None and window.camera.is_connected


def test_disconnect_is_guarded_too(window, monkeypatch):
    closes = []

    class ReentrantOnClose(SimulatedCamera):
        def disconnect(self):
            window.on_disconnect_clicked()   # a second Disconnect click
            closes.append(self)
            super().disconnect()

    monkeypatch.setattr(mw, "SimulatedCamera", ReentrantOnClose)
    window.on_connect_clicked()
    window.on_disconnect_clicked()

    assert len(closes) == 1, "the re-entered click closed the device twice"
    assert window.camera is None
    assert window.connect_btn.isEnabled()


# -- The same hole on Acquire ------------------------------------------------
#
# _start_acquisition runs a long chain of blocking DCAM calls and only sets
# self.acquiring at the very end, so a second click during the arm used to
# re-enter it and arm twice. _stop_acquisition is worse: it is reachable from
# the Stop click, the FPGA error signal, the poll timer's buffer check, both
# disconnects and closeEvent, and a driver pump can deliver any of those from
# inside another.

@pytest.fixture
def running(window):
    w = window
    w.on_connect_clicked()
    w.on_fpga_connect_clicked()
    w.exposure_spin.setValue(5.0)
    for i, (chk, _wl, spin) in enumerate(w.excitation_rows):
        chk.setChecked(i == 0)
        spin.setValue(10 if i == 0 else 0)
    return w


def test_second_acquire_click_during_the_arm_is_ignored(running, monkeypatch):
    prepared, armed = [], []
    orig_prepare = running.camera.prepare_sequence
    orig_arm = running.fpga.start_free_run

    def reentrant_prepare():
        running.on_acquire_clicked()     # the second click, mid-arm
        prepared.append(1)
        return orig_prepare()

    def counting_arm(*a, **k):
        armed.append(1)
        return orig_arm(*a, **k)

    monkeypatch.setattr(running.camera, "prepare_sequence", reentrant_prepare)
    monkeypatch.setattr(running.fpga, "start_free_run", counting_arm)
    running.on_acquire_clicked()

    assert prepared == [1], "the re-entered click armed the camera twice"
    assert armed == [1], "the re-entered click armed the FPGA twice"
    assert running.acquiring
    assert any("ignored" in m for m in running.logs)


def test_a_nested_stop_does_not_disarm_twice(running, monkeypatch):
    running.on_acquire_clicked()
    assert running.acquiring
    disarms = []
    orig = running.fpga.stop_free_run

    def reentrant_stop_free_run():
        disarms.append(1)
        running._stop_acquisition()      # e.g. the FPGA error signal landing
        return orig()

    monkeypatch.setattr(running.fpga, "stop_free_run", reentrant_stop_free_run)
    running.on_acquire_clicked()         # Stop

    assert disarms == [1], "the FPGA was disarmed twice"
    assert running.acquiring is False
    assert running._stopping is False


def test_stop_stays_live_while_a_run_drives_the_galvos(running):
    running.on_acquire_clicked()
    assert running.acquiring
    # If the guard left this disabled the user could not stop a run that is
    # driving the galvos and holding the AOTF open.
    assert running.acquire_btn.isEnabled(), "Stop was left dead during a run"
    assert running.acquire_btn.text() == "Stop"
    running.on_acquire_clicked()
    assert running.acquiring is False
    assert running.acquire_btn.text() == "Acquire"
    assert running.acquire_btn.isEnabled()


def test_an_acquire_click_during_a_disconnect_is_ignored(running, monkeypatch):
    running.on_acquire_clicked()
    assert running.acquiring
    orig = running.camera.disconnect

    def reentrant_disconnect():
        running.on_acquire_clicked()     # an Acquire click mid-teardown
        return orig()

    monkeypatch.setattr(running.camera, "disconnect", reentrant_disconnect)
    running.on_disconnect_clicked()

    assert running.camera is None
    assert running.acquiring is False


# -- What the adversarial review turned up -----------------------------------

def test_a_pumped_poll_tick_during_disconnect_touches_no_driver(running):
    # _stop_acquisition deliberately leaves camera_poll_timer running for a
    # grace window, and camera.disconnect() pumps the native queue -- so the
    # 30 ms tick used to land inside the close, on a device that still
    # reported is_connected.
    running.on_acquire_clicked()
    touched = []
    orig_disconnect = running.camera.disconnect
    orig_remaining = running.camera.remaining_image_count

    def observing_disconnect():
        running._poll_camera_for_frame()      # the pumped tick
        return orig_disconnect()

    def observing_remaining():
        touched.append(1)
        return orig_remaining()

    monkeypatch_instance(running.camera, "disconnect", observing_disconnect)
    monkeypatch_instance(running.camera, "remaining_image_count", observing_remaining)
    running.on_disconnect_clicked()

    assert touched == [], "the poll tick drained a camera that was being unloaded"
    assert running.camera is None


def test_camera_is_unpublished_and_the_timer_stopped_before_the_close(running):
    seen = {}
    orig = running.camera.disconnect

    def observing_disconnect():
        seen["camera"] = running.camera
        seen["timer_running"] = running.camera_poll_timer.isActive()
        return orig()

    monkeypatch_instance(running.camera, "disconnect", observing_disconnect)
    running.on_acquire_clicked()
    running.on_disconnect_clicked()

    assert seen == {"camera": None, "timer_running": False}


def test_connection_buttons_stay_dead_for_the_whole_run(running):
    # _start_acquisition greys all four; _end_blocking must not undo that.
    # A live Disconnect during a run would yank the camera or the FPGA out
    # from under moving galvos and an open AOTF.
    running.on_acquire_clicked()
    assert running.acquiring
    assert not running.connect_btn.isEnabled()
    assert not running.disconnect_btn.isEnabled(), "camera could be yanked mid-run"
    assert not running.fpga_connect_btn.isEnabled()
    assert not running.fpga_disconnect_btn.isEnabled(), "FPGA could be yanked mid-run"
    assert not running.backend_combo.isEnabled()
    assert running.acquire_btn.isEnabled(), "Stop must stay live"


def test_a_close_refused_during_a_call_is_re_issued_afterwards(window, app, monkeypatch):
    # Refusing the close is right; losing it is not. Without the re-issue the
    # user's X does nothing and they force-kill the process mid-driver.
    outcome = []

    class ClosingCamera(SimulatedCamera):
        def connect(self):
            outcome.append(window.close())
            super().connect()

    monkeypatch.setattr(mw, "SimulatedCamera", ClosingCamera)
    window.on_connect_clicked()

    assert outcome == [False], "the window closed from inside the open"
    assert window.camera is not None            # the open finished intact
    app.processEvents()                          # the deferred close fires
    assert window.camera is None, "the deferred close never ran"


def test_a_raise_in_begin_blocking_does_not_wedge_the_gui(window):
    def boom():
        raise RuntimeError("Qt went away")

    window._grey_out_for_blocking_call = boom
    with pytest.raises(RuntimeError):
        window.on_connect_clicked()

    # A stuck flag would make every button a silent no-op and, via closeEvent,
    # make the window unclosable with the hardware live.
    assert window._blocking_op is None
    assert QApplication.overrideCursor() is None
    del window._grey_out_for_blocking_call
    window.on_connect_clicked()
    assert window.camera is not None, "the GUI was left wedged"
