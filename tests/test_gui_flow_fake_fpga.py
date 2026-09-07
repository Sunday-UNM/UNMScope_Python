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


# -- Acquire auto-switches the Waveforms tab to Simulated on Simulate-on-FPGA -----------
# The user's follow-up simplification: Simulate-on-FPGA clamps every AO output to 0 V by
# design, so the real scope is known-uninformative there -- Acquire should just show the
# computed waveform automatically rather than making the user flip Source by hand.

def test_acquire_with_a_simulated_camera_shows_the_computed_waveform_automatically(app, window):
    w = window                                    # fixture already uses the Simulated backend
    assert not w.camera.reacts_to_dio4             # Simulate-on-FPGA: this run
    w.scope_panel.source_combo.setCurrentText("Hardware")   # as if left there from a real-camera look
    w.mode_combo.setCurrentText(mw.MODE_CONTINUOUS)
    pump(app, 0.05)

    w.on_acquire_clicked()
    assert w.acquiring

    assert w.scope_panel.source_combo.currentText() == "Simulated"     # switched with no user action
    snap = w.scope_panel._preview_snap
    assert snap is not None and len(snap.frames) > 0
    assert snap.frames[:, 8].astype("int64").max() != 0                # X Galvo: real, nonzero waveform

    w.on_acquire_clicked()
    pump(app, 0.3)


def test_trigger_period_is_the_cycle_time_not_the_exposure(app, window):
    """The Z-stack image-count bug, at its root.

    MEASURED by the user in LouisXIV (2026-09-06): Cam exp 0.1 s with Cycle
    time 0.127 s -- 27 ms for the X galvo to fly back before the next cycle.
    Our SYNCREADOUT period had been max(exposure, readout + margin), i.e. the
    exposure itself, so the galvo got zero flyback time and triggers landing
    in readout were silently dropped: fewer images than Slices.

    Reconciled 2026-09-06 against docs/louisxiv_cycle_time_semantics.md: the
    user confirmed Custom Cycle Time IS ticked on this rig, so 0.127 s is a
    value they typed, not one LouisXIV computed -- the default (Custom off)
    is now LouisXIV's own camera-cycle formula (engine_times() /
    Camera.cycle_time_s()), with the port's own hardware-margin floor
    (Camera.trigger_period_ms()) still enforced separately, since it
    protects against a different, real failure mode.
    """
    w = window
    panel = w.utilities_tab.waveform_panel
    w.exposure_spin.setValue(100.0)
    w.sync_readout_chk.setChecked(True)
    w.mode_combo.setCurrentText(mw.MODE_ZSTACK)
    w.z_start_spin.setValue(0); w.z_end_spin.setValue(4); w.z_interval_spin.setValue(1)   # 5 slices
    pump(app, 0.05)

    def run():
        w.on_acquire_clicked()
        assert pump(app, 30.0, until=lambda: not w.acquiring)
        pump(app, 0.3)
        return w._trigger_period_s * 1000.0

    # default (Custom Cycle Time off): LouisXIV's own camera-cycle formula,
    # NOT a 27% flyback padding -- at a 100 ms full-frame SYNCREADOUT
    # exposure the camera's own readout term is negligible next to it, so
    # this lands on the exposure itself (docs/louisxiv_cycle_time_semantics.md).
    # The 127 ms the rig actually runs at is now something the user sets
    # explicitly via Custom Cycle Time, exactly as in LouisXIV.
    panel.custom_cycle_time.setChecked(False)
    assert run() == pytest.approx(100.0, abs=0.5)
    assert w.frame_count == w.z_target_frames == 5

    # the user's own LouisXIV value, typed
    panel.custom_cycle_time.setChecked(True)
    panel.cycle_time_s.setValue(0.127)
    assert run() == pytest.approx(127.0, abs=0.5)

    # and the 0.2 the panel had been left at, which is where the "2x" came from
    panel.cycle_time_s.setValue(0.200)
    assert run() == pytest.approx(200.0, abs=0.5)

    # a cycle time under LouisXIV's own camera-cycle floor is raised to it
    # (engine_times' own flooring, Q2), with a log line naming the floor
    panel.cycle_time_s.setValue(0.050)
    w.logs.clear()
    period = run()
    assert period >= w.camera.trigger_period_ms(100.0) - 1e-6
    assert any("LouisXIV raises it to" in m for m in w.logs)

    # a MUCH shorter exposure where the camera's own readout floor exceeds
    # even LouisXIV's camera-cycle rule: the port's bench-measured safety
    # margin (Camera.trigger_period_ms, spikes/19) is what raises it here,
    # not engine_times() -- a separate, distinctly-labelled log line, since
    # LouisXIV itself has no such margin.
    w.exposure_spin.setValue(5.0)
    panel.cycle_time_s.setValue(0.001)
    w.logs.clear()
    period = run()
    assert period == pytest.approx(10.376, abs=0.01)
    assert period == pytest.approx(w.camera.trigger_period_ms(5.0), abs=1e-6)
    assert any("port safety margin" in m for m in w.logs)

    w.exposure_spin.setValue(100.0)
    panel.custom_cycle_time.setChecked(False)


# -- frame accounting: the double discard (2026-09-06) --------------------------
# Found by the adversarially-verified frame-loss hunt, three lenses agreeing:
# the stale-pre-trigger filter discarded the SYNCREADOUT warm-up frame without
# charging the warm-up count, so the next frame -- real slice 1 -- was then
# discarded as warm-up. N-1 frames, a timeout, no save. The simulated camera
# delivers instantly, so with reacts_to_dio4 forced on (it is True on the real
# Orca) every leading frame lands inside the pre-trigger window: the worst case.

def _five_slice_sync(app, w):
    w.sync_readout_chk.setChecked(True)
    w.exposure_spin.setValue(100.0)
    w.z_start_spin.setValue(0); w.z_end_spin.setValue(4); w.z_interval_spin.setValue(1)
    w.mode_combo.setCurrentText(mw.MODE_ZSTACK)
    pump(app, 0.05)
    w.on_acquire_clicked()
    assert pump(app, 30.0, until=lambda: not w.acquiring), "stack did not finish"
    pump(app, 0.4)


def test_second_sync_stack_keeps_every_slice_when_the_warmup_frame_is_early(app, window):
    """Deterministic: the stale predicate is stubbed to fire on exactly the
    first pop of run 2 -- the warm-up frame arriving inside the window --
    so the test does not depend on the simulated camera's (instant, and
    therefore unphysical) delivery timing."""
    w = window
    _five_slice_sync(app, w)                   # run 1: fresh camera, nothing open
    assert w.frame_count == 5 and w._warmup_discarded == 0

    pops = {"n": 0}
    def first_pop_looks_stale():
        pops["n"] += 1
        return pops["n"] == 1
    w._is_stale_pre_trigger_frame = first_pop_looks_stale
    try:
        _five_slice_sync(app, w)               # run 2: the closing edge left an exposure open
    finally:
        del w._is_stale_pre_trigger_frame      # back to the class method
    assert w._warmup_discarded == 1            # exactly one physical frame discarded ...
    assert w._stale_discarded == 0             # ... charged to warm-up, not counted twice
    assert w.frame_count == w.z_target_frames == 5   # ... and every slice kept
    assert w.acquired_stack().shape[0] == 5
    assert not any("timed out" in m for m in w.logs)


def test_stale_window_never_reaches_a_real_frame_however_long_the_cycle(app, window):
    """EDGE, 20 ms exposure, Custom Cycle Time 0.6 s: the first real slice
    lands about exposure + readout after the arm. The old half-period
    window (300 ms) swallowed it; the window is now capped at half of
    exposure + readout."""
    import time as _t
    w = window
    w.camera.reacts_to_dio4 = True
    w.sync_readout_chk.setChecked(False)
    w.exposure_spin.setValue(20.0)
    w._trigger_period_s = 0.6
    physical_ms = 20.0 + w.camera.readout_ms()
    w._arm_time = _t.perf_counter() - physical_ms / 1000.0      # a real frame's earliest arrival
    assert not w._is_stale_pre_trigger_frame(), "a real first slice must not be called stale"
    w._arm_time = _t.perf_counter() - 0.001                      # 1 ms after arming: leftover territory
    assert w._is_stale_pre_trigger_frame()
    w._arm_time = 0.0
    w.camera.reacts_to_dio4 = False


def test_exposure_edits_mid_run_do_not_reach_the_camera(app, window):
    w = window
    w.sync_readout_chk.setChecked(False)
    w.exposure_spin.setValue(20.0)
    pump(app, 0.05)
    w.mode_combo.setCurrentText(mw.MODE_CONTINUOUS)
    pump(app, 0.05)
    w.on_acquire_clicked()
    assert w.acquiring
    before = w.camera.get_exposure_ms()
    w.logs.clear()
    w.exposure_spin.setValue(200.0)             # a wheel or arrow nudge mid-run
    pump(app, 0.05)
    assert w.camera.get_exposure_ms() == pytest.approx(before)
    assert any("applies at the next Acquire" in m for m in w.logs)
    w.on_acquire_clicked()
    pump(app, 0.3)


# -- Waveforms tab, Source: Simulated (2026-09-07) --------------------------------------
# The user's original complaint: Simulate-on-FPGA clamps every AO output to 0 V by
# design, so the real scope reads flat even when the computed waveform (Z piezo
# included) is correct. Preview shows the computed values directly -- no FPGA
# involved at all, proven structurally below, not just by not calling anything.

class _FpgaLandmine:
    """Raises on ANY attribute access or call. Swapped in for w.fpga to prove a
    code path never references the FPGA controller in any way, not just that it
    doesn't happen to arm it today."""

    def __getattr__(self, name):
        raise AssertionError(f"preview touched .fpga.{name} -- it must never reference the FPGA")

    def __call__(self, *a, **k):
        raise AssertionError("preview called .fpga(...) -- it must never reference the FPGA")


def test_preview_never_touches_the_fpga_and_shows_the_true_z_piezo_staircase(app, window):
    import numpy as np

    w = window
    real_fpga = w.fpga
    w.fpga = _FpgaLandmine()                    # any real FPGA reference would now raise
    try:
        w.exposure_spin.setValue(100.0)
        w.mode_combo.setCurrentText(mw.MODE_ZSTACK)
        w.z_start_spin.setValue(0); w.z_end_spin.setValue(4); w.z_interval_spin.setValue(1)   # 5 slices
        pump(app, 0.05)
        w.logs.clear()

        # The real path: selecting Simulated in the Source combo (no separate
        # button) emits preview_requested; must not raise -- the landmine
        # would catch it.
        w.scope_panel.source_combo.setCurrentText("Simulated")

        assert w.scope_panel.source_combo.currentText() == "Simulated"
        assert any("no voltage sent to the FPGA" in m for m in w.logs)
        snap = w.scope_panel._preview_snap
        assert snap is not None and len(snap.frames) > 0

        zpiezo_counts = snap.frames[:, 10].astype(np.int64)     # column 10 = Z Piezo (AO), fpga_scope.md
        assert zpiezo_counts.min() != zpiezo_counts.max(), "Z piezo must show a real staircase, not 0 V"
        n_levels = len(np.unique(zpiezo_counts))
        assert n_levels >= 5, f"expected close to 5 distinct Z piezo levels (one per slice), got {n_levels}"

        xg_counts = snap.frames[:, 8].astype(np.int64)          # X Galvo (AO): the fast-axis sweep
        assert xg_counts.min() != xg_counts.max()
    finally:
        w.fpga = real_fpga                       # restore before the window fixture's own teardown
