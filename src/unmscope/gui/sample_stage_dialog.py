"""Sample Stage Control -- LouisXIV's ``Motion/Sample Stage Control (All
Axes)/Sample Stage Control GUI.vi``, the non-modal panel SPIM MAIN opens
from the Utilities button '3D Stage Control' (caption 'Sample Stage
Control') and from Scan Setup's 'Configure' next to 'Multi-location' (both
in event case [4], via Launch Asynch VI; hidden on Exit, not stopped).

Layout: every rectangle is MEASURED (PIL bboxes, ``docs/sample_stage.md``)
on the COM-rendered panel ``VI_Diagrams/.../Sample Stage Control GUI/
Sample Stage Control GUIp.png`` (701 x 805) and the two extra tab pages
rendered 2026-09-05 into ``panel_xyz_settings/`` and ``panel_rotation/``.
Only the top 564 x 159 px area is a tab control; the tables, buttons and
the Simulate? switch below it are common to all three pages. Window
coordinates are the render's; tab-page coordinates are relative to the
page's white area, whose origin is (11, 26) in the render.

Behaviour, event case by consumer case (hidden-frames export of the GUI,
``.../Sample Stage Control GUI/hidden_frames``, 73 frames):

* [19] Refresh -> 'Refresh Position' = SIMP-285 'Query Position' -> Current
  Location; the MP COM Error indicator becomes visible on error.
* [11] Set Control Loc. ('Current to Control Location') copies Current
  Location into Control Location. [1] Go -> 'Validate Position' ("#TODO: add
  range checking") -> 'Set Position' = SIMP-285 'Set Position' with the
  'Wait for Moves' control as Wait for Move (Set Busy around it).
* [2] Set Origin -> "Set current position as origin?" -> SIMP-285 'Set Origin'.
* [8] Save Location ('Save Control Pos') -> Save Location Dialog seeded with
  Control Location, Position (deg) and the selected saved row's name; OK ->
  'Insert Location' (name changed) or 'Update Location' -> 'Save Locations File'.
* [6]/[16] table selection -> Recall / Sequence / Remove enabled iff a row
  0..N-1 is selected. [9] Recall / [10] double-click -> 'Recall Saved': Control
  Location := row XYZ, Z Rel. Offset -> SPIM MAIN ('Write Relative Offset',
  here ``rel_offset_recalled``) unless NaN, theta -> 'Set Rotation Position'
  unless NaN, then 'Validate Position' -> the stage MOVES. A double-click on
  the symbol column is 'Toggle Lock' instead (+ 'Save Locations File').
* [15] Sequence ('Add to Sequence') appends the saved row to the Location
  Sequence (Location Sequence FG Write, then 'Configure Stack' to SPIM MAIN
  = ``sequence_changed``). [5] Remove "Remove selected location?", [18]
  Remove All "Remove ALL unlocked saved locations?" (locked rows survive).
* [13] Sequence Recall / double-click -> 'Recall Sequence' (same as Recall
  Saved, columns shifted by the '#'); [12] "Remove selected location from
  sequence?"; [17] "Remove ALL locations from sequence?".
* [22] Gen. Grid Sequence launches Generate Multipoint Grid Sequence GUI --
  NOT ported (needs the acquisition's image size / Z piezo pixels; multi-
  position acquisition is out of scope for now), greyed.
* [23] Timeout (500 ms): when 'Auto Referesh MP' is on and the queue is
  idle -> 'Refresh Position' (and the rotation equivalent). 'Auto Referesh
  MP' and 'Wait for Moves' are controls that are NOT visible on any of the
  three rendered pages (hidden in LouisXIV); they are shown here in the
  empty band right of Go, position ASSUMED.
  Their default STATE is not assumed: read out of the VI over COM
  (2026-09-05), 'Auto Referesh MP' is TRUE and 'Wait for Moves' is FALSE,
  which is what we default them to. Neither is ini-backed, so those VI
  defaults are the effective ones. The same read gives Stage Velocity 2500,
  Simulate False, Enable Stage False, COM Port ("", 0) -- and Settling Time
  0.0, which the ini's `Settling Time (ms) = 300` overrides at runtime, so
  300 ms is the right default for us and 0 is a dead control default.
* [14] Update Settings ('Save Settings' on the XYZ Stage Settings page) ->
  SIMP-285 INI FG 'Write INI (input)' of Enable Stage / COM Port / Stage
  Velocity / Settling Time / Simulate / XYZ Assignment, then 'Init HW' =
  SIMP-285 'Init' (re-connect). Written to the UNMScope ini copy only.
* Rotation Control page ([3] Go - Rotation -> 'Set Rotation Position', [4]
  Write Config -> 'Set Rotation Speed' + 'Set Rotation Settling Time' (also
  written to the ini), [20]/[21] refresh): the PI U-651 is disabled in the
  ini and not on this rig -> the page is laid out from its render but greyed
  and backed by ``SimulatedRotationStage`` only.
* 'Initialize' hides both COM-error indicators, selects no row, then
  queues Init Controls (ini -> settings controls), Load Locations File, Load
  Sequence File, Refresh Position, Refresh Rotation Status.
* The 'Simulate?' slide switch is the VI's connector-pane input, OR-ed by
  SIMP-285 Module with the ini's Simulate and NOT Enable?; here it picks the
  simulated backend, and stays checked and greyed while no real transport
  factory is supplied (the MP-285 is not connected to this rig).

Dropped (cleanup): the 'error in' / 'error out' clusters at the bottom (VI
plumbing), so the window is 701 x 735 instead of 701 x 805; the path
indicators' folder glyphs; the button icons.
"""
from __future__ import annotations

import math
import queue
import time as _time
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFrame,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QStackedWidget, QTabBar,
    QTableWidget, QTableWidgetItem, QToolButton, QWidget,
)

from unmscope.config.spim_ini import (
    XYZ_ASSIGNMENTS, RotationStageSettings, Simp285Settings, ensure_user_ini, user_ini_path,
)
from unmscope.fileio.stage_locations import (
    DEFAULT_LOCATIONS_FILE, DEFAULT_SEQUENCE_FILE, LocationSequence, SavedLocations,
    ensure_user_copy, format_location_row, user_locations_path, user_sequence_path,
)
from unmscope.hardware.stage import (
    RotationStage, SimulatedMP285, SimulatedRotationStage, StageError, Vec3, XYZStage,
)
from unmscope.hardware.arduino_focus import (
    DEFAULT_STEPS_PER_UM, FocusError, SimulatedArduinoFocus, ZFocusStage,
)
from unmscope.gui.widgets import rect_mapper
from unmscope.gui.grid_sequence_dialog import GridSequenceDialog

# -- measured colours (render, 2026-09-05) ------------------------------------------
PANEL_BG = "#dddddd"        # (221,221,221) window background
PAGE_BG = "#ffffff"         # tab page
READOUT_BG = "#000000"      # Current Location bar
READOUT_GREEN = "#00cc33"   # (0,204,51) digits
TABLE_HEADER = "#cccccc"    # (204,204,204) listbox header + grid lines
TABLE_SELECT = "#0066cc"    # (0,102,204) selected row
FIELD_BORDER = "#cccccc"

#: Tab page origin in the render (white area starts at x=11, y=26).
PX, PY = 11, 26
WINDOW_W, WINDOW_H = 701, 735
TAB_BAR_H = 24          # tab strip y 1..25 (measured: page frame top at 24)

#: The XYZ axis assignment ring's caption on the settings page.
ASSIGNMENT_CAPTION = "Stage XYZ : Image XYZ"

SAVED_HEADERS = ["Name", "X", "Y", "Z", "Z Rel. Off.", "Theta"]
SAVED_WIDTHS = [152, 64, 63, 68, 79, 69]           # measured column pitches
SEQ_HEADERS = ["#", "Name", "X", "Y", "Z", "Z Rel. Off.", "Theta"]
SEQ_WIDTHS = [26, 128, 66, 64, 59, 81, 71]
ROW_H, HEADER_H = 18, 19
LOCK_GLYPH, UNLOCK_GLYPH = "■ ", "□ "   # stand-ins for the lock / unlock item symbols
Z_POLL_MS = 250          # Arduino Z position refresh interval (ms)


#: Render rect -> tab-page rect.
_pg = rect_mapper(PX, PY)


# ---------------------------------------------------------------------------
# Arduino Z focus workers (actor pattern: one thread owns the serial port)
# ---------------------------------------------------------------------------

class _ZConnectWorker(QThread):
    """3 s Arduino boot warmup in a background thread."""
    connected = Signal(object)   # ready ZFocusStage
    error = Signal(str)

    def __init__(self, stage: ZFocusStage, parent=None):
        super().__init__(parent)
        self._stage = stage

    def run(self) -> None:
        try:
            self._stage.connect()
            self.connected.emit(self._stage)
        except FocusError as exc:
            self.error.emit(str(exc))
        except Exception as exc:
            self.error.emit(f"{type(exc).__name__}: {exc}")


class _ZActor(QThread):
    """The ONE thread that owns the Arduino Z serial port.

    Commands arrive via ``send_cmd()`` (a tuple); results leave via Qt signals.
    Polling (GET_POS every Z_POLL_MS ms) happens automatically when idle.
    """

    z_position   = Signal(int)    # periodic GET_POS step count
    z_move_done  = Signal(int)    # MOVE finished: final step count
    z_move_error = Signal(str)    # MOVE or SET_ZERO failed
    z_zero_done  = Signal()       # SET_ZERO finished
    z_poll_error = Signal(str)    # GET_POS failed

    _STOP = object()

    def __init__(self, stage: ZFocusStage, parent=None):
        super().__init__(parent)
        self._stage = stage
        self._q: queue.Queue = queue.Queue()

    def send_cmd(self, cmd) -> None:
        self._q.put(cmd)

    def stop(self) -> None:
        self._q.put(self._STOP)

    def run(self) -> None:
        poll_interval = Z_POLL_MS / 1000.0
        last_poll = _time.monotonic() - poll_interval
        while True:
            cmd = None
            try:
                cmd = self._q.get_nowait()
            except queue.Empty:
                pass
            if cmd is self._STOP:
                break
            if cmd is not None:
                kind = cmd[0]
                if kind == 'move':
                    # cmd[2] (optional): a queue.Queue a non-GUI caller blocks
                    # on for the outcome -- added 2026-09-22 for the multi-
                    # position acquisition sequence controller, which runs on
                    # its own background thread and must never touch
                    # self._stage directly (this actor is its sole owner; see
                    # the class docstring) nor rely on a Qt signal connection
                    # (QueuedConnection delivery needs a running GUI event
                    # loop, which a plain background thread doesn't pump).
                    reply_q = cmd[2] if len(cmd) > 2 else None
                    try:
                        final = self._stage.move_relative_steps(cmd[1])
                        self.z_move_done.emit(final)
                        if reply_q is not None:
                            reply_q.put(('ok', final))
                    except FocusError as exc:
                        self.z_move_error.emit(str(exc))
                        if reply_q is not None:
                            reply_q.put(('error', str(exc)))
                    last_poll = _time.monotonic()
                    continue
                elif kind == 'set_zero':
                    try:
                        self._stage.set_zero()
                        self.z_zero_done.emit()
                    except FocusError as exc:
                        self.z_move_error.emit(str(exc))
                    continue
            now = _time.monotonic()
            if now - last_poll >= poll_interval:
                if self._stage.is_connected:
                    try:
                        steps = self._stage.get_position_steps()
                        self.z_position.emit(steps)
                    except FocusError as exc:
                        self.z_poll_error.emit(str(exc))
                last_poll = now
            else:
                _time.sleep(0.02)


class _XYConnectWorker(QThread):
    """Connect the ASI X/Y stage on a background thread."""
    connected = Signal(object)   # ready XYZStage
    error = Signal(str)

    def __init__(self, stage: XYZStage, parent=None):
        super().__init__(parent)
        self._stage = stage

    def run(self) -> None:
        try:
            self._stage.connect()
            self.connected.emit(self._stage)
        except StageError as exc:
            self.error.emit(str(exc))
        except Exception as exc:
            self.error.emit(f"{type(exc).__name__}: {exc}")


def _label(parent, text, rect, *, bold=False, align=Qt.AlignLeft | Qt.AlignVCenter, color=None) -> QLabel:
    lab = QLabel(text, parent)
    lab.setGeometry(*rect)
    lab.setAlignment(align)
    if bold:
        f = lab.font(); f.setBold(True); lab.setFont(f)
    if color:
        lab.setStyleSheet(f"color: {color}; background: transparent;")
    return lab


def _checkbox(parent, text, rect) -> QCheckBox:
    """A 13 x 13 indicator (LabVIEW's system checkbox) with its text to the right."""
    c = QCheckBox(text, parent)
    c.setGeometry(*rect)
    c.setStyleSheet("QCheckBox::indicator { width: 13px; height: 13px; }")
    return c


def _button(parent, text, rect, slot=None) -> QPushButton:
    b = QPushButton(text, parent)
    b.setGeometry(*rect)
    if slot is not None:
        b.clicked.connect(slot)
    return b


def _bevel(parent, rect, *, sunken=True) -> QFrame:
    f = QFrame(parent)
    f.setGeometry(*rect)
    f.setFrameShape(QFrame.StyledPanel)
    f.setFrameShadow(QFrame.Sunken if sunken else QFrame.Raised)
    f.setLineWidth(1)
    f.lower()
    return f


class _NumericControl(QWidget):
    """A LabVIEW numeric control: rounded 3D frame, increment arrows on the
    left, the field on the right. ``frame`` and ``field`` rects are the
    measured ones (parent coordinates); the arrow buttons are ASSUMED to be
    the 17 px band left of the field (the render's arrows sit at x+6..x+18)."""

    def __init__(self, parent, frame_rect, field_rect, *, decimals=2, lo=-1e9, hi=1e9, step=1.0):
        super().__init__(parent)
        fx, fy, fw, fh = frame_rect
        self.setGeometry(fx, fy, fw, fh)
        self.frame = QFrame(self)
        self.frame.setGeometry(0, 0, fw, fh)
        self.frame.setObjectName("numFrame")
        self.frame.setStyleSheet("QFrame#numFrame { border: 1px solid #cccccc; border-radius: 4px; background: #dddddd; }")
        x, y, w, h = field_rect
        self.spin = QDoubleSpinBox(self)
        self.spin.setGeometry(x - fx, y - fy, w, h)
        self.spin.setDecimals(decimals)
        self.spin.setRange(lo, hi)
        self.spin.setSingleStep(step)
        self.spin.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.spin.setKeyboardTracking(False)
        self.spin.setStyleSheet(f"border: 1px solid {FIELD_BORDER}; background: white;")
        ax = x - fx - 18
        self.up = QToolButton(self); self.up.setArrowType(Qt.UpArrow)
        self.up.setGeometry(ax, y - fy, 17, h // 2)
        self.up.clicked.connect(self.spin.stepUp)
        self.down = QToolButton(self); self.down.setArrowType(Qt.DownArrow)
        self.down.setGeometry(ax, y - fy + h // 2, 17, h - h // 2)
        self.down.clicked.connect(self.spin.stepDown)
        for b in (self.up, self.down):
            b.setAutoRaise(True)
            b.setFocusPolicy(Qt.NoFocus)

    def value(self) -> float:
        return self.spin.value()

    def setValue(self, v: float) -> None:
        self.spin.setValue(v)


class SaveLocationDialog(QDialog):
    """``Motion/3D Stage/Save Location Dialog.vi`` (modal, 327 x 429 render):
    Location (um) X/Y/Z, Z Rel. Offset + 'Save Rel. Offset', Theta (degrees)
    + 'Save Theta', Name, OK, Cancel. OK with an empty name shows 'Please
    enter a name' and stays open; the name note 'Modify to save as new
    location' explains the rule Insert (T) = the name differs from the one
    the dialog was opened with, Update (F) otherwise. The row is formatted
    by ``format_location_row`` (X/Y/Z %.2f, Rel. Offset %.6f or NaN, Theta
    %.4f or NaN). Rects measured on the render (label text tops, field
    faces + 1 px border)."""

    def __init__(self, xyz: Vec3, theta_deg: float, rel_offset_um: float, name: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Save Location")
        self.setFixedSize(327, 429)
        self.setStyleSheet(f"QDialog {{ background: {PANEL_BG}; }}")
        self.incoming_name = name
        self.row_out: list[str] | None = None
        self.insert = False
        _label(self, "Location (um)", (2, 0, 120, 16), bold=True)
        self.x = self._numeric("X", 5, xyz[0])
        self.y = self._numeric("Y", 113, xyz[1])
        self.z = self._numeric("Z", 221, xyz[2])
        _label(self, "Z Rel. Offset", (2, 84, 100, 16))
        self.rel = _NumericControl(self, (2, 100, 92, 37), (24, 110, 61, 19), decimals=6)
        self.rel.setValue(rel_offset_um if not math.isnan(rel_offset_um) else 0.0)
        self.save_rel = QCheckBox("Save Rel. Offset", self)
        self.save_rel.setGeometry(106, 107, 140, 24)
        self.save_rel.setStyleSheet("QCheckBox::indicator { width: 24px; height: 24px; }")
        _label(self, "Theta (degrees)", (2, 144, 110, 16))
        self.theta = _NumericControl(self, (2, 160, 92, 37), (24, 170, 61, 19), decimals=4)
        self.theta.setValue(theta_deg if not math.isnan(theta_deg) else 0.0)
        self.save_theta = QCheckBox("Save Theta", self)
        self.save_theta.setGeometry(107, 165, 120, 24)
        self.save_theta.setStyleSheet("QCheckBox::indicator { width: 24px; height: 24px; }")
        _label(self, "Name", (2, 204, 60, 16))
        name_frame = QFrame(self); name_frame.setGeometry(2, 224, 320, 33); name_frame.setObjectName("numFrame")
        name_frame.setStyleSheet("QFrame#numFrame { border: 1px solid #cccccc; border-radius: 4px; background: #dddddd; }")
        self.name = QLineEdit(name, self)
        self.name.setGeometry(9, 230, 303, 19)
        self.name_note = _label(self, "", (2, 258, 300, 16), color="#cc0000")
        self.name.textChanged.connect(self._on_name_changed)
        _button(self, "OK", (109, 319, 95, 34), self.on_ok)
        _button(self, "Cancel", (217, 319, 95, 34), self.reject)
        self.name.setFocus()

    def _numeric(self, letter, x0, value):
        _label(self, letter, (x0, 24, 14, 14))
        n = _NumericControl(self, (x0, 44, 93, 32), (x0 + 25, 49, 59, 19))
        n.setValue(value)
        return n

    def _on_name_changed(self, text: str) -> None:
        # 'Name note': 'Modify to save as new location' while the name equals
        # the incoming one (the dialog's Name: Value Change handler).
        self.name_note.setText("Modify to save as new location" if text == self.incoming_name and text else "")

    def on_ok(self) -> None:
        name = self.name.text()
        if not name:
            self.name_note.setText("Please enter a name")
            self.name.setFocus()
            return
        self.row_out = format_location_row(
            name, self.x.value(), self.y.value(), self.z.value(),
            self.rel.value() if self.save_rel.isChecked() else None,
            self.theta.value() if self.save_theta.isChecked() else None)
        self.insert = name != self.incoming_name
        self.accept()


class SampleStageDialog(QWidget):
    """The panel. Construct once and ``show()`` it from the Utilities button
    and from Scan Setup's Configure; closing hides it (LouisXIV: FP.State =
    Hidden on Exit).

    ``stage``: an ``XYZStage``; None builds a ``SimulatedMP285`` from the ini
    settings. ``real_stage_factory(settings) -> XYZStage`` is what the
    'Simulate?' switch turns to when unchecked (e.g. an ``MP285Serial`` over
    pyserial); without it the switch stays checked and greyed.
    ``sequence`` is the shared ``LocationSequence`` the acquisition will
    read later. Signals: ``rel_offset_recalled(float)`` (LouisXIV's 'Write
    Relative Offset' to SPIM MAIN), ``sequence_changed()`` ('Configure
    Stack'), ``position_changed(x, y, z)`` (every Current Location update).
    """

    rel_offset_recalled = Signal(float)
    sequence_changed = Signal()
    position_changed = Signal(float, float, float)
    settings_saved = Signal(object)

    AUTO_REFRESH_MS = 500       # the event loop's timeout

    def __init__(self, *, stage: XYZStage | None = None, rotation: RotationStage | None = None,
                 sequence: LocationSequence | None = None, saved: SavedLocations | None = None,
                 settings: Simp285Settings | None = None, ini_path: Path | None = None,
                 real_stage_factory: Callable[[Simp285Settings], XYZStage] | None = None,
                 # --- Arduino Z focus ---
                 z_stage: ZFocusStage | None = None,
                 real_z_factory: Callable[[str], ZFocusStage] | None = None,
                 z_com_port: str = "COM8",
                 z_steps_per_um: float = DEFAULT_STEPS_PER_UM,
                 # ---
                 rel_offset_provider: Callable[[], float] | None = None,
                 log: Callable[[str], None] | None = None, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("Sample Stage Control")
        self.setFixedSize(WINDOW_W, WINDOW_H)
        self.setStyleSheet(f"SampleStageDialog {{ background: {PANEL_BG}; }}")
        self._log = log or (lambda msg: None)
        self._real_factory = real_stage_factory
        self._real_z_factory = real_z_factory
        self._rel_offset_provider = rel_offset_provider or (lambda: 0.0)
        self._busy = False
        self._confirm: Callable[[str], bool] = self._ask
        self._notify: Callable[[str], None] = self._info

        # Z-axis state (Arduino Z focus)
        self._z_steps_per_um: float = float(z_steps_per_um)
        self._current_z_um: float = 0.0
        self._z_actor: _ZActor | None = None
        self._z_connect_worker: _ZConnectWorker | None = None
        self._z_com_port = z_com_port
        self._xy_connect_worker: _XYConnectWorker | None = None
        self._grid_dialog: GridSequenceDialog | None = None

        # settings: the UNMScope ini copy ('Init Controls' reads the register)
        self.ini_path = ini_path if ini_path is not None else ensure_user_ini(dest=user_ini_path())
        self.settings = settings if settings is not None else Simp285Settings.load(self.ini_path)
        self.rotation_settings = RotationStageSettings.load(self.ini_path)
        # X/Y stage (ASI MS-2000 or simulated MP-285 for X/Y; Z always via Arduino)
        self.stage: XYZStage = stage if stage is not None else self._make_stage(self.settings, True)
        # Z stage (Arduino or simulated)
        if z_stage is not None:
            self.z_stage: ZFocusStage = z_stage
        else:
            sim_z = SimulatedArduinoFocus(steps_per_um=z_steps_per_um)
            sim_z.connect()
            self.z_stage = sim_z
        self.rotation: RotationStage = rotation if rotation is not None else SimulatedRotationStage(
            self.rotation_settings.speed_deg_s, self.rotation_settings.settling_ms)
        self.saved = saved if saved is not None else SavedLocations(
            ensure_user_copy(DEFAULT_LOCATIONS_FILE, user_locations_path()))
        self.sequence = sequence if sequence is not None else LocationSequence(
            ensure_user_copy(DEFAULT_SEQUENCE_FILE, user_sequence_path()))
        self.sequence.add_listener(lambda _seq: self._refresh_sequence_table())

        self._build()
        self._start_z_actor()
        self._initialize()
        self._timer = QTimer(self)
        self._timer.setInterval(self.AUTO_REFRESH_MS)
        self._timer.timeout.connect(self._on_timeout)
        self._timer.start()

    # -- construction --------------------------------------------------------------------
    def _build(self) -> None:
        # tab control: strip + pane + three pages (measured: pane (10,24)-(573,182))
        self.tabs = QTabBar(self)
        self.tabs.setGeometry(10, 0, 564, TAB_BAR_H + 2)
        for name in ("XYZ Control", "XYZ Stage Settings", "Rotation Control"):
            self.tabs.addTab(name)
        self.tabs.setStyleSheet(f"QTabBar::tab {{ height: {TAB_BAR_H - 2}px; padding: 0 8px; }}")
        pane = QFrame(self)
        pane.setObjectName("tabPane")
        pane.setGeometry(10, TAB_BAR_H, 564, 183 - TAB_BAR_H)
        pane.setStyleSheet(f"QFrame#tabPane {{ border: 1px solid #aaaaaa; background: {PAGE_BG}; }}")
        self.pages = QStackedWidget(pane)
        self.pages.setGeometry(1, 2, 560, 156)
        self.pages.setStyleSheet(f"background: {PAGE_BG};")
        self.tabs.currentChanged.connect(self.pages.setCurrentIndex)
        self.page_xyz = QWidget(); self.page_settings = QWidget(); self.page_rotation = QWidget()
        for p in (self.page_xyz, self.page_settings, self.page_rotation):
            p.setFixedSize(560, 156)
            self.pages.addWidget(p)
        self._build_xyz_page(self.page_xyz)
        self._build_settings_page(self.page_settings)
        self._build_rotation_page(self.page_rotation)
        self.tabs.setCurrentIndex(0)      # 'First Call?' forces Tab Control = XYZ Control

        # Save Location / Set Origin (right of the tab control)
        self.save_location_btn = _button(self, "Save Location", (582, 98, 118, 34), self.on_save_location)
        self.set_origin_btn = _button(self, "Set Origin", (581, 141, 118, 34), self.on_set_origin)

        # Saved Locations
        _label(self, "Saved Locations", (13, 193, 140, 18), bold=True)
        _bevel(self, (13, 212, 538, 165))
        self.saved_table = self._table(SAVED_HEADERS, SAVED_WIDTHS, (23, 222, 515, 150))
        self.saved_table.itemSelectionChanged.connect(self._update_button_enables)
        self.saved_table.cellDoubleClicked.connect(self._on_saved_double_clicked)
        self.locations_path_label = _label(self, str(self.saved.path), (26, 379, 520, 18))
        self.recall_btn = _button(self, "Recall", (577, 218, 117, 33), self.on_recall_saved)
        self.sequence_btn = _button(self, "Sequence", (577, 262, 117, 33), self.on_add_to_sequence)
        self.remove_btn = _button(self, "Remove", (577, 306, 117, 33), self.on_remove_saved)
        self.remove_all_btn = _button(self, "Remove All", (577, 350, 118, 34), self.on_remove_all_saved)

        # Location Sequence
        _label(self, "Location Sequence", (13, 397, 160, 18), bold=True)
        _bevel(self, (13, 416, 538, 181))
        self.sequence_table = self._table(SEQ_HEADERS, SEQ_WIDTHS, (23, 426, 515, 167))
        self.sequence_table.itemSelectionChanged.connect(self._update_button_enables)
        self.sequence_table.cellDoubleClicked.connect(lambda r, c: self.on_recall_sequence())
        self.sequence_path_label = _label(self, str(self.sequence.path), (29, 605, 520, 18))
        self.seq_recall_btn = _button(self, "Recall", (575, 421, 117, 33), self.on_recall_sequence)
        self.seq_remove_btn = _button(self, "Remove", (575, 469, 117, 33), self.on_remove_sequence)
        self.seq_remove_all_btn = _button(self, "Remove All", (577, 517, 118, 34), self.on_sequence_remove_all)
        self.gen_grid_btn = _button(self, "Gen. Grid Sequence", (577, 566, 118, 34),
                                    self._show_grid_sequence)

        # Simulate? (the VI's connector-pane switch)
        _label(self, "Simulate?", (216, 664, 60, 16))
        self.simulate_switch = QPushButton("Simulated", self)
        self.simulate_switch.setGeometry(214, 686, 55, 27)
        self.simulate_switch.setCheckable(True)
        self.simulate_switch.setChecked(True)
        self.simulate_switch.setEnabled(self._real_factory is not None)
        if self._real_factory is None:
            self.simulate_switch.setToolTip("No serial transport for the MP-285 on this rig: simulated stage only")
        self.simulate_switch.toggled.connect(self._on_simulate_toggled)

    def _build_xyz_page(self, page: QWidget) -> None:
        _label(page, "Current Location (um)", _pg(14, 39, 140, 18), bold=True)
        # Connection status indicators — compact dots right of the title label
        self.asi_ctrl_indicator = _label(page, "● ASI: Simulated", (152, 13, 120, 16))
        self.asi_ctrl_indicator.setStyleSheet("color: #888888; background: transparent;")
        self.z_ctrl_indicator = _label(page, "● Z: Simulated", (284, 13, 100, 16))
        self.z_ctrl_indicator.setStyleSheet("color: #888888; background: transparent;")
        self.readout = QFrame(page)
        self.readout.setGeometry(*_pg(19, 62, 424, 30))
        self.readout.setStyleSheet(f"background: {READOUT_BG};")
        mono = QFont("Consolas", 11); mono.setBold(True)
        self.readout_values: list[QLabel] = []
        for letter, lx, vx in (("X", 14, 35), ("Y", 152, 178), ("Z", 294, 316)):
            lab = _label(self.readout, letter, (lx, 0, 14, 30), color="white"); lab.setFont(mono)
            val = _label(self.readout, "0.00", (vx, 0, 100, 30), color=READOUT_GREEN); val.setFont(mono)
            self.readout_values.append(val)
        self.refresh_btn = _button(page, "Refresh", _pg(450, 62, 118, 34), self.on_refresh)
        _label(page, "Control Location (um)", _pg(14, 101, 140, 18), bold=True)
        self.control_xyz: list[_NumericControl] = []
        for letter, fx in (("X", 20), ("Y", 128), ("Z", 236)):
            _label(page, letter, _pg(fx, 120, 14, 14))
            n = _NumericControl(page, _pg(fx, 138, 93, 35), _pg(fx + 24, 144, 62, 22))
            self.control_xyz.append(n)
        self.set_control_btn = _button(page, "Set Control Loc.", _pg(339, 95, 106, 34), self.on_set_control_location)
        self.go_btn = _button(page, "Go", _pg(340, 137, 106, 34), self.on_go)
        # LouisXIV's hidden controls (see module docstring): positions ASSUMED
        self.auto_refresh_chk = QCheckBox("Auto Refresh", page)
        self.auto_refresh_chk.setGeometry(*_pg(452, 104, 112, 18))
        self.auto_refresh_chk.setChecked(True)
        self.wait_for_moves_chk = QCheckBox("Wait for Moves", page)
        self.wait_for_moves_chk.setGeometry(*_pg(452, 128, 112, 18))
        self.com_error_label = _label(page, "COM ERROR", _pg(452, 154, 112, 18), bold=True, color="#cc0000")
        self.com_error_label.hide()

    def _build_settings_page(self, page: QWidget) -> None:
        # -- row 1: two COM ports side-by-side, Enable/Simulate, XYZ assignment --
        _label(page, "ASI X/Y COM Port", (12, 4, 112, 16))
        frame_asi = QFrame(page); frame_asi.setGeometry(12, 23, 108, 30)
        frame_asi.setObjectName("visaFrameASI")
        frame_asi.setStyleSheet("QFrame#visaFrameASI { border: 1px solid #cccccc; border-radius: 4px; background: white; }")
        self.com_port_combo = QComboBox(page)
        self.com_port_combo.setGeometry(27, 28, 86, 18)
        self.com_port_combo.setEditable(True)
        self.com_port_combo.addItems(["COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8"])

        _label(page, "Arduino Z COM Port", (130, 4, 120, 16))
        frame_ard = QFrame(page); frame_ard.setGeometry(130, 23, 108, 30)
        frame_ard.setObjectName("visaFrameZ")
        frame_ard.setStyleSheet("QFrame#visaFrameZ { border: 1px solid #cccccc; border-radius: 4px; background: white; }")
        self.z_com_port_combo = QComboBox(page)
        self.z_com_port_combo.setGeometry(145, 28, 82, 18)
        self.z_com_port_combo.setEditable(True)
        self.z_com_port_combo.addItems(["COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8"])

        self.enable_stage_chk = _checkbox(page, "Enable Stage", (260, 26, 110, 14))
        self.simulate_chk = _checkbox(page, "Simulate", (260, 44, 90, 14))
        _label(page, "XYZ Assignment", _pg(400, 32, 120, 16))
        self.assignment_combo = QComboBox(page)
        self.assignment_combo.setGeometry(*_pg(400, 51, 116, 21))
        self.assignment_combo.addItems(list(XYZ_ASSIGNMENTS))
        _label(page, ASSIGNMENT_CAPTION, _pg(403, 76, 130, 16))

        # -- row 2: Connect / Disconnect for each stage (status indicator + buttons) --
        # ASI X/Y row
        self.asi_status_indicator = _label(page, "●", (12, 59, 10, 16))
        self.asi_status_indicator.setStyleSheet("color: #888888; background: transparent;")
        self.asi_status_text = _label(page, "Simulated", (24, 59, 78, 16))
        self.asi_connect_btn = _button(page, "Connect", (104, 56, 56, 20), self._on_asi_connect_clicked)
        self.asi_disconnect_btn = _button(page, "Disconnect", (162, 56, 70, 20), self._on_asi_disconnect_clicked)
        # Arduino Z row
        self.z_status_indicator = _label(page, "●", (12, 79, 10, 16))
        self.z_status_indicator.setStyleSheet("color: #888888; background: transparent;")
        self.z_status_text = _label(page, "Simulated", (24, 79, 78, 16))
        self.z_connect_btn = _button(page, "Connect", (104, 76, 56, 20), self._on_z_connect_clicked)
        self.z_disconnect_btn = _button(page, "Disconnect", (162, 76, 70, 20), self._on_z_disconnect_clicked)

        # -- row 3: Stage Velocity, Settling Time, Save Settings (shifted down to make room) --
        _label(page, "Stage Velocity", (14, 102, 90, 16))
        self.velocity = _NumericControl(page, (14, 120, 68, 32), (36, 125, 39, 22), decimals=0, lo=0, hi=3000, step=100)
        _label(page, "um/s", (82, 132, 30, 16))
        _label(page, "Settling Time", (144, 102, 90, 16))
        self.settling = _NumericControl(page, (144, 120, 68, 32), (166, 125, 39, 22), decimals=0, lo=0, hi=100000, step=10)
        _label(page, "ms", (214, 132, 20, 16))
        self.save_settings_btn = _button(page, "Save Settings", (462, 118, 86, 34), self.on_save_settings)

    def _build_rotation_page(self, page: QWidget) -> None:
        # Laid out from the render; greyed: the PI U-651 is disabled in the ini.
        _label(page, "Position (deg)", _pg(40, 36, 90, 16))
        self.rot_position = QDoubleSpinBox(page); self.rot_position.setGeometry(*_pg(39, 54, 60, 22))
        self.rot_go_btn = _button(page, "Go", _pg(133, 49, 106, 34))
        _label(page, "Speed (deg/sec)", _pg(40, 85, 100, 16))
        self.rot_speed = QDoubleSpinBox(page); self.rot_speed.setGeometry(*_pg(39, 103, 60, 22))
        self.rot_write_btn = _button(page, "Write Config", _pg(155, 116, 106, 34))
        _label(page, "Settling Time (ms)", _pg(39, 133, 110, 16))
        self.rot_settling = QDoubleSpinBox(page); self.rot_settling.setGeometry(*_pg(38, 151, 60, 22))
        for s in (self.rot_position, self.rot_speed, self.rot_settling):
            s.setButtonSymbols(QDoubleSpinBox.NoButtons)
            s.setRange(-1e6, 1e6)
            s.setStyleSheet("border: 1px solid #aaaaaa; background: white;")
        self.rot_speed.setValue(self.rotation_settings.speed_deg_s)
        self.rot_settling.setValue(self.rotation_settings.settling_ms)
        _label(page, "Rotation Stage Status", _pg(325, 56, 130, 16))
        box = QFrame(page); box.setGeometry(*_pg(324, 77, 114, 72)); box.setObjectName("rotBox")
        box.setStyleSheet("QFrame#rotBox { border: 1px solid black; background: #dddddd; }")
        self.rot_connected_led = _label(box, "", (4, 7, 23, 13))
        _label(box, "Connected", (26, 4, 80, 16))
        self.rot_degrees = QLineEdit("0.00000", box); self.rot_degrees.setGeometry(4, 25, 59, 22)
        self.rot_degrees.setReadOnly(True)
        _label(box, "Degrees", (66, 28, 45, 16))
        self.rot_moving_led = _label(box, "", (4, 54, 23, 13))
        _label(box, "Moving", (26, 51, 60, 16))
        for led in (self.rot_connected_led, self.rot_moving_led):
            led.setStyleSheet("background: #336633; border: 1px solid #224422; border-radius: 6px;")
        self.rot_auto_refresh_chk = _checkbox(page, "Auto Referesh", _pg(456, 86, 100, 13))  # sic (LouisXIV's caption)
        self.rot_refresh_btn = _button(page, "Refresh", _pg(445, 111, 118, 34))
        self.rot_move_time = QLineEdit("0", page); self.rot_move_time.setGeometry(*_pg(324, 153, 89, 22))
        self.rot_move_time.setReadOnly(True)
        _label(page, "Move Time (s)", _pg(413, 155, 90, 16))
        self.rotation_controls = (self.rot_position, self.rot_go_btn, self.rot_speed, self.rot_write_btn,
                                  self.rot_settling, self.rot_auto_refresh_chk, self.rot_refresh_btn)
        for w in self.rotation_controls:
            w.setEnabled(False)
            w.setToolTip("PI U-651 rotation stage: disabled in SPIMProject.ini and not on this rig")

    def _table(self, headers, widths, rect) -> QTableWidget:
        t = QTableWidget(0, len(headers), self)
        t.setGeometry(*rect)
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.verticalHeader().setDefaultSectionSize(ROW_H)
        hh = t.horizontalHeader()
        hh.setFixedHeight(HEADER_H)
        hh.setSectionResizeMode(QHeaderView.Fixed)
        hh.setHighlightSections(False)
        hh.setStretchLastSection(False)
        for i, w in enumerate(widths):
            t.setColumnWidth(i, w)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setSelectionMode(QAbstractItemView.SingleSelection)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setShowGrid(True)
        t.setFrameShape(QFrame.Box)
        t.setStyleSheet(
            f"QTableWidget {{ gridline-color: {TABLE_HEADER}; background: white; border: 1px solid #777777;"
            f" selection-background-color: {TABLE_SELECT}; selection-color: white; }}"
            f"QHeaderView::section {{ background: {TABLE_HEADER}; border: 0; border-right: 1px solid #ffffff;"
            f" padding-left: 6px; }}")
        return t

    # -- 'Initialize' ---------------------------------------------------------------------
    def _initialize(self) -> None:
        self.com_error_label.hide()
        self._init_controls()
        self.load_locations_file()
        self.load_sequence_file()
        self.refresh_position()
        # Initial connection status (both stages start simulated)
        self._update_asi_status('simulated')
        self._update_z_status('simulated')

    def _init_controls(self) -> None:
        """'Init Controls': SIMP-285 INI FG 'Read Register' -> the settings page."""
        s = self.settings
        self.enable_stage_chk.setChecked(s.enabled)
        self.com_port_combo.setCurrentText(s.com_port)
        self.z_com_port_combo.setCurrentText(self._z_com_port)
        self.velocity.setValue(s.velocity_um_s)
        self.settling.setValue(s.settling_ms)
        self.simulate_chk.setChecked(s.simulate)
        self.assignment_combo.setCurrentIndex(s.xyz_assignment % len(XYZ_ASSIGNMENTS))

    def _make_stage(self, settings: Simp285Settings, simulate_switch: bool) -> XYZStage:
        """SIMP-285 'Init': simulate = ini Simulate OR Simulate? OR NOT Enable?."""
        simulate = settings.simulate or simulate_switch or not settings.enabled or self._real_factory is None
        if simulate:
            stage = SimulatedMP285(settings.velocity_um_s, settings.settling_ms, settings.xyz_assignment)
        else:
            stage = self._real_factory(settings)
        stage.connect()
        return stage

    # -- Z axis (Arduino focus) management ------------------------------------------------
    def _start_z_actor(self) -> None:
        """Create and start the Z serial actor (called after z_stage is ready)."""
        if self._z_actor is not None:
            self._z_actor.stop()
            self._z_actor.wait()
        self._z_actor = _ZActor(self.z_stage, parent=self)
        self._z_actor.z_position.connect(self._on_z_position, Qt.QueuedConnection)
        self._z_actor.z_move_done.connect(self._on_z_move_done, Qt.QueuedConnection)
        self._z_actor.z_move_error.connect(self._on_z_move_error, Qt.QueuedConnection)
        self._z_actor.z_zero_done.connect(self._on_z_zero_done, Qt.QueuedConnection)
        self._z_actor.z_poll_error.connect(self._on_z_poll_error, Qt.QueuedConnection)
        self._z_actor.start()

    def _connect_z_stage(self, com_port: str) -> None:
        """(Re-)connect the Arduino Z stage on a background thread."""
        if self._z_actor is not None:
            self._z_actor.stop()
            self._z_actor.wait()
            self._z_actor = None
        try:
            self.z_stage.disconnect()
        except Exception:
            pass
        if self._real_z_factory is not None:
            self.z_stage = self._real_z_factory(com_port)
        else:
            sim = SimulatedArduinoFocus(steps_per_um=self._z_steps_per_um)
            sim.connect()
            self.z_stage = sim
            self._start_z_actor()
            self._update_z_status('simulated')
            return
        worker = _ZConnectWorker(self.z_stage, parent=self)
        self._z_connect_worker = worker
        worker.connected.connect(self._on_z_connected, Qt.QueuedConnection)
        worker.error.connect(self._on_z_connect_error, Qt.QueuedConnection)
        worker.start()

    def _on_z_connected(self, stage: ZFocusStage) -> None:
        self.z_stage = stage
        self._start_z_actor()
        self._update_z_status('connected')
        self._log("Arduino Z focus connected")

    def _on_z_connect_error(self, msg: str) -> None:
        self._update_z_status('error', f"Error: {msg}")
        self._log(f"Arduino Z focus connect error: {msg}")
        # Fall back to simulated so polling continues
        sim = SimulatedArduinoFocus(steps_per_um=self._z_steps_per_um)
        sim.connect()
        self.z_stage = sim
        self._start_z_actor()

    # -- connection status helpers ---------------------------------------------------
    _STATUS_COLORS = {
        'connected':    "#00cc00",   # green
        'disconnected': "#cc3333",   # red
        'connecting':   "#ff9900",   # amber
        'simulated':    "#888888",   # grey
        'error':        "#cc3333",   # red
    }
    _STATUS_TEXTS = {
        'connected':    "Connected",
        'disconnected': "Not connected",
        'connecting':   "Connecting...",
        'simulated':    "Simulated",
        'error':        "Error",
    }

    def _update_asi_status(self, state: str, detail: str = "") -> None:
        """Update all ASI X/Y connection indicators. state: 'connected'|'disconnected'|
        'connecting'|'simulated'|'error'. Provide detail for error messages."""
        color = self._STATUS_COLORS.get(state, "#888888")
        text = detail if detail else self._STATUS_TEXTS.get(state, state)
        if hasattr(self, 'asi_status_indicator'):
            self.asi_status_indicator.setStyleSheet(f"color: {color}; background: transparent;")
            self.asi_status_text.setText(text)
        if hasattr(self, 'asi_ctrl_indicator'):
            self.asi_ctrl_indicator.setStyleSheet(f"color: {color}; background: transparent;")
            self.asi_ctrl_indicator.setText(f"● ASI: {text}")

    def _update_z_status(self, state: str, detail: str = "") -> None:
        """Update all Arduino Z connection indicators."""
        color = self._STATUS_COLORS.get(state, "#888888")
        text = detail if detail else self._STATUS_TEXTS.get(state, state)
        if hasattr(self, 'z_status_indicator'):
            self.z_status_indicator.setStyleSheet(f"color: {color}; background: transparent;")
            self.z_status_text.setText(text)
        if hasattr(self, 'z_ctrl_indicator'):
            self.z_ctrl_indicator.setStyleSheet(f"color: {color}; background: transparent;")
            self.z_ctrl_indicator.setText(f"● Z: {text}")

    # -- ASI X/Y connect/disconnect buttons -----------------------------------------
    def _on_asi_connect_clicked(self) -> None:
        com_port = self.com_port_combo.currentText().strip()
        if not com_port:
            return
        self._update_asi_status('connecting')
        self._connect_xy_stage(com_port)

    def _on_asi_disconnect_clicked(self) -> None:
        """Force-disconnect ASI stage and fall back to simulated."""
        if self._xy_connect_worker is not None and self._xy_connect_worker.isRunning():
            return  # don't interrupt an in-flight connect
        try:
            self.stage.disconnect()
        except Exception:
            pass
        sim = SimulatedMP285(self.settings.velocity_um_s, self.settings.settling_ms,
                             self.settings.xyz_assignment)
        sim.connect()
        self.stage = sim
        self._update_asi_status('disconnected')

    def _connect_xy_stage(self, com_port: str) -> None:
        """(Re-)connect the ASI X/Y stage on a background thread."""
        if self._real_factory is None:
            self._update_asi_status('simulated')
            return
        # Build a settings snapshot with the target COM port
        s = Simp285Settings()
        s.enabled = True
        s.com_port = com_port
        s.velocity_um_s = self.settings.velocity_um_s
        s.settling_ms = self.settings.settling_ms
        s.simulate = False
        s.xyz_assignment = self.settings.xyz_assignment
        try:
            new_stage = self._real_factory(s)
        except Exception as exc:
            self._update_asi_status('error', f"Init: {exc}")
            self._log(f"ASI init error: {exc}")
            return
        try:
            self.stage.disconnect()
        except Exception:
            pass
        worker = _XYConnectWorker(new_stage, parent=self)
        self._xy_connect_worker = worker
        worker.connected.connect(self._on_xy_connected, Qt.QueuedConnection)
        worker.error.connect(self._on_xy_connect_error, Qt.QueuedConnection)
        worker.start()

    def _on_xy_connected(self, stage: XYZStage) -> None:
        self.stage = stage
        self._update_asi_status('connected')
        self.com_error_label.hide()
        self._log("ASI X/Y stage connected")

    def _on_xy_connect_error(self, msg: str) -> None:
        self._update_asi_status('error', f"Error: {msg}")
        self._log(f"ASI X/Y connect error: {msg}")
        sim = SimulatedMP285(self.settings.velocity_um_s, self.settings.settling_ms,
                             self.settings.xyz_assignment)
        sim.connect()
        self.stage = sim

    # -- Arduino Z connect/disconnect buttons ----------------------------------------
    def _on_z_connect_clicked(self) -> None:
        com_port = self.z_com_port_combo.currentText().strip()
        if not com_port:
            return
        self._z_com_port = com_port
        self._update_z_status('connecting')
        self._connect_z_stage(com_port)

    def _on_z_disconnect_clicked(self) -> None:
        """Force-disconnect Arduino Z and fall back to simulated."""
        if self._z_actor is not None:
            self._z_actor.stop()
            self._z_actor.wait()
            self._z_actor = None
        try:
            self.z_stage.disconnect()
        except Exception:
            pass
        sim = SimulatedArduinoFocus(steps_per_um=self._z_steps_per_um)
        sim.connect()
        self.z_stage = sim
        self._start_z_actor()
        self._update_z_status('disconnected')

    # -- Z position / command slots -------------------------------------------------------
    def _on_z_position(self, steps: int) -> None:
        """Called by _ZActor on each GET_POS poll."""
        self._current_z_um = steps / self._z_steps_per_um
        self._show_position((self._current[0], self._current[1], self._current_z_um))

    def _on_z_move_done(self, steps: int) -> None:
        self._current_z_um = steps / self._z_steps_per_um
        self._show_position((self._current[0], self._current[1], self._current_z_um))

    def _on_z_move_error(self, msg: str) -> None:
        self.com_error_label.show()
        self._log(f"Z focus: {msg}")

    def _on_z_zero_done(self) -> None:
        self._current_z_um = 0.0
        self._show_position((self._current[0], self._current[1], 0.0))

    def _on_z_poll_error(self, msg: str) -> None:
        self._log(f"Z focus poll: {msg}")

    # -- helpers -------------------------------------------------------------------------
    def _ask(self, text: str) -> bool:
        return QMessageBox.question(self, "Sample Stage Control", text,
                                    QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes

    def _info(self, text: str) -> None:
        QMessageBox.warning(self, "Sample Stage Control", text)

    def _selected(self, table: QTableWidget) -> int:
        rows = table.selectionModel().selectedRows() if table.selectionModel() else []
        return rows[0].row() if rows else -1

    def _update_button_enables(self) -> None:
        """[6] / [16]: Enabled iff the selected row is in 0..N-1 (In Range and Coerce)."""
        ok = 0 <= self._selected(self.saved_table) < len(self.saved)
        for b in (self.recall_btn, self.sequence_btn, self.remove_btn):
            b.setEnabled(ok)
        ok2 = 0 <= self._selected(self.sequence_table) < len(self.sequence)
        for b in (self.seq_recall_btn, self.seq_remove_btn):
            b.setEnabled(ok2)

    def control_location(self) -> Vec3:
        return tuple(n.value() for n in self.control_xyz)

    def set_control_location(self, xyz: Vec3) -> None:
        for n, v in zip(self.control_xyz, xyz):
            n.setValue(float(v))

    def current_location(self) -> Vec3:
        return self._current

    def _show_position(self, xyz: Vec3) -> None:
        self._current = tuple(float(v) for v in xyz)
        for lab, v in zip(self.readout_values, self._current):
            lab.setText(f"{v:.2f}")
        self.position_changed.emit(*self._current)

    _current: Vec3 = (0.0, 0.0, 0.0)

    def _busy_call(self, fn: Callable[[], None]) -> None:
        """Set Busy / Unset Busy around a stage operation on the GUI thread."""
        if self._busy:
            return
        self._busy = True
        app = QApplication.instance()
        if app is not None:
            app.setOverrideCursor(Qt.WaitCursor)
        try:
            fn()
        finally:
            if app is not None:
                app.restoreOverrideCursor()
            self._busy = False

    # -- stage cases ------------------------------------------------------------------------
    def refresh_position(self) -> None:
        """'Refresh Position': Query X/Y from the ASI stage; Z comes from the
        cached Arduino Z position (updated continuously by _ZActor)."""
        try:
            pos = self.stage.get_position_um()  # (x, y, 0.0) for ASI
            self.moving = self.stage.is_moving()
            self._show_position((pos[0], pos[1], self._current_z_um))
            self.com_error_label.hide()
        except StageError as e:
            self.com_error_label.show()
            self._log(f"Stage: {e}")

    moving: bool = False

    def on_refresh(self) -> None:
        self._busy_call(self.refresh_position)

    def _on_timeout(self) -> None:
        """[23] Timeout: auto refresh while nothing else is being processed."""
        if self.auto_refresh_chk.isChecked() and not self._busy and self.isVisible():
            self.refresh_position()

    def on_set_control_location(self) -> None:
        """[11] 'Current to Control Location'."""
        self.set_control_location(self._current)

    def on_go(self) -> None:
        """[1] Go -> 'Validate Position' (no range check in LouisXIV either) -> 'Set Position'."""
        self.validate_position(self.control_location())

    def validate_position(self, xyz: Vec3) -> None:
        self._busy_call(lambda: self.set_position(xyz))

    def set_position(self, xyz: Vec3) -> None:
        """'Set Position': X/Y to ASI stage; Z delta to the Arduino Z actor."""
        # Move X/Y via ASI (blocking; typically < 1 s for jog-sized moves)
        try:
            self.stage.move_absolute_um((xyz[0], xyz[1], 0.0),
                                        wait=self.wait_for_moves_chk.isChecked())
            self.moving = self.stage.is_moving()
            pos = self.stage.get_position_um()
            self._show_position((pos[0], pos[1], self._current_z_um))
            self.com_error_label.hide()
        except StageError as e:
            self.com_error_label.show()
            self._log(f"Stage: {e}")
        # Move Z via Arduino actor (non-blocking: move_done signal updates display)
        if self._z_actor is not None and self.z_stage.is_connected:
            delta_um = xyz[2] - self._current_z_um
            delta_steps = int(round(delta_um * self._z_steps_per_um))
            if delta_steps != 0:
                self._z_actor.send_cmd(('move', delta_steps))

    # -- headless, programmatic control (2026-09-22, for the multi-position
    # acquisition sequence controller in main_window.py) -----------------------
    #: ArduinoFocusProtocol.move() itself times out at 35 s (arduino_focus.py);
    #: this is a safety margin above that, not a real expected duration.
    _Z_MOVE_TIMEOUT_S = 40.0

    def begin_sequence_control(self) -> None:
        """Hand exclusive control of the stage to a sequence controller:
        pause Auto Refresh (it polls self.stage on the GUI thread -- a race
        against a background sequence thread also touching self.stage) and
        disable the interactive move controls, so nothing but the sequence
        controller touches self.stage/self.z_stage until end_sequence_control."""
        self._timer.stop()
        for w in (self.go_btn, self.recall_btn, self.seq_recall_btn):
            w.setEnabled(False)

    def end_sequence_control(self) -> None:
        self._timer.start()
        for w in (self.go_btn, self.recall_btn, self.seq_recall_btn):
            w.setEnabled(True)

    def move_to_position_blocking(self, xyz: Vec3, timeout_s: float | None = None) -> None:
        """Move to ``xyz`` and block until BOTH axes have actually arrived,
        for a caller on its own background thread (never a GUI slot -- this
        touches no QWidget, unlike set_position()/on_recall_*, which read
        and write QDoubleSpinBox controls directly).

        Unlike set_position(), this always waits (ignores
        wait_for_moves_chk -- an automated sequence must always confirm
        arrival) and RAISES StageError/FocusError on failure or timeout
        instead of catching and logging: the caller needs a hard yes/no,
        not a log line. Z still goes through _ZActor (the sole owner of
        that serial port, see its class docstring) via a reply queue,
        never called directly.
        """
        self.stage.move_absolute_um((xyz[0], xyz[1], 0.0), wait=True, timeout_s=timeout_s)
        pos = self.stage.get_position_um()
        new_z = self._current_z_um
        if self._z_actor is not None and self.z_stage.is_connected:
            delta_um = xyz[2] - self._current_z_um
            delta_steps = int(round(delta_um * self._z_steps_per_um))
            if delta_steps != 0:
                reply_q: queue.Queue = queue.Queue()
                self._z_actor.send_cmd(('move', delta_steps, reply_q))
                limit = self._Z_MOVE_TIMEOUT_S if timeout_s is None else timeout_s
                try:
                    outcome = reply_q.get(timeout=limit)
                except queue.Empty:
                    raise StageError(f"Z focus move timed out after {limit:.1f} s")
                kind, payload = outcome
                if kind == 'error':
                    raise StageError(f"Z focus move failed: {payload}")
                new_z = payload / self._z_steps_per_um
        self._current_z_um = new_z
        self._current = (pos[0], pos[1], new_z)
        self.position_changed.emit(*self._current)

    def on_set_origin(self) -> None:
        """[2] "Set current position as origin?" -> 'Set Origin' on XY + SET_ZERO on Z."""
        if not self._confirm("Set current position as origin?"):
            return

        def do():
            try:
                self.stage.set_origin()
                pos = self.stage.get_position_um()
                self._show_position((pos[0], pos[1], self._current_z_um))
            except StageError as e:
                self.com_error_label.show()
                self._log(f"Stage: {e}")
        self._busy_call(do)
        # Also zero the Arduino Z (non-blocking via actor)
        if self._z_actor is not None and self.z_stage.is_connected:
            self._z_actor.send_cmd(('set_zero',))

    def _on_simulate_toggled(self, checked: bool) -> None:
        self.simulate_switch.setText("Simulated" if checked else "Real")
        self.init_hw()

    def init_hw(self) -> None:
        """'Init HW': SIMP-285 'Init' with the current settings."""
        try:
            self.stage.disconnect()
        except Exception:
            pass
        try:
            self.stage = self._make_stage(self.settings, self.simulate_switch.isChecked())
            self.com_error_label.hide()
        except (StageError, OSError) as e:
            self._log(f"Stage init failed: {e}; using the simulated stage")
            self.com_error_label.show()
            self.stage = SimulatedMP285(self.settings.velocity_um_s, self.settings.settling_ms,
                                        self.settings.xyz_assignment)
            self.stage.connect()
        is_sim = isinstance(self.stage, SimulatedMP285)
        self._update_asi_status('simulated' if is_sim else 'connected')
        self.refresh_position()

    # -- settings page -----------------------------------------------------------------------
    def on_save_settings(self) -> None:
        """[14] 'Update Settings': write the [SIMP-285 3D Stage] section of the
        UNMScope ini copy, then re-init the X/Y stage and reconnect Arduino Z."""
        s = Simp285Settings()
        s.enabled = self.enable_stage_chk.isChecked()
        s.com_port = self.com_port_combo.currentText().strip()
        s.velocity_um_s = self.velocity.value()
        s.settling_ms = self.settling.value()
        s.simulate = self.simulate_chk.isChecked()
        s.xyz_assignment = self.assignment_combo.currentIndex()
        s.save(self.ini_path)
        self.settings = s
        self.settings_saved.emit(s)
        self._busy_call(self.init_hw)
        # Reconnect the Arduino Z if the COM port changed
        new_z_port = self.z_com_port_combo.currentText().strip()
        if new_z_port and new_z_port != self._z_com_port:
            self._z_com_port = new_z_port
            self._update_z_status('connecting')
            self._connect_z_stage(new_z_port)

    # -- saved locations ----------------------------------------------------------------------
    def load_locations_file(self) -> None:
        """'Load Locations File'."""
        self.saved.load()
        self._refresh_saved_table()

    def _refresh_saved_table(self, select: int = -1) -> None:
        t = self.saved_table
        t.setRowCount(0)
        t.setRowCount(len(self.saved))
        for r, (row, locked) in enumerate(zip(self.saved.rows, self.saved.locked)):
            cells = [(LOCK_GLYPH if locked else UNLOCK_GLYPH) + row[0]] + list(row[1:6])
            for c, text in enumerate(cells):
                t.setItem(r, c, QTableWidgetItem(text))
        t.clearSelection()
        if 0 <= select < len(self.saved):
            t.selectRow(select)
        self._update_button_enables()

    def save_locations_file(self) -> None:
        """'Save Locations File'."""
        self.saved.save()

    def on_save_location(self) -> None:
        """[8] 'Save Control Pos' -> Save Location Dialog -> Insert / Update."""
        sel = self._selected(self.saved_table)
        name = self.saved.rows[sel][0] if 0 <= sel < len(self.saved) else ""
        dlg = SaveLocationDialog(self.control_location(), self.rot_position.value(),
                                 float(self._rel_offset_provider()), name, self)
        if dlg.exec() == QDialog.Accepted and dlg.row_out is not None:
            self.apply_save_dialog(dlg.row_out, dlg.insert)

    def apply_save_dialog(self, row: list[str], insert: bool) -> None:
        """'Insert Location' / 'Update Location' (+ 'Save Locations File')."""
        if insert:
            idx = self.saved.insert(row)
        else:
            idx = self._selected(self.saved_table)
            if idx < 0:
                idx = self.saved.insert(row)
            else:
                self.saved.update(idx, row)
        self.save_locations_file()
        self._refresh_saved_table(idx)

    def _on_saved_double_clicked(self, row: int, column: int) -> None:
        """[10]: a double-click on the symbol column toggles the lock,
        elsewhere it recalls (LouisXIV: MCL Point To Row Column, InSymbol)."""
        if column == 0:
            self.toggle_lock(row)
        else:
            self.on_recall_saved()

    def toggle_lock(self, row: int) -> None:
        """'Toggle Lock' + 'Save Locations File'."""
        if 0 <= row < len(self.saved):
            self.saved.toggle_lock(row)
            self.save_locations_file()
            self._refresh_saved_table(row)

    def on_recall_saved(self) -> None:
        """[9] 'Recall Saved'."""
        sel = self._selected(self.saved_table)
        if not 0 <= sel < len(self.saved):
            return
        self._recall(self.saved.xyz(sel), *self.saved.rel_offset_and_theta(sel))

    def _recall(self, xyz: Vec3, rel_offset: float, theta: float) -> None:
        """'Recall Saved' / 'Recall Sequence': fill Control Location, push the
        Z rel. offset and theta when numeric, then 'Validate Position'."""
        self.set_control_location(xyz)
        if not math.isnan(rel_offset):
            self.rel_offset_recalled.emit(rel_offset)
        if not math.isnan(theta):
            self.rot_position.setValue(theta)
            try:
                self.rotation.move_absolute_deg(theta)
            except StageError:
                pass
        self.validate_position(self.control_location())

    def on_add_to_sequence(self) -> None:
        """[15] 'Add to Sequence'."""
        sel = self._selected(self.saved_table)
        if 0 <= sel < len(self.saved):
            self.sequence.append_saved(self.saved.rows[sel])
            self.sequence_changed.emit()

    def on_remove_saved(self) -> None:
        """[5] "Remove selected location?" -> 'Remove'."""
        sel = self._selected(self.saved_table)
        if 0 <= sel < len(self.saved) and self._confirm("Remove selected location?"):
            self.saved.remove(sel)
            self.save_locations_file()
            self._refresh_saved_table()

    def on_remove_all_saved(self) -> None:
        """[18] "Remove ALL unlocked saved locations?" -> 'Remove All'."""
        if self._confirm("Remove ALL unlocked saved locations?"):
            self.saved.remove_all_unlocked()
            self.save_locations_file()
            self._refresh_saved_table()

    # -- location sequence ------------------------------------------------------------------------
    def load_sequence_file(self) -> None:
        """'Load Sequence File': Location Sequence FG 'Load'."""
        self.sequence.load()
        self._refresh_sequence_table()

    def _refresh_sequence_table(self) -> None:
        """'Refresh Sequence' (also sent by the grid generator)."""
        t = self.sequence_table
        t.setRowCount(0)
        rows = self.sequence.read()
        t.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c in range(len(SEQ_HEADERS)):
                t.setItem(r, c, QTableWidgetItem(row[c] if c < len(row) else ""))
        t.clearSelection()
        self._update_button_enables()

    def on_recall_sequence(self) -> None:
        """[13] 'Recall Sequence'."""
        sel = self._selected(self.sequence_table)
        if not 0 <= sel < len(self.sequence):
            return
        self._recall(self.sequence.xyz(sel), *self.sequence.rel_offset_and_theta(sel))

    def on_remove_sequence(self) -> None:
        """[12] "Remove selected location from sequence?" -> 'Remove Sequence'."""
        sel = self._selected(self.sequence_table)
        if 0 <= sel < len(self.sequence) and self._confirm("Remove selected location from sequence?"):
            self.sequence.remove(sel)
            self.sequence_changed.emit()

    def on_sequence_remove_all(self) -> None:
        """[17] "Remove ALL locations from sequence?" -> 'Sequence Remove All'."""
        if self._confirm("Remove ALL locations from sequence?"):
            self.sequence.remove_all()
            self.sequence_changed.emit()

    # -- Generate Grid Sequence ----------------------------------------------------------------
    def _show_grid_sequence(self) -> None:
        """[22] Gen. Grid Sequence -> opens/raises the GenerateMultipointGridSequenceGUI."""
        if self._grid_dialog is None:
            self._grid_dialog = GridSequenceDialog(
                current_xyz_provider=lambda: self._current,
                sequence=self.sequence,
                sequence_changed=self.sequence_changed,
                log=self._log,
                parent=self,
            )
        self._grid_dialog.show()
        self._grid_dialog.raise_()
        self._grid_dialog.activateWindow()

    # -- window ----------------------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        """[7] Panel Close? -> 'Exit': the panel is hidden, not destroyed."""
        event.ignore()
        self.hide()

    def shutdown(self) -> None:
        """For the owner's exit: stop polling, stop Z actor, release both stages."""
        self._timer.stop()
        if self._z_actor is not None:
            self._z_actor.stop()
            self._z_actor.wait()
            self._z_actor = None
        try:
            self.stage.disconnect()
        except Exception:
            pass
        try:
            self.z_stage.disconnect()
        except Exception:
            pass
