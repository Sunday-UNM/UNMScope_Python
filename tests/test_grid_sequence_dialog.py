"""GridSequenceDialog: Start seeded from the stage once at construction
(never again on its own -- see below), the Absolute/Relative toggle
swapping which of Range/End is the user's input, negative/reverse-direction
ranges actually stepping, and the dialog's own preview clearing when the
shared sequence is emptied elsewhere.

User reports this covers, in order:
- 2026-09-23: "it starts from a different position than the actual
  starting position" -> seed Start from the stage (originally done from a
  showEvent override, re-seeding on every re-show).
- 2026-09-23: "the absolute and relative option are also meant to be
  different"; "the set current position button no longer works in the
  relative option"; "the Y coordinate remained unchanged... even though
  the start and end values for the Y coordinate are different"; "once a
  grid sequence is generated it never gets deleted even after clicking
  remove all".
- 2026-09-24: "The button, set current position, should only alter the
  start position when clicked. The start position coordinates should not
  be affected by entering the end position manually" -- the showEvent
  re-seed from the first bullet was itself the bug: reopening the dialog
  after editing Start/End silently overwrote Start with whatever the
  stage was at NOW. Moved the seed to construction time only; Current
  Position is the only thing that touches Start after that.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from unmscope.fileio.stage_locations import LocationSequence
from unmscope.gui.grid_sequence_dialog import GridSequenceDialog, generate_grid


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def dlg(app, tmp_path):
    pos = {"xyz": (100.0, 200.0, 5.0)}
    seq = LocationSequence(tmp_path / "sequence.txt")
    d = GridSequenceDialog(
        current_xyz_provider=lambda: pos["xyz"],
        sequence=seq,
        sequence_changed=None,
        log=lambda msg: None,
    )
    d._pos = pos     # let tests move the "stage"
    yield d
    d.hide()


def test_generate_grid_first_point_is_exactly_start():
    points = generate_grid((10.0, 20.0, 0.0), (50.0, 0.0, 0.0), (10.0, 10.0, 1.0), 0.0)
    assert points[0] == (10.0, 20.0, 0.0)


def test_generate_grid_steps_in_the_negative_direction_too():
    """A negative range (End before Start, or a typed-negative Relative
    Range) must still step -- it used to fail the stopping test on its
    very first point and silently collapse to one, unmoving point."""
    points = generate_grid((0.0, 0.0, 0.0), (0.0, -240.0, 0.0), (100.0, 100.0, 1.0), 20.0)
    ys = sorted({p[1] for p in points})
    assert ys[0] < 0.0, "the axis with a negative range never moved"
    assert len(ys) > 1


def test_start_is_seeded_from_the_stage_at_construction(dlg):
    assert [s.value() for s in dlg._start] == [100.0, 200.0, 5.0]


def test_start_is_not_touched_by_reshowing_or_editing_end(dlg):
    """FIXED 2026-09-24 (user: "the set current position button... should
    only alter the start position when clicked" / "the start position
    coordinates should not be affected by entering the end position
    manually"): Start used to re-seed from the live stage on every show(),
    so hiding and reopening the dialog after editing Start/End silently
    overwrote Start with wherever the stage was at NOW. Neither reshowing
    nor typing an End value may change Start any more -- only clicking
    Current Position (i.e. calling _on_current_position) may."""
    dlg._start[0].setValue(555.0)
    dlg.show()
    assert dlg._start[0].value() == pytest.approx(555.0)

    dlg.hide()
    dlg._pos["xyz"] = (300.0, 400.0, 7.0)      # the stage moved while closed
    dlg.show()
    assert dlg._start[0].value() == pytest.approx(555.0), \
        "Start changed on its own just from reshowing the dialog"

    dlg._mid[0].setValue(dlg._start[0].value() + 40.0)   # type an End
    assert dlg._start[0].value() == pytest.approx(555.0), \
        "Start changed from typing an End value"

    dlg._on_current_position()                  # only this should move it
    assert dlg._start[0].value() == pytest.approx(300.0)


def test_current_position_button_works_in_relative_mode_too(dlg):
    dlg.show()
    dlg._relative_btn.setChecked(True)
    dlg._pos["xyz"] = (111.0, 222.0, 3.0)
    dlg._on_current_position()
    assert [s.value() for s in dlg._start] == [111.0, 222.0, 3.0]


def test_absolute_mode_end_is_input_range_is_computed(dlg):
    dlg.show()
    assert not dlg._relative_btn.isChecked()          # default
    assert dlg._mid_label.text() == "End"
    assert dlg._right_label.text() == "Range (Calc)"
    assert not dlg._mid[0].isReadOnly()               # End: editable
    assert dlg._right[0].isReadOnly()                 # Range: computed

    start = dlg._start[0].value()
    dlg._mid[0].setValue(start + 40.0)                # type an End
    assert dlg._right[0].value() == pytest.approx(40.0)   # Range = End - Start


def test_relative_mode_range_is_input_end_is_computed(dlg):
    dlg.show()
    start = dlg._start[0].value()
    dlg._relative_btn.setChecked(True)
    assert dlg._relative_btn.text() == "Relative"
    assert dlg._mid_label.text() == "Range"
    assert dlg._right_label.text() == "End (Calc)"
    assert not dlg._mid[0].isReadOnly()                # Range: editable
    assert dlg._right[0].isReadOnly()                  # End: computed
    assert dlg._start[0].value() == pytest.approx(start)   # toggling never touches Start

    dlg._mid[0].setValue(25.0)                         # type a Range
    assert dlg._right[0].value() == pytest.approx(start + 25.0)   # End = Start + Range


def test_generate_uses_the_real_start_in_both_modes(dlg):
    """Neither mode adds the current stage position a second time on top
    of Start -- Start already IS the real coordinate in both."""
    dlg._pos["xyz"] = (100.0, 200.0, 5.0)
    dlg.show()
    for s in dlg._tile:
        s.setValue(10.0)
    dlg._relative_btn.setChecked(True)
    for s in dlg._mid:
        s.setValue(0.0)                                 # Range = 0 on every axis
    dlg._on_generate()
    assert dlg._grid_points[0] == pytest.approx((100.0, 200.0, 5.0))


def test_remove_all_elsewhere_clears_the_stale_preview(dlg):
    """A Remove All done in Sample Stage Control's Location Sequence panel
    (i.e. anything that empties the shared LocationSequence) must clear
    this dialog's own generated preview too, or Set Sequence would
    silently resurrect exactly what was just removed."""
    dlg.show()
    for s in dlg._tile:
        s.setValue(10.0)
    dlg._on_generate()
    assert dlg._grid_points and dlg._table.rowCount() > 0

    dlg._sequence.remove_all()

    assert dlg._grid_points == []
    assert dlg._table.rowCount() == 0
    assert dlg._npts_label.text() == "0"
    assert not dlg._set_btn.isEnabled()
