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
def window(app, monkeypatch, tmp_path):
    monkeypatch.setattr(mw.MainWindow, "fpga_controller_factory", FakeFpgaTriggerController)
    monkeypatch.setattr(mw.QMessageBox, "warning", lambda *a, **k: None)
    # Acquire now puts the Save Image dialog up on EVERY run with Save Files
    # ticked, and a modal dialog in a headless test hangs the whole run. This
    # stands in for it, accepting whatever folder the test has already set;
    # the tests that are ABOUT the dialog install their own stub over it.
    def _stub_dialog(parent, **k):
        folder = parent._data_dir or tmp_path
        # Same experiment-folder calculation the real dialog shows, so the
        # Cell counter behaves in tests exactly as it does on the panel.
        return _AcceptedSaveDialog(folder, experiment=parent._next_experiment_name(folder))
    monkeypatch.setattr(mw, "SaveImageDialog", _stub_dialog)
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


def test_simulate_on_fpga_drives_the_aotf_but_still_clamps_the_ao(app, window):
    """User, 2026-09-07 (laser module confirmed off): "Something happened to
    the AOTF channel again. I am not seeing any voltage at those channels."
    A simulate-on-FPGA run forced every AOTF level to 0 V, so the channels
    could never show anything on the live trace. The AO clamp is what stops
    the mirrors moving, and it stays on."""
    w = window
    regs = w.fpga._session.registers
    w.mode_combo.setCurrentText(mw.MODE_CONTINUOUS)
    # 488 nm is the shipped default row; make sure a level is actually asked for
    levels, _desc = w._aotf_levels_for_run()
    assert levels, "the test needs a selected excitation row"
    ch, counts = next(iter(levels.items()))
    pump(app, 0.05)
    w.on_acquire_clicked()
    pump(app, 0.3)

    assert regs["AOTF ch (V)"].read()[f"AOTF ch {ch}"] == counts
    assert regs["AO Limit Max (counts)"].read()["AOTF on?"] is True
    assert w.fpga.ao_clamped, "the AO outputs must still be frozen"

    w.on_acquire_clicked()                                  # Stop
    pump(app, 0.3)
    assert regs["AOTF ch (V)"].read()[f"AOTF ch {ch}"] == 0, "levels must return to 0 V at Stop"


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
    # The stack write now happens on a background thread (2026-09-18 fix for
    # "the GUI takes time to save"); wait for it, not a fixed sleep.
    assert pump(app, 5.0, until=lambda: not w._saving_stack), "stack save did not finish"

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
    assert pump(app, 5.0, until=lambda: not w._saving_stack), "stack save did not finish"
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

    # The write is now backgrounded (2026-09-18 fix for "the GUI takes time
    # to save"), and _save_stack skips a call made while one is still in
    # flight -- wait for each to land before calling the next, same as any
    # real caller would (this test fires them back to back on purpose).
    def save(*a, **k):
        assert pump(app, 5.0, until=lambda: not w._saving_stack), "previous save never finished"
        return w._save_stack(*a, **k)

    p1 = save(stack)
    assert p1 is not None and p1.name == "beads_CH00_000000.tif"

    p2 = save(stack, timepoint=42)
    assert p2.name == "beads_CH00_000042.tif"

    # no position folder while the LouisXIV global is off
    p3 = save(stack, position=0)
    assert p3.parent.name.startswith("Cell"), p3

    w.separate_position_folders = True
    p4 = save(stack, timepoint=1, position=0)
    assert p4.parent.name == "position 1"        # 1-based, as Build Image Path is
    assert p4.name == "beads_CH00_000001.tif"

    p5 = save(stack, position=3)
    assert p5.parent.name == "position 4"


# -- Acquire auto-switches the Waveforms tab to Simulated on Simulate-on-FPGA -----------
# The user's follow-up simplification: Simulate-on-FPGA clamps every AO output to 0 V by
# design, so the real scope is known-uninformative there -- Acquire should just show the
# computed waveform automatically rather than making the user flip Source by hand.

def test_acquire_with_a_simulated_camera_shows_the_computed_waveform_automatically(app, window):
    w = window                                    # fixture already uses the Simulated backend
    assert not w.camera.reacts_to_dio4             # Simulate-on-FPGA: this run
    w.scope_panel.show_live()                     # as if left there from a real-camera look
    w.mode_combo.setCurrentText(mw.MODE_CONTINUOUS)
    pump(app, 0.05)

    w.on_acquire_clicked()
    assert w.acquiring

    assert w.scope_panel._mode == "simulated"     # switched with no user action
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

        # The real path: the compute-and-display half of a Simulate-on-FPGA
        # Acquire, driven without arming anything. Must not raise -- the
        # landmine catches any reference to the FPGA controller at all.
        lx, _period_s, _exposure_s, _sync = w._compute_scan_waveform()
        levels, _desc = w._aotf_levels_for_run()
        w.scope_panel.show_computed_waveform(w._scope_snapshot_from_arm(lx.scan, levels))

        assert w.scope_panel._mode == "simulated"
        snap = w.scope_panel._preview_snap
        assert snap is not None and len(snap.frames) > 0

        zpiezo_counts = snap.frames[:, 10].astype(np.int64)     # column 10 = Z Piezo (AO), fpga_scope.md
        assert zpiezo_counts.min() != zpiezo_counts.max(), "Z piezo must show a real staircase, not 0 V"
        n_levels = len(np.unique(zpiezo_counts))
        assert n_levels >= 5, f"expected close to 5 distinct Z piezo levels (one per slice), got {n_levels}"

        xg_counts = snap.frames[:, 8].astype(np.int64)          # X Galvo (AO): the fast-axis sweep
        assert xg_counts.min() != xg_counts.max()

        # The camera trigger: NOT part of the AO waveform math (the FPGA's
        # own timing logic generates it, a separate mechanism -- see
        # _scope_snapshot_from_waveform's docstring), so without it this
        # view would show every analog channel but never the trigger they
        # are meant to be in step with. Checking that they ARE in step,
        # before touching real hardware, is the whole point.
        from unmscope.hardware.fpga_scope import DIGITAL_THRESHOLD, digital_edges
        wf = w.last_waveform
        dio4 = snap.frames[:, 15]
        # One pulse per slice, each firing exactly at its block's first
        # sample (so the very first has no rising edge before it -- count
        # the falling ones, which are all present).
        _, dio4_falling = digital_edges(dio4)
        assert len(dio4_falling) == wf.n_slices, f"expected one camera trigger per slice, got {len(dio4_falling)}"
        for k in range(wf.n_slices):
            assert dio4[k * wf.points_per_trigger] > DIGITAL_THRESHOLD, f"no trigger at the start of slice {k}"
        assert dio4[wf.points_per_trigger // 2] <= DIGITAL_THRESHOLD, "the pulse is short, not the whole block"
        # Int Sync is deliberately not modeled (armed longer than the whole
        # block by design, so it would just be a permanently-high line).
        assert snap.frames[:, 14].max() == 0

        # The AO traces are the ARM PAYLOAD replayed, not a second model of
        # it: unpacking the very words start_free_run() would DMA has to
        # give back exactly what is drawn, slot for slot.
        from unmscope.hardware.waveform import unpack_words
        streamed = unpack_words(wf.words)
        for name, col in mw.MainWindow.AO_STREAM_COLUMNS.items():
            assert np.array_equal(snap.frames[:, col], streamed[name][:len(snap.frames)]), name

        # The laser: its level, BLANKED through the galvo's return move. The
        # blanking is unconditional -- it happens at AOTF cycle = None, which
        # is what this rig runs. Settled 2026-09-08 against LouisXIV's own
        # exported Full Waveform, whose AOTF Ch2 column is binary and notches
        # once per slice (60 high / 9 low at the rig's settings). An earlier
        # reading of its X Waveform graph said "steady level"; that graph
        # shows one cycle at ~87 % duty pinned to the top of its axis, so the
        # notch did not render.
        levels, _desc = w._aotf_levels_for_run()
        assert levels, "the fixture selects one excitation row, so a level must be computed"
        assert w.utilities_tab.waveform_panel.config().aotf_cycle == "None"
        line = w.last_louisxiv.line if hasattr(w, "last_louisxiv") else None
        for ch, counts in levels.items():
            col = mw.MainWindow.AOTF_LEVEL_COLUMNS[ch]
            trace = snap.frames[:, col].astype(np.int64)
            assert counts != 0
            assert set(np.unique(trace)) == {0, counts}, "the AOTF gate is a square: level or nothing"
            # High through the sweep, low through the return move, once per
            # slice -- the same shape LouisXIV exports.
            per_block = trace[:wf.points_per_trigger]
            assert per_block[0] == counts, "the line starts with the laser on"
            assert per_block[-1] == 0, "the return move is blanked"
            n_high = int((per_block == counts).sum())
            assert 0 < n_high < wf.points_per_trigger, "must blank, but not the whole line"
            if line is not None:
                assert n_high == line.on_points
            # Every slice gets the same gate.
            for k in range(1, wf.n_slices):
                blk = trace[k * wf.points_per_trigger:(k + 1) * wf.points_per_trigger]
                assert np.array_equal(blk, per_block), f"slice {k} gate differs"
    finally:
        w.fpga = real_fpga                       # restore before the window fixture's own teardown


# -- the operator decides which AOTF channels are driven (2026-09-07) ------
# "let me make the decision on if the voltage on AOTFs should be forced to
# zero ... From here on if any of the AOTF is checked please throw the
# voltage at the appropriate channel." LouisXIV refuses to start unless
# exactly one Excitation row is on; that is now a log line, not a block.

def test_two_excitation_rows_drive_two_aotf_channels(app, window):
    w = window
    regs = w.fpga._session.registers
    for chk, _wl, spin in w.excitation_rows:
        chk.setChecked(False)
    w.excitation_rows[0][0].setChecked(True); w.excitation_rows[0][2].setValue(50.0)   # 637
    w.excitation_rows[2][0].setChecked(True); w.excitation_rows[2][2].setValue(25.0)   # 488
    levels, _desc = w._aotf_levels_for_run()
    assert len(levels) == 2, "both ticked rows must produce a level"

    w.mode_combo.setCurrentText(mw.MODE_CONTINUOUS)
    pump(app, 0.05)
    w.on_acquire_clicked()
    pump(app, 0.3)
    assert w.acquiring, "two lasers ticked must no longer refuse to start"
    written = regs["AOTF ch (V)"].read()
    for ch, counts in levels.items():
        assert written[f"AOTF ch {ch}"] == counts
    w.on_acquire_clicked()
    pump(app, 0.3)


def test_no_excitation_row_is_a_legitimate_dark_run(app, window):
    """Checking waveforms with every laser off must not be blocked either."""
    w = window
    for chk, _wl, _spin in w.excitation_rows:
        chk.setChecked(False)
    w.mode_combo.setCurrentText(mw.MODE_CONTINUOUS)
    pump(app, 0.05)
    w.on_acquire_clicked()
    pump(app, 0.3)
    assert w.acquiring
    assert w._aotf_levels_for_run()[0] == {}
    w.on_acquire_clicked()
    pump(app, 0.3)


# -- the save prompt comes first (2026-09-07, user) ------------------------
# "The saving prompt needs to happen first before the actual acquisition
# starts." It used to be asked by _save_stack, after the run had finished,
# so a file dialog appeared over a completed stack and cancelling it threw
# the data away.

class _AcceptedSaveDialog:
    """Stands in for the modal Save Image dialog: OK, with fields filled."""

    def __init__(self, folder, experiment="Cell1"):
        self._folder = folder
        self._experiment = experiment

    def exec(self):
        return mw.QDialog.Accepted

    def date_folder(self):
        return self._folder

    def values(self):
        return {"root": str(self._folder.parent), "user_name": "chitra",
                "cell_type": "celegan", "cell_labeling": "singlestain",
                "date": "260907", "experiment": self._experiment,
                "description": "a description", "path": self._folder / self._experiment}


class _CancelledSaveDialog(_AcceptedSaveDialog):
    def __init__(self):
        super().__init__(None)

    def exec(self):
        return mw.QDialog.Rejected


def test_the_save_path_is_asked_before_anything_is_armed(app, window, monkeypatch, tmp_path):
    w = window
    assert w._data_dir is None
    w.save_files_chk.setChecked(True)

    armed_when_asked = []

    def fake_dialog(*a, **k):
        armed_when_asked.append((w.acquiring, w.camera.is_sequence_running()))
        return _AcceptedSaveDialog(tmp_path)
    monkeypatch.setattr(mw, "SaveImageDialog", fake_dialog)

    # Z stack, not Continuous: 2026-09-18 fix scopes the save prompt (and
    # saving at all) to Z stack only -- see test_save_prompt_is_zstack_only.
    # Widened past the fixture's default 10 slices: the fake FPGA finishes
    # 10 in well under the 0.3 s below, and this test wants to catch it
    # still running to prove the dialog appeared BEFORE the arm.
    w.z_end_spin.setValue(990.0)
    w.mode_combo.setCurrentText(mw.MODE_ZSTACK)
    pump(app, 0.05)
    w.on_acquire_clicked()
    pump(app, 0.3)

    assert armed_when_asked == [(False, False)], "the dialog appeared after the run had started"
    assert w._data_dir == tmp_path
    assert w._save_meta["user_name"] == "chitra"      # the typed fields are kept
    assert w.acquiring
    w.on_acquire_clicked()
    pump(app, 0.3)


def test_cancelling_the_save_prompt_starts_nothing(app, window, monkeypatch):
    w = window
    w.save_files_chk.setChecked(True)
    monkeypatch.setattr(mw, "SaveImageDialog", lambda *a, **k: _CancelledSaveDialog())
    w.mode_combo.setCurrentText(mw.MODE_ZSTACK)
    pump(app, 0.05)
    w.on_acquire_clicked()
    pump(app, 0.2)

    assert not w.acquiring, "cancelling must leave the hardware alone"
    assert not w.camera.is_sequence_running()
    assert any("Acquisition cancelled" in m for m in w.logs)


def test_it_asks_on_every_acquire(app, window, monkeypatch, tmp_path):
    """User, 2026-09-07: "everytime I hit acquire button and the save file is
    checked the software needs to prompt regarding the save options." It used
    to ask once a session, so a second run silently reused the first run's
    cell type and labeling."""
    w = window
    w.save_files_chk.setChecked(True)
    # Z stack, not Continuous (2026-09-18 fix: the save prompt is Z-stack
    # only). Widen it well past the fixture's default 10 slices so the
    # fake FPGA can't self-complete the stack inside a pump() window -- this
    # loop needs to control start/stop itself, same as it did as Continuous.
    w.z_end_spin.setValue(990.0)
    asked = []
    monkeypatch.setattr(mw, "SaveImageDialog",
                        lambda *a, **k: (asked.append(k), _AcceptedSaveDialog(tmp_path))[1])
    w.mode_combo.setCurrentText(mw.MODE_ZSTACK)
    pump(app, 0.05)
    for _ in range(3):
        w.on_acquire_clicked(); pump(app, 0.3)      # start
        assert w.acquiring, "the stack self-completed before the manual stop -- widen it further"
        w.on_acquire_clicked(); pump(app, 0.3)      # stop
    assert len(asked) == 3, f"prompted {len(asked)} times for 3 runs"
    # ...and it opens on the last values, so an unchanged setup is one click
    assert asked[-1]["user_name"] == "chitra" and asked[-1]["cell_type"] == "celegan"


def test_save_prompt_is_zstack_only(app, window, monkeypatch, tmp_path):
    """User, 2026-09-18: "The save file checkbox should only be related to
    z stack, it should have nothing to do with the continuous mode." Nothing
    a Continuous run does was ever actually saved (_on_stack_finished only
    calls _save_stack when z_target_frames is set, which Continuous leaves
    at 0), so the save-location prompt must not appear for it either --
    before this fix it did, and cancelling it could abort a live-view run
    over a save location that would never be used."""
    w = window
    w.save_files_chk.setChecked(True)
    monkeypatch.setattr(mw, "SaveImageDialog",
                        lambda *a, **k: pytest.fail("Save Files + Continuous must not prompt"))

    w.mode_combo.setCurrentText(mw.MODE_CONTINUOUS)
    pump(app, 0.05)
    w.on_acquire_clicked()
    pump(app, 0.3)
    assert w.acquiring, "Continuous must start even with nothing to save"
    w.on_acquire_clicked()
    pump(app, 0.3)


def test_saving_a_stack_afterwards_does_not_re_prompt(app, window, monkeypatch, tmp_path):
    """The post-run paths must not put a modal dialog up over finished data;
    only Acquire asks."""
    w = window
    w._data_dir = tmp_path
    monkeypatch.setattr(mw, "SaveImageDialog",
                        lambda *a, **k: pytest.fail("re-prompted while saving"))
    assert w._ensure_data_dir() == tmp_path


def test_no_prompt_when_save_files_is_off(app, window, monkeypatch):
    """Checking waveforms with nothing being written must not ask."""
    w = window
    w.save_files_chk.setChecked(False)
    monkeypatch.setattr(mw, "SaveImageDialog",
                        lambda *a, **k: pytest.fail("asked with Save Files off"))
    w.mode_combo.setCurrentText(mw.MODE_CONTINUOUS)
    pump(app, 0.05)
    w.on_acquire_clicked()
    pump(app, 0.3)
    assert w.acquiring
    w.on_acquire_clicked()
    pump(app, 0.3)


def test_the_cell_counter_increments_in_the_same_parent_folder(app, window, tmp_path):
    """"increase the Cell counter by one increment if it is the same parent
    folder". The filesystem alone is not enough: a run that writes nothing
    (cancelled, aborted, or a partial stack, which is not kept) leaves no
    folder behind, so a scan would hand the same number out again."""
    w = window
    base = tmp_path / "CHITRA" / "CELEGAN" / "SINGLESTAIN" / "260907"
    base.mkdir(parents=True)
    assert w._next_experiment_name(base) == "Cell1"

    w._data_dir = base
    w.save_files_chk.setChecked(True)
    assert w._prompt_save_image() == base                 # the fixture's stub accepts
    assert w._experiment_dir == base / "Cell1"
    assert w._next_experiment_name(base) == "Cell2", "the counter did not move"

    assert w._prompt_save_image() == base
    assert w._experiment_dir == base / "Cell2"
    assert w._next_experiment_name(base) == "Cell3"


def test_a_folder_already_on_disk_still_wins(app, window, tmp_path):
    """Someone else's Cell7 in the same folder must not be overwritten."""
    w = window
    base = tmp_path / "date"
    (base / "Cell7").mkdir(parents=True)
    assert w._next_experiment_name(base) == "Cell8"


def test_a_different_parent_folder_starts_again_at_cell1(app, window, tmp_path):
    """The counter is per parent -- a different cell type is a different
    folder with its own numbering."""
    w = window
    a, b = tmp_path / "typeA", tmp_path / "typeB"
    a.mkdir(); b.mkdir()
    w._data_dir = a
    w._prompt_save_image()
    assert w._next_experiment_name(a) == "Cell2"
    assert w._next_experiment_name(b) == "Cell1"


def test_the_dialog_opens_on_the_last_values(app, window, monkeypatch, tmp_path):
    """"make the options pre filled out from last time"."""
    w = window
    w._save_meta.update({"root": str(tmp_path), "user_name": "CHITRA", "cell_type": "CELEGAN",
                         "cell_labeling": "SINGLESTAIN", "description": "0.9NA secondary"})
    seen = {}
    monkeypatch.setattr(mw, "SaveImageDialog",
                        lambda *a, **k: (seen.update(k), _AcceptedSaveDialog(tmp_path))[1])
    w._data_dir = None
    w._prompt_save_image()
    assert seen["root"] == str(tmp_path)
    assert seen["user_name"] == "CHITRA" and seen["cell_type"] == "CELEGAN"
    assert seen["cell_labeling"] == "SINGLESTAIN" and seen["description"] == "0.9NA secondary"


def test_ticking_a_laser_row_puts_its_voltage_out_immediately(window, app):
    """User, 2026-09-08: "I want the AOTF mode giving out appropriate voltage
    everytime it is checked in." Before this, the level only reached the card
    at Acquire. Measured on the real PCIe-7852R the same day (spikes/38):
    'AOTF ch (V)' reaches 'AOTF ch out (V)', and the pin, with nothing armed,
    so no run is needed for a ticked row to be live."""
    w = window
    for chk, _wl, _spin in w.excitation_rows:
        chk.setChecked(False)
    pump(app, 0.02)
    assert all(v == 0 for v in w.fpga.aotf_levels.values()), w.fpga.aotf_levels
    assert not w.acquiring                      # nothing armed, on purpose

    chk, wl, spin = w.excitation_rows[2]        # 488 nm -> AOTF ch 2 -> AO7
    assert wl == 488
    spin.setValue(38.5)                         # the rig's own working setting
    chk.setChecked(True)
    pump(app, 0.02)

    # 38.5 % of the ini's 0..5 V AOTF range = 1.925 V; 3276.7 counts/V.
    assert w.fpga.aotf_levels["AOTF ch 2"] == pytest.approx(6308, abs=2)
    assert w.fpga.aotf_levels["AOTF ch 0"] == 0
    assert not w.acquiring                      # still no run

    # Moving the slider/spin alone re-pushes, no re-tick needed.
    spin.setValue(77.0)
    pump(app, 0.02)
    assert w.fpga.aotf_levels["AOTF ch 2"] == pytest.approx(12615, abs=3)

    # Unticking zeroes that channel -- the operator asked for it. Nothing
    # else in the port zeroes an AOTF channel on its own (docs/aotf.md).
    chk.setChecked(False)
    pump(app, 0.02)
    assert w.fpga.aotf_levels["AOTF ch 2"] == 0


def test_more_than_one_row_may_be_live_at_once(window, app):
    """The one-laser rule is logged, not enforced (2026-09-07), so two ticked
    rows drive two AOTF channels."""
    w = window
    for chk, _wl, _spin in w.excitation_rows:
        chk.setChecked(False)
    pump(app, 0.02)
    for i in (1, 2):
        chk, _wl, spin = w.excitation_rows[i]
        spin.setValue(50.0)
        chk.setChecked(True)
    pump(app, 0.02)
    assert w.fpga.aotf_levels["AOTF ch 1"] > 0
    assert w.fpga.aotf_levels["AOTF ch 2"] > 0
