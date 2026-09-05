"""The um/V calibration panel -- LouisXIV's ``GUI/Microns per Volt Settings
GUI.vi`` (SPIM MAIN.vi Utilities button "Edit um/V Cal", event case [31],
which launches that VI as an independent window). It is a plain ``QWidget``
the main window hosts in its own non-modal ``QWidget(Qt.Window)``, sized to
the panel's own ``minimumSize()`` and opened from the Utilities grid (a
left tab was tried first and dropped on the user's 2026-09-05 call).

Behaviour, frame for frame (hidden-frame renders of the VI's state machine):
- "Initialize Variables": read the cluster from the constants VI (the ini)
  into the fields -- :meth:`CalibrationTab.reload`.
- "Update Values" (pushed by event [1], any cluster edit): the indicator
  ``Galvo Pos um/Galvo Pos Volt`` = X um/V x cmd V/pos V. Here: every
  ``editingFinished`` recomputes it; nothing is written until Save.
- Event [0] "Save": write all nine values to the ini section, then
  ``Send Message to GUI "Force Waveform Recalc"`` (SPIM MAIN then pushes
  "Calculate Waveforms" with Force Recalc = TRUE). Here: :meth:`save` writes
  the UNMScope-owned ini copy and emits :attr:`saved` with the reloaded
  ``Calibration`` for the window to hand to the waveform builder.
- Event [2] "Close": FP.Close. Meaningless in a tab -- DROPPED. In its place
  (same measured rect) sits "Revert", an ADDITION: reload from disk, since a
  tab has no dialog lifecycle to discard edits with.
- "Property Nodes": an empty frame (no-op). "Error"/"Invalid State": the
  state machine's error paths; an ini write failure is logged here
  (LouisXIV's Save frame does not even wire the error line).

Layout: MEASURED with PIL scans on the COM render
``VI_Diagrams/SPIM/SPIM LV8.6 VIs/GUI/Microns per Volt Settings GUI/
Microns per Volt Settings GUIp.png`` (1901 x 593; the print is the whole
panel including the connector-pane error clusters far right, which are not
part of the window). Rects below are render coordinates; the page origin is
``OX, OY`` -- ASSUMED, since the VI's window bounds are not in the print:
x is the pane edge (x = 0 of the render is the 1 px pane border), y puts the
cluster label 4 px below the page top. Colours measured: page white, field
bevel (170,170,170), cluster bevel (221,221,221)/(204,204,204), indicator
fill (221,221,221), multiply triangle (255,255,204).

All nine fields are editable and saved, as in LouisXIV; which of them the
Python scan consumes today is in each field's tooltip and in
docs/um_per_volt_calibration.md (Y galvo, XTile, SamplePiezo, Z Piezo2 and
the position ratio are stored for parity only).
"""
from __future__ import annotations

import math
from typing import Callable

from dataclasses import replace
from PySide6.QtCore import QEvent, QRegularExpression, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygon, QRegularExpressionValidator, QValidator
from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton, QWidget
from PySide6.QtCore import QPoint

from unmscope.config.um_per_volt import (
    MicronsToVolt, ensure_unmscope_ini, load_calibration_from_unmscope_ini,
)

#: Page origin in the render (ASSUMED, see module docstring).
OX, OY = 0, 320

FIELD_BEVEL = "#aaaaaa"       # (170,170,170)
BEVEL_LIGHT = "#dddddd"       # (221,221,221)
BEVEL_MID = "#cccccc"         # (204,204,204)
INDICATOR_FILL = "#dddddd"    # (221,221,221)
TRIANGLE_FILL = QColor(255, 255, 204)
PAGE_BG = "#ffffff"

ROW_PITCH = 20
N_ROWS = 9
# Render rects (x, y, w, h), all MEASURED.
CLUSTER_LABEL = (0, 322, 170, 16)          # text bbox x0..126, y324..337
CLUSTER_FRAME = (1, 342, 251, 186)         # bevel: (221) x1 / y342..343, (204) x2..3 / y344..345
FIELD0 = (4, 346, 75, 20)                  # (170) box x4..78, y346..365; white 73x18 at (5,347)
ROW_LABEL0 = (82, 346, 168, 20)            # row-0 text starts x82, y351..359
DECORATION = (250, 353, 64, 23)            # bracket lines, x triangle, '=' : x250..313, y353..375
INDICATOR = (314, 353, 72, 22)             # (170) box; fill (221) 70x20 at (315,354)
INDICATOR_LABEL = (315, 336, 150, 16)      # text x316..455, y340..348
SAVE_BTN = (106, 558, 85, 34)              # outer bevel x106..190, y558..591
CLOSE_BTN = (238, 558, 85, 34)             # outer bevel x238..322 (Revert takes this slot)

MIN_WIDTH, MIN_HEIGHT = INDICATOR_LABEL[0] + INDICATOR_LABEL[2] + 1 - OX, SAVE_BTN[1] + SAVE_BTN[3] + 8 - OY

_FLOAT_RE = QRegularExpression(r"^[+-]?((\d+\.?\d*|\.\d+)([eE][+-]?\d+)?|[Nn][Aa][Nn]|[Ii][Nn][Ff])$")


def _rect(x: int, y: int, w: int, h: int) -> tuple[int, int, int, int]:
    return (x - OX, y - OY, w, h)


def format_value(v: float) -> str:
    """LabVIEW's default numeric display (6 significant digits, no trailing
    zeros): 2000.000000 -> '2000', 0.5 -> '0.5', NaN -> 'NaN'."""
    if math.isnan(v):
        return "NaN"
    if math.isinf(v):
        return "Inf" if v > 0 else "-Inf"
    return f"{v:.6g}"


class _ClusterFrame(QWidget):
    """The cluster's raised bevel: only its top and left edges are visible on
    the white page (the light sides vanish), drawn exactly as measured."""

    def paintEvent(self, ev):
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(PAGE_BG))
        light, mid = QColor(BEVEL_LIGHT), QColor(BEVEL_MID)
        p.fillRect(0, 0, w, 2, light)           # y342..343
        p.fillRect(0, 0, 1, h, light)           # x1
        p.fillRect(1, 2, w - 4, 2, mid)         # y344..345, x2..248
        p.fillRect(1, 2, 2, h - 3, mid)         # x2..3, y344..525
        p.end()


class _Decoration(QWidget):
    """The 'x' / '=' decoration between rows 0-1 and the derived indicator:
    two bracket lines from the fields, a vertical bar, the multiply triangle
    with a small x, and the equals sign. Local coordinates = render - (250,353)."""

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(PAGE_BG))
        black = QPen(QColor(0, 0, 0)); black.setWidth(1)
        p.setPen(black)
        p.drawLine(0, 2, 26, 2)                 # y355, x250..276
        p.drawLine(0, 21, 26, 21)               # y374
        p.setPen(QPen(QColor(51, 51, 51)))
        p.drawLine(27, 1, 27, 21)               # x277, y354..374
        # Triangle: fill (255,255,204) occupies x278..295 x y355..373 (apex at
        # x=295/296, y=364); its dark slanted edges lie just outside the fill.
        p.setPen(Qt.NoPen)
        p.setBrush(TRIANGLE_FILL)
        p.drawPolygon(QPolygon([QPoint(28, 2), QPoint(28, 21), QPoint(46, 11), QPoint(46, 12)]))
        p.setPen(QPen(QColor(68, 68, 68)))
        p.drawLine(29, 1, 47, 10)               # upper edge (279,354) -> (297,363)
        p.drawLine(29, 21, 47, 12)              # lower edge (279,374) -> (297,365)
        p.drawLine(31, 9, 36, 13)               # the small x: x281..286, y362..366
        p.drawLine(36, 9, 31, 13)
        p.setPen(black)
        p.drawLine(54, 9, 57, 9)                # '=' bars: x304..307 at y362 and y365
        p.drawLine(54, 12, 57, 12)
        p.end()


class CalibrationTab(QWidget):
    """See the module docstring.

    ``ini_path``: the UNMScope-owned ini copy (default: created from
    LouisXIV's file on first use). ``log`` receives one-line status
    messages. Signals: :attr:`saved` (the reloaded ``Calibration`` after a
    successful Save -- LouisXIV's "Force Waveform Recalc"), :attr:`changed`
    (the edited ``MicronsToVolt`` after any field edit, unsaved).
    """

    saved = Signal(object)        # Calibration
    changed = Signal(object)      # MicronsToVolt (unsaved edits)

    def __init__(self, ini_path=None, log: Callable[[str], None] | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._ini_path = ini_path
        self._log = log or (lambda msg: None)
        self._values = MicronsToVolt()
        self._updating = False
        self.setStyleSheet(f"CalibrationTab {{ background: {PAGE_BG}; }}")
        self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)
        self._build()
        self.reload()

    # -- construction -----------------------------------------------------------
    def _label(self, text: str, rect) -> QLabel:
        lab = QLabel(text, self)
        lab.setGeometry(*rect)
        lab.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        lab.setStyleSheet("background: transparent;")
        return lab

    def _build(self) -> None:
        self.frame = _ClusterFrame(self)
        self.frame.setGeometry(*_rect(*CLUSTER_FRAME))
        self.frame.lower()
        self.cluster_label = self._label("Microns to Volt calibrations", _rect(*CLUSTER_LABEL))

        self.fields: dict[str, QLineEdit] = {}
        self.row_labels: dict[str, QLabel] = {}
        validator = QRegularExpressionValidator(_FLOAT_RE, self)
        x, y0, w, h = FIELD0
        lx, _, lw, lh = ROW_LABEL0
        for k, name in enumerate(MicronsToVolt.field_names()):
            key = MicronsToVolt.key_of(name)
            f = QLineEdit(self)
            f.setGeometry(*_rect(x, y0 + k * ROW_PITCH, w, h))
            f.setStyleSheet(f"QLineEdit {{ border: 1px solid {FIELD_BEVEL}; background: #ffffff; padding: 0 2px; }}")
            f.setValidator(validator)
            f.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            consumer = MicronsToVolt.consumer_of(name)
            f.setToolTip(f"[{'Microns to Volt calibrations'}] {key}\n"
                         + (f"Used by UNMScope: {consumer}" if consumer
                            else "Stored for LouisXIV parity; no UNMScope consumer yet"))
            f.editingFinished.connect(lambda n=name: self._on_field_edited(n))
            f.installEventFilter(self)       # restore the value if the text is left invalid
            self.fields[name] = f
            self.row_labels[name] = self._label(key, _rect(lx, y0 + k * ROW_PITCH, lw, lh))

        self.decoration = _Decoration(self)
        self.decoration.setGeometry(*_rect(*DECORATION))
        self.indicator_label = self._label("Galvo Pos um/Galvo Pos Volt", _rect(*INDICATOR_LABEL))
        self.indicator = QLineEdit(self)
        self.indicator.setGeometry(*_rect(*INDICATOR))
        self.indicator.setReadOnly(True)
        self.indicator.setFocusPolicy(Qt.NoFocus)
        self.indicator.setStyleSheet(f"QLineEdit {{ border: 1px solid {FIELD_BEVEL}; background: {INDICATOR_FILL}; padding: 0 2px; }}")
        self.indicator.setToolTip("Galvo cmd X um/X Volt x Galvo position  cmd V/pos V (HHMI - Microns per Volt.vi)")

        self.save_button = QPushButton("Save", self)
        self.save_button.setGeometry(*_rect(*SAVE_BTN))
        self.save_button.clicked.connect(self.save)
        self.revert_button = QPushButton("Revert", self)
        self.revert_button.setGeometry(*_rect(*CLOSE_BTN))
        self.revert_button.setToolTip("Reload the values from the ini copy (UNMScope addition; LouisXIV has Close here)")
        self.revert_button.clicked.connect(self.reload)

    # -- state -----------------------------------------------------------------
    @property
    def ini_path(self):
        """The ini copy this tab reads and writes (created on first access)."""
        if self._ini_path is None:
            self._ini_path = ensure_unmscope_ini()
        return self._ini_path

    def values(self) -> MicronsToVolt:
        """The cluster as currently shown (edited, possibly unsaved)."""
        return MicronsToVolt(**{n: getattr(self._values, n) for n in MicronsToVolt.field_names()})

    def set_values(self, mtv: MicronsToVolt) -> None:
        """Fill the fields from a cluster and refresh the indicator (the
        "Initialize Variables" + "Update Values" pair)."""
        self._values = replace(mtv)          # our own copy: edits never reach the caller's object
        self._updating = True
        try:
            for name, f in self.fields.items():
                f.setText(format_value(getattr(mtv, name)))
        finally:
            self._updating = False
        self._update_values()

    def reload(self) -> None:
        """"Initialize Variables": read the section from the ini copy."""
        try:
            mtv = MicronsToVolt.read(self.ini_path)
        except Exception as e:                       # unreadable file: keep the defaults
            self._log(f"um/V calibration: could not read {self.ini_path}: {e}")
            mtv = MicronsToVolt()
        self.set_values(mtv)

    def save(self) -> bool:
        """Event [0] "Save": commit any pending edit, write the nine keys into
        the ini copy, emit :attr:`saved` with the reloaded Calibration.
        Returns False (and logs) if the write failed."""
        for name in self.fields:
            self._commit_field(name)
        mtv = self.values()
        try:
            path = mtv.save(self.ini_path)
            cal = load_calibration_from_unmscope_ini(path)
        except Exception as e:
            self._log(f"um/V calibration: save failed: {e}")
            return False
        self._log(f"um/V calibration saved to {path}: "
                  + ", ".join(f"{k}={format_value(v)}" for k, v in mtv.values().items()))
        self.saved.emit(cal)
        return True

    # -- events ----------------------------------------------------------------
    def _commit_field(self, name: str) -> bool:
        """Parse one field into the cluster; an unparsable entry is put back
        to the last good value (LabVIEW's numeric controls cannot hold text)."""
        f = self.fields[name]
        try:
            v = float(f.text().strip())
        except ValueError:
            f.setText(format_value(getattr(self._values, name)))
            return False
        if v != getattr(self._values, name) and not (math.isnan(v) and math.isnan(getattr(self._values, name))):
            setattr(self._values, name, v)
            return True
        return False

    def eventFilter(self, obj, event):
        # Qt emits editingFinished only for Acceptable text; a field left
        # empty or as a lone '-' would otherwise keep showing invalid text
        # while the model kept the old value. LabVIEW keeps the old value.
        if event.type() == QEvent.FocusOut and isinstance(obj, QLineEdit) and obj in self.fields.values():
            v = obj.validator()
            if v is not None and v.validate(obj.text(), 0)[0] != QValidator.Acceptable:
                name = next(n for n, f in self.fields.items() if f is obj)
                obj.setText(format_value(getattr(self._values, name)))
        return super().eventFilter(obj, event)

    def _on_field_edited(self, name: str) -> None:
        if self._updating:
            return
        changed = self._commit_field(name)
        self._update_values()               # event [1] -> "Update Values"
        if changed:
            self.changed.emit(self.values())

    def _update_values(self) -> None:
        """"Update Values": the derived indicator."""
        self.indicator.setText(format_value(self._values.galvo_pos_um_per_pos_v))
