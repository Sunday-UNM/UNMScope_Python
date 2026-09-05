"""The real GUI acquisition flow -- Z stack, Continuous, Z stack -- on the
Simulated camera + the FAKE FPGA. No hardware, offscreen Qt. Mirrors
spikes/26 and 27 so the counting logic (closing trigger, warm-up
discard, stop grace, AO clamp, scope) is covered by pytest."""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from unmscope.gui import main_window as mw  # noqa: E402
from unmscope.hardware.fake_fpga import FakeFpgaTriggerController  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pump(app, seconds, until=None):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.002)
    return False


@pytest.fixture
def window(app, monkeypatch):
    monkeypatch.setattr(mw.MainWindow, "fpga_controller_factory", FakeFpgaTriggerController)
    monkeypatch.setattr(mw.QMessageBox, "warning", lambda *a, **k: None)
    w = mw.MainWindow()
    logs = []
    w._log = logs.append
    w.logs = logs
    w.backend_combo.setCurrentText("Simulated")
    w.on_connect_clicked()
    w.on_fpga_connect_clicked()
    assert w.camera is not None and w.fpga is not None
    w.exposure_spin.setValue(5.0)                 # sync period = readout floor ~10.4 ms -> fast tests
    for i, (chk, _wl, spin) in enumerate(w.excitation_rows):
        chk.setChecked(i == 0)
        spin.setValue(10 if i == 0 else 0)
    w.z_start_spin.setValue(0.0); w.z_end_spin.setValue(9.0); w.z_interval_spin.setValue(1.0)
    pump(app, 0.05)
    yield w
    if w.acquiring:
        w.on_acquire_clicked()
        pump(app, 0.2)
    w.on_disconnect_clicked()
    w.on_fpga_disconnect_clicked()
    pump(app, 0.05)


def _zstack(app, w, closing):
    w.mode_combo.setCurrentText(mw.MODE_ZSTACK)
    pump(app, 0.05)
    assert w.slice_count_field.text() == "10"
    w.on_acquire_clicked()
    assert w.acquiring
    done = pump(app, 10.0, until=lambda: not w.acquiring)
    pump(app, 0.3)
    assert done, "Z stack did not finish"
    assert w.frame_count == 10
    assert w._triggers_fired == 10 + closing


@pytest.mark.parametrize("sync", [True, False])
def test_zstack_continuous_zstack(app, window, sync):
    w = window
    w.sync_readout_chk.setChecked(sync)
    closing = 1 if sync else 0
    _zstack(app, w, closing)
    assert w._warmup_discarded == 0                              # fresh camera: no garbage frame
    # continuous
    w.mode_combo.setCurrentText(mw.MODE_CONTINUOUS)
    pump(app, 0.05)
    w.on_acquire_clicked()
    assert w.acquiring
    pump(app, 0.4)
    w.on_acquire_clicked()
    pump(app, 0.3)
    assert not w.acquiring
    assert w._triggers_fired >= 15
    assert w.frame_count == w._triggers_fired - closing          # sim camera is exact
    assert w._warmup_discarded == closing                        # sync: previous run left an exposure open
    # and again
    _zstack(app, w, closing)
    assert w._warmup_discarded == closing


def test_simulate_on_fpga_clamps_ao_and_feeds_the_scope(app, window):
    w = window
    regs = w.fpga._session.registers
    assert regs["AO Limit Max (counts)"].read()["X Galvo"] == 0      # clamped at connect (simulated camera)
    assert w.scope is not None and w.scope.running
    w.mode_combo.setCurrentText(mw.MODE_CONTINUOUS)
    pump(app, 0.05)
    w.on_acquire_clicked()
    pump(app, 0.5)
    assert w.fpga.ao_clamped is True
    assert any("SIMULATE ON FPGA" in m for m in w.logs)
    st = w.scope.trigger_stats()
    w.on_acquire_clicked()
    pump(app, 0.3)
    assert st is not None and st.edges >= 10
    assert st.period_ms == pytest.approx(w._trigger_period_s * 1000, rel=0.05)
    assert w.last_waveform is not None and w.last_waveform.points_per_trigger > 10


def test_arm_failure_is_reported_and_unwinds(app, window, monkeypatch):
    w = window
    monkeypatch.setattr(w.fpga, "start_free_run", lambda *a, **k: False)
    w.fpga.last_error = "injected"
    w.mode_combo.setCurrentText(mw.MODE_CONTINUOUS)
    pump(app, 0.05)
    w.on_acquire_clicked()
    pump(app, 0.3)
    assert not w.acquiring
    assert any("arm FAILED" in m for m in w.logs)


def test_simulate_on_fpga_keeps_aotf_off(app, window):
    w = window
    regs = w.fpga._session.registers
    w.mode_combo.setCurrentText(mw.MODE_CONTINUOUS)
    pump(app, 0.05)
    w.on_acquire_clicked()
    pump(app, 0.3)
    assert any("AOTF forced OFF" in m for m in w.logs)
    assert regs["AOTF ch (V)"].read()["AOTF ch 0"] == 0 and regs["AOTF ch out (V)"].read()["AOTF ch 0"] == 0
    w.on_acquire_clicked()
    pump(app, 0.3)


def test_real_camera_run_drives_selected_aotf_channel(app, window):
    """A camera that reacts to DIO4 (the Orca) is not clamped: the selected
    Excitation row's power becomes its AOTF channel level for the run."""
    w = window
    w.camera.reacts_to_dio4 = True                    # behave like the Orca for the FPGA side
    regs = w.fpga._session.registers
    for i, (chk, _wl, spin) in enumerate(w.excitation_rows):
        chk.setChecked(i == 2)                        # 488 nm row
        spin.setValue(100.0 if i == 2 else 0)
    w.mode_combo.setCurrentText(mw.MODE_CONTINUOUS)
    pump(app, 0.05)
    w.on_acquire_clicked()
    pump(app, 0.3)
    assert w.acquiring
    assert any("488 nm at 100 % -> AOTF ch 2 = 5.000 V (16384 counts)" in m for m in w.logs)
    assert regs["AOTF ch (V)"].read()["AOTF ch 2"] == 16384
    assert regs["AOTF ch out (V)"].read()["AOTF ch 2"] == 16384
    assert regs["AOTF ch (V)"].read()["AOTF ch 0"] == 0
    w.on_acquire_clicked()
    pump(app, 0.4)
    assert regs["AOTF ch (V)"].read()["AOTF ch 2"] == 0    # off at Stop


def test_zstack_is_retained_and_continuous_is_not(app, window):
    """Item 3/2 foundation: a Z-stack must be kept in memory (for TIFF save
    and Calc projections); Continuous scan is unbounded and must retain
    nothing. Warm-up/stale frames are dropped before retention."""
    import numpy as np
    w = window

    # Z stack: exactly the accepted slices are retained, oldest first.
    w.mode_combo.setCurrentText(mw.MODE_ZSTACK)
    pump(app, 0.05)
    assert w.slice_count_field.text() == "10"
    w.on_acquire_clicked()
    assert pump(app, 10.0, until=lambda: not w.acquiring), "Z stack did not finish"
    pump(app, 0.3)
    stack = w.acquired_stack()
    assert stack is not None
    assert stack.shape[0] == w.frame_count == 10
    assert stack.ndim == 3 and stack.shape[1] > 0 and stack.shape[2] > 0
    assert stack.dtype == np.uint16 or np.issubdtype(stack.dtype, np.integer)

    # Continuous: retains nothing, no matter how many frames stream.
    w.mode_combo.setCurrentText(mw.MODE_CONTINUOUS)
    pump(app, 0.05)
    w.on_acquire_clicked()
    assert w.acquiring
    pump(app, 0.4)
    assert w.frame_count > 3
    assert w._stack_frames is None and w.acquired_stack() is None
    w.on_acquire_clicked()
    pump(app, 0.3)
