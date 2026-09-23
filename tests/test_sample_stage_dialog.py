"""The Sample Stage Control panel, headless, on the simulated MP-285: the
consumer cases (Go / Set Control Loc. / Set Origin / Save Location / Recall /
Sequence / Remove / Remove All / Save Settings), the greyed parts, and that
it only ever writes the UNMScope copies."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import math
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from unmscope.config import spim_ini
from unmscope.fileio import stage_locations as sl
from unmscope.gui import sample_stage_dialog as ssd
from unmscope.gui.sample_stage_dialog import SampleStageDialog, SaveLocationDialog, WINDOW_H, WINDOW_W
from unmscope.hardware.stage import SimulatedMP285

INI = (b"[SIMP-285 3D Stage]\r\nEnable? = False\r\nCOM Port = \"COM8\"\r\nVelocity (um/s) = 2500\r\n"
       b"Settling Time (ms) = 300\r\nSimulate = FALSE\r\nXYZ Assignment = 5\r\n\r\n"
       b"[Rotation Stage (PI U651) Settings]\r\nEnable? = FALSE\r\nSimulate = FALSE\r\nSerial Number = \"\"\r\n"
       b"Speed (deg/s) = 72\r\nSettling Time (ms) = 100\r\n")
LOCATIONS = (b"42\t1\t1.00\t2.00\t3.00\tNaN\t25.0000\r\n"
             b"41\t3\t50.00\t60.00\t70.00\t0.500000\tNaN\r\n")
SEQUENCE = b"1\t1\t0.00\t0.00\t0.00\tNaN\tNaN\r\n2\t2\t83.20\t0.00\t0.00\tNaN\tNaN\r\n"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def env(tmp_path, monkeypatch):
    """LouisXIV's files in tmp/louisxiv (must stay untouched), the UNMScope
    copies in tmp/home."""
    lx = tmp_path / "louisxiv"; lx.mkdir()
    (lx / "SPIMProject.ini").write_bytes(INI)
    (lx / sl.LOCATIONS_FILENAME).write_bytes(LOCATIONS)
    (lx / sl.SEQUENCE_FILENAME).write_bytes(SEQUENCE)
    home = tmp_path / "home"
    monkeypatch.setattr(spim_ini, "USER_INI", home / "SPIMProject.ini")
    monkeypatch.setattr(spim_ini, "DEFAULT_INI", lx / "SPIMProject.ini")
    monkeypatch.setattr(sl, "USER_DIR", home)
    monkeypatch.setattr(ssd, "DEFAULT_LOCATIONS_FILE", lx / sl.LOCATIONS_FILENAME)
    monkeypatch.setattr(ssd, "DEFAULT_SEQUENCE_FILE", lx / sl.SEQUENCE_FILENAME)
    # ensure_user_ini's default source argument was bound at import; pass it explicitly
    monkeypatch.setattr(ssd, "ensure_user_ini", lambda source=None, dest=None: spim_ini.ensure_user_ini(
        lx / "SPIMProject.ini", dest))
    return lx, home


@pytest.fixture
def dlg(app, env):
    d = SampleStageDialog()
    d._confirm = lambda text: True
    yield d
    d.shutdown()
    d.deleteLater()


def test_first_use_copies_and_defaults(dlg, env):
    lx, home = env
    assert (home / "SPIMProject.ini").read_bytes() == INI
    assert (home / sl.LOCATIONS_FILENAME).read_bytes() == LOCATIONS
    assert (home / sl.SEQUENCE_FILENAME).read_bytes() == SEQUENCE
    assert dlg.size().width() == WINDOW_W and dlg.size().height() == WINDOW_H
    assert isinstance(dlg.stage, SimulatedMP285) and dlg.stage.is_connected
    assert dlg.stage.velocity_um_s == 2500 and dlg.stage.xyz_assignment == 5
    assert dlg.current_location() == (0.0, 0.0, 0.0)
    assert [lab.text() for lab in dlg.readout_values] == ["0.00", "0.00", "0.00"]
    # settings page = Init Controls
    assert not dlg.enable_stage_chk.isChecked() and dlg.com_port_combo.currentText() == "COM8"
    assert dlg.velocity.value() == 2500 and dlg.settling.value() == 300 and not dlg.simulate_chk.isChecked()
    assert dlg.assignment_combo.currentText() == "ZYX"
    assert [dlg.assignment_combo.itemText(i) for i in range(6)] == list(spim_ini.XYZ_ASSIGNMENTS)
    # tables
    assert dlg.saved_table.rowCount() == 2 and dlg.saved_table.item(0, 0).text().endswith("1")
    assert dlg.saved.locked == [True, False]
    assert dlg.sequence_table.rowCount() == 2 and dlg.sequence_table.item(1, 2).text() == "83.20"
    assert dlg.tabs.currentIndex() == 0


def test_greyed_parts(dlg):
    # FIXED 2026-09-22: gen_grid_btn was disabled when this test was written
    # (a9bd88d); the later grid-sequence checkpoint commit (3abed8d) wired
    # it live (opens GridSequenceDialog) and never re-greyed it for this
    # case -- the assertion was stale, not the source. It has no position
    # to grey against until the stage is actually connected, so "always
    # enabled" is correct, not a bug.
    assert dlg.gen_grid_btn.isEnabled()
    assert all(not w.isEnabled() for w in dlg.rotation_controls)
    assert dlg.simulate_switch.isChecked() and not dlg.simulate_switch.isEnabled()
    # nothing selected -> Recall / Sequence / Remove greyed (frame [6] / [16])
    for b in (dlg.recall_btn, dlg.sequence_btn, dlg.remove_btn, dlg.seq_recall_btn, dlg.seq_remove_btn):
        assert not b.isEnabled()
    assert dlg.remove_all_btn.isEnabled() and dlg.seq_remove_all_btn.isEnabled()
    dlg.saved_table.selectRow(1)
    assert dlg.recall_btn.isEnabled() and dlg.sequence_btn.isEnabled() and dlg.remove_btn.isEnabled()


def test_go_set_control_and_origin(dlg):
    dlg.set_control_location((10.0, 20.0, 30.0))
    dlg.wait_for_moves_chk.setChecked(True)
    dlg.on_go()
    assert dlg.current_location() == (10.0, 20.0, 30.0)
    assert [lab.text() for lab in dlg.readout_values] == ["10.00", "20.00", "30.00"]
    assert dlg.stage.get_position_um() == (10.0, 20.0, 30.0)
    dlg.set_control_location((0.0, 0.0, 0.0))
    dlg.on_set_control_location()
    assert dlg.control_location() == (10.0, 20.0, 30.0)
    seen = []
    dlg.position_changed.connect(lambda x, y, z: seen.append((x, y, z)))
    dlg.on_set_origin()
    assert dlg.current_location() == (0.0, 0.0, 0.0) and seen == [(0.0, 0.0, 0.0)]
    dlg._confirm = lambda text: False
    dlg.set_control_location((5.0, 5.0, 5.0)); dlg.on_go()
    dlg.on_set_origin()
    assert dlg.current_location() == (5.0, 5.0, 5.0)          # declined prompt -> no origin change


def test_auto_refresh_shows_the_stage_position(dlg):
    dlg.show()
    dlg.stage.move_absolute_um((1.0, 2.0, 3.0))
    dlg.auto_refresh_chk.setChecked(False)
    dlg._on_timeout()
    assert dlg.current_location() == (0.0, 0.0, 0.0)
    dlg.auto_refresh_chk.setChecked(True)
    dlg._on_timeout()
    assert dlg.current_location() == (1.0, 2.0, 3.0)
    dlg.close()
    assert not dlg.isVisible()                                 # Panel Close -> hidden, not destroyed


def test_save_location_insert_and_update(dlg, env):
    lx, home = env
    dlg.set_control_location((7.0, 8.0, 9.0))
    # Update: same name as the selected row
    dlg.saved_table.selectRow(1)
    dlg.apply_save_dialog(sl.format_location_row("3", 7.0, 8.0, 9.0, None, 1.5), insert=False)
    assert dlg.saved.rows[1] == ["3", "7.00", "8.00", "9.00", "NaN", "1.5000"] and len(dlg.saved) == 2
    # Insert: a new name
    dlg.apply_save_dialog(sl.format_location_row("new", 1.0, 1.0, 1.0, 0.25, None), insert=True)
    assert len(dlg.saved) == 3 and dlg.saved.locked == [True, False, False]
    assert dlg.saved_table.rowCount() == 3 and dlg.saved_table.selectionModel().selectedRows()[0].row() == 2
    assert (home / sl.LOCATIONS_FILENAME).read_bytes() == (
        b"42\t1\t1.00\t2.00\t3.00\tNaN\t25.0000\r\n"
        b"41\t3\t7.00\t8.00\t9.00\tNaN\t1.5000\r\n"
        b"41\tnew\t1.00\t1.00\t1.00\t0.250000\tNaN\r\n")
    assert (lx / sl.LOCATIONS_FILENAME).read_bytes() == LOCATIONS       # LouisXIV's file untouched


def test_save_location_dialog_rules(app):
    d = SaveLocationDialog((1.0, 2.0, 3.0), 25.0, 0.5, name="1")
    assert d.name_note.text() == "" and d.x.value() == 1.0 and d.theta.value() == 25.0 and d.rel.value() == 0.5
    d.name.setText("")
    d.on_ok()
    assert d.name_note.text() == "Please enter a name" and d.row_out is None
    d.name.setText("1")
    assert d.name_note.text() == "Modify to save as new location"
    d.on_ok()
    assert d.row_out == ["1", "1.00", "2.00", "3.00", "NaN", "NaN"] and d.insert is False
    d2 = SaveLocationDialog((1.0, 2.0, 3.0), 25.0, 0.5, name="1")
    d2.name.setText("2"); d2.save_rel.setChecked(True); d2.save_theta.setChecked(True)
    d2.on_ok()
    assert d2.row_out == ["2", "1.00", "2.00", "3.00", "0.500000", "25.0000"] and d2.insert is True


def test_recall_moves_the_stage_and_pushes_rel_offset(dlg):
    offsets = []
    dlg.rel_offset_recalled.connect(offsets.append)
    dlg.saved_table.selectRow(1)                       # "3": 50, 60, 70, rel 0.5, theta NaN
    dlg.wait_for_moves_chk.setChecked(True)
    dlg.on_recall_saved()
    assert dlg.control_location() == (50.0, 60.0, 70.0) and dlg.current_location() == (50.0, 60.0, 70.0)
    assert offsets == [0.5]
    dlg.saved_table.selectRow(0)                       # "1": theta 25 -> rotation position, rel NaN -> nothing
    dlg.on_recall_saved()
    assert dlg.current_location() == (1.0, 2.0, 3.0) and offsets == [0.5]
    assert dlg.rot_position.value() == 25.0
    dlg.sequence_table.selectRow(1)
    dlg.on_recall_sequence()
    assert dlg.current_location() == (83.2, 0.0, 0.0)


def test_sequence_cases_write_the_file_and_signal(dlg, env):
    lx, home = env
    changes = []
    dlg.sequence_changed.connect(lambda: changes.append(1))
    dlg.saved_table.selectRow(1)
    dlg.on_add_to_sequence()
    assert len(dlg.sequence) == 3 and dlg.sequence.rows[2] == ["3", "3", "50.00", "60.00", "70.00", "0.500000", "NaN"]
    assert dlg.sequence_table.rowCount() == 3 and dlg.sequence_table.item(2, 0).text() == "3"
    dlg.sequence_table.selectRow(0)
    dlg.on_remove_sequence()
    assert [r[0] for r in dlg.sequence.rows] == ["1", "2"] and dlg.sequence.rows[0][1] == "2"
    assert (home / sl.SEQUENCE_FILENAME).read_bytes() == (
        b"1\t2\t83.20\t0.00\t0.00\tNaN\tNaN\r\n2\t3\t50.00\t60.00\t70.00\t0.500000\tNaN\r\n")
    dlg.on_sequence_remove_all()
    assert len(dlg.sequence) == 0 and (home / sl.SEQUENCE_FILENAME).read_bytes() == b""
    assert changes == [1, 1, 1]
    assert (lx / sl.SEQUENCE_FILENAME).read_bytes() == SEQUENCE
    assert not dlg.seq_recall_btn.isEnabled()


def test_remove_and_remove_all_keep_locked_rows(dlg):
    dlg.saved_table.selectRow(0)                       # the locked row
    dlg.on_remove_saved()                              # Remove works on locked rows too (LouisXIV: no lock check)
    assert len(dlg.saved) == 1 and dlg.saved.rows[0][0] == "3"
    dlg.toggle_lock(0)
    dlg.on_remove_all_saved()
    assert len(dlg.saved) == 1                         # locked survives Remove All
    dlg.toggle_lock(0)
    dlg.on_remove_all_saved()
    assert len(dlg.saved) == 0 and dlg.saved_table.rowCount() == 0


def test_save_settings_writes_the_copy_and_reinits(dlg, env):
    lx, home = env
    saved = []
    dlg.settings_saved.connect(saved.append)
    dlg.velocity.setValue(1000)
    dlg.settling.setValue(50)
    dlg.assignment_combo.setCurrentIndex(0)
    dlg.enable_stage_chk.setChecked(True)
    dlg.on_save_settings()
    raw = (home / "SPIMProject.ini").read_bytes()
    assert b"Enable? = True\r\nCOM Port = \"COM8\"\r\nVelocity (um/s) = 1000\r\nSettling Time (ms) = 50\r\n" \
           b"Simulate = False\r\nXYZ Assignment = 0\r\n" in raw
    assert b"[Rotation Stage (PI U651) Settings]\r\nEnable? = FALSE" in raw
    assert (lx / "SPIMProject.ini").read_bytes() == INI
    assert saved and saved[0].velocity_um_s == 1000
    # Init HW re-made the (still simulated: no real transport) stage with the new settings
    assert isinstance(dlg.stage, SimulatedMP285) and dlg.stage.velocity_um_s == 1000
    assert dlg.stage.xyz_assignment == 0 and dlg.stage.is_connected


def test_real_factory_is_used_when_simulate_is_off(app, env):
    lx, home = env
    made = []

    class Fake(SimulatedMP285):
        pass

    def factory(settings):
        made.append(settings.com_port)
        return Fake(settings.velocity_um_s, settings.settling_ms, settings.xyz_assignment)

    d = SampleStageDialog(real_stage_factory=factory)
    try:
        assert d.simulate_switch.isEnabled() and isinstance(d.stage, SimulatedMP285) and not made
        d.enable_stage_chk.setChecked(True)
        d.on_save_settings()                       # Enable? True but the switch still says Simulate
        assert not made
        d.simulate_switch.setChecked(False)        # -> Init HW with the real factory
        assert made == ["COM8"] and isinstance(d.stage, Fake)
    finally:
        d.shutdown()
