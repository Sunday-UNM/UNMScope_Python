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
    # wait for frames, not for a fixed time: a fixed 0.4 s flaked under
    # full-suite load (the fake FPGA bursts when the CPU is oversubscribed)
    assert pump(app, 5.0, until=lambda: w.frame_count > 3), "no frames from Continuous scan"
    assert w._stack_frames is None and w.acquired_stack() is None
    w.on_acquire_clicked()
    pump(app, 0.3)


def test_calc_projects_the_retained_stack_and_save_files_writes_louisxiv_layout(app, window, tmp_path):
    """Items 2 and 3, end to end on the fake FPGA: after a Z-stack, Calc is
    live and fills the three views; with Save Files on, the stack lands in
    <data>/Cell1/img_CH<row>_000000.tif as a 10-page U16 OME-TIFF next to an
    AcqInfo.txt, and the next stack goes to Cell2."""
    import tifffile
    w = window
    w._data_dir = tmp_path                      # bypass the folder prompt
    w.save_files_chk.setChecked(True)
    # Calc is always enabled (LouisXIV's Max Projs latch); with no stack it only logs
    assert w.calc_projections_btn.isEnabled() and w.acquired_stack() is None
    msgs, orig_log = [], w._log
    w._log = lambda m: (msgs.append(m), orig_log(m))
    try:
        w._on_calc_projections()
    finally:
        w._log = orig_log
    assert any("no Z-stack in memory" in m for m in msgs)

    w.mode_combo.setCurrentText(mw.MODE_ZSTACK)
    pump(app, 0.05)
    w.on_acquire_clicked()
    assert pump(app, 10.0, until=lambda: not w.acquiring), "Z stack did not finish"
    pump(app, 0.3)

    # Calc
    assert w.calc_projections_btn.isEnabled() and w.deskew_check.isEnabled()
    w.calc_projections_btn.click()
    assert set(w._last_projections) == {"XY", "YZ", "XZ"}
    for name, view in w.projection_labels.items():
        assert view.pixmap() is not None and not view.pixmap().isNull(), f"{name} view empty"
        assert w.projection_save_btns[name].isEnabled()
    n, h, wd = w.acquired_stack().shape
    assert w._last_projections["XY"].shape[0] == h
    assert w._last_projections["XY"].shape[1] >= wd          # deskewed canvas is at least as wide

    # Save Files -> LouisXIV layout
    ch = w._selected_channel_index()
    exp1 = tmp_path / "Cell1"
    tif = exp1 / f"img_CH{ch:02d}_000000.tif"
    assert tif.exists(), sorted(p.name for p in tmp_path.rglob("*"))
    assert (exp1 / "AcqInfo.txt").exists()
    with tifffile.TiffFile(str(tif)) as tf:
        assert len(tf.pages) == 10 and tf.pages[0].dtype.name == "uint16" and tf.is_ome
    # AcqInfo.txt carries LouisXIV's keys, then our own block under [UNMScope]
    acq = (exp1 / "AcqInfo.txt").read_text(encoding="utf-8")
    assert "SizeZ_px = 10" in acq                      # LouisXIV's name for the slice count
    assert "Multi-positionAcq = FALSE" in acq
    assert "PositionX_mm" not in acq                   # skipped while not multi-position
    assert "StageAngle_deg" not in acq                 # likewise
    head, sep, extras = acq.partition("[UNMScope]")
    assert sep, "the extras block should be present"
    # "Mode = ..." is ours; "AOTFCycleMode = ..." is LouisXIV's, hence startswith
    assert not any(l.startswith("Mode = ") for l in head.splitlines())
    assert "Trigger mode = " in extras and "Camera serial = " in extras

    # projection save goes next to the stack
    w._on_save_projection("XY")
    assert (exp1 / f"img_CH{ch:02d}_000000_XYMIP.tif").exists()

    # a second stack -> the next experiment folder
    w.on_acquire_clicked()
    assert pump(app, 10.0, until=lambda: not w.acquiring)
    pump(app, 0.3)
    assert (tmp_path / "Cell2" / f"img_CH{ch:02d}_000000.tif").exists()


def test_save_files_off_saves_nothing_and_save_image_writes_the_last_frame(app, window, tmp_path):
    w = window
    w._data_dir = tmp_path
    w.save_files_chk.setChecked(False)
    w.mode_combo.setCurrentText(mw.MODE_ZSTACK)
    pump(app, 0.05)
    w.on_acquire_clicked()
    assert pump(app, 10.0, until=lambda: not w.acquiring)
    pump(app, 0.3)
    assert not list(tmp_path.rglob("*.tif"))
    assert w._last_frame is not None
    out = w._save_frame_to(tmp_path / "single.tif")
    import tifffile
    with tifffile.TiffFile(str(out)) as tf:
        assert len(tf.pages) == 1 and tf.pages[0].dtype.name == "uint16"


def test_save_stack_honours_the_base_name_timepoint_and_position(app, window, tmp_path):
    """Build Image Path.vi's three indices, end to end through _save_stack.

    Nothing drives timepoint or position past 0 yet (Timepoints and
    Multi-location are greyed by the user's decision), so this is what proves
    the plumbing is real rather than decorative.
    """
    import numpy as np
    w = window
    w._data_dir = tmp_path                       # bypass the save dialog
    w._save_base = "beads"                       # what the dialog would have set
    stack = np.zeros((3, 8, 8), np.uint16)

    p1 = w._save_stack(stack)
    assert p1 is not None and p1.name == "beads_CH00_000000.tif"

    p2 = w._save_stack(stack, timepoint=42)
    assert p2.name == "beads_CH00_000042.tif"

    # no position folder while the LouisXIV global is off
    p3 = w._save_stack(stack, position=0)
    assert p3.parent.name.startswith("Cell"), p3

    w.separate_position_folders = True
    p4 = w._save_stack(stack, timepoint=1, position=0)
    assert p4.parent.name == "position 1"        # 1-based, as Build Image Path is
    assert p4.name == "beads_CH00_000001.tif"

    p5 = w._save_stack(stack, position=3)
    assert p5.parent.name == "position 4"
