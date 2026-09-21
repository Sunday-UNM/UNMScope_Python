"""Generate Multipoint Grid Sequence GUI -- LouisXIV's
``Motion/3D Stage/Generate Multipoint Grid Sequence GUI.vi``.

The dialog opens from the Sample Stage Control window's "Gen. Grid Sequence"
button.  The user supplies:

* **Start** (X, Y, Z um) — first tile position; "Current Position" fills it
  from the stage; the "Relative" toggle adds the current position at generation
  time rather than storing an absolute value.
* **Range** (X, Y, Z um) — total span to cover (≥ 0).
* **Tile Size** (X, Y, Z um) — physical size of one image tile on the sample.
  In LouisXIV these were computed from ``Image Size X|Y * um/px`` and
  ``Z um/px * # slices``; here they are explicit user inputs because the
  acquisition pipeline is not connected to this dialog.
* **Overlap %** (0–99) — fractional overlap between adjacent tiles expressed as
  a percentage of the tile size.  ``step = size × (1 - overlap/100)``.

The algorithm (from the block diagram annotation):

  Pos[n] = step × n,   while  (range − size) ≥ step × n  (always ≥ 1 point)

  Size_X|Y = Image Size X|Y × um/px   (user-supplied here)
  Size_Z   = Z um/px × # slices       (user-supplied here)

The 3-D grid iterates Z outermost, Y middle, X innermost (matches the 16-point
example in the front-panel render: two X × two Y × four Z = 16 rows).

**Set Sequence** replaces the Location Sequence entirely with the generated
rows (name = "Grid N", rel_offset = NaN, theta = NaN) and fires the
``sequence_changed`` signal so SPIM MAIN's Configure Stack is notified.
"""
from __future__ import annotations

import math
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDoubleSpinBox, QFrame, QHeaderView,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QWidget,
)

from unmscope.fileio.stage_locations import LocationSequence, format_location_row

# ── colours (match Sample Stage Control) ──────────────────────────────────────
_BG      = "#dddddd"
_WHITE   = "#ffffff"
_HEADER  = "#cccccc"
_SELECT  = "#0066cc"
_BORDER  = "#cccccc"

_ROW_H, _HEADER_H = 18, 19


def _lbl(parent, text, rect, *, bold=False):
    lab = QLabel(text, parent)
    lab.setGeometry(*rect)
    lab.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    if bold:
        f = lab.font(); f.setBold(True); lab.setFont(f)
    return lab


def _spin(parent, rect, *, lo=-1e7, hi=1e7, step=1.0, decimals=2, ro=False):
    s = QDoubleSpinBox(parent)
    s.setGeometry(*rect)
    s.setDecimals(decimals)
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setKeyboardTracking(False)
    if ro:
        s.setReadOnly(True)
        s.setButtonSymbols(QDoubleSpinBox.NoButtons)
        s.setStyleSheet(f"background: {_BG}; border: 1px solid {_BORDER};")
    else:
        s.setStyleSheet(f"background: {_WHITE}; border: 1px solid {_BORDER};")
    return s


def _btn(parent, text, rect, slot=None):
    b = QPushButton(text, parent)
    b.setGeometry(*rect)
    if slot is not None:
        b.clicked.connect(slot)
    return b


# ── algorithm ─────────────────────────────────────────────────────────────────

def generate_grid(start_xyz, range_xyz, size_xyz, overlap_pct: float):
    """Return a list of (x, y, z) stage positions for a tiled grid.

    Per axis: step = size × (1 − overlap_pct/100).
    Points: start, start+step, start+2·step, …
    Stop when step×n > range − size (always include at least the start point).
    Order: Z outermost, Y middle, X innermost.
    """
    axis_pts: list[list[float]] = []
    for i in range(3):
        start = float(start_xyz[i])
        rng   = float(range_xyz[i])
        size  = float(size_xyz[i])
        if size <= 0 or overlap_pct >= 100:
            axis_pts.append([start])
            continue
        overlap = size * (overlap_pct / 100.0)
        step = size - overlap
        if step <= 0:
            axis_pts.append([start])
            continue
        pts = [start]
        n = 1
        # LouisXIV: while (range - size) >= step*n  (always at least 1 point)
        while (rng - size) >= step * n - 1e-9:
            pts.append(start + step * n)
            n += 1
        axis_pts.append(pts)

    result: list[tuple[float, float, float]] = []
    for z in axis_pts[2]:
        for y in axis_pts[1]:
            for x in axis_pts[0]:
                result.append((x, y, z))
    return result


# ── dialog ────────────────────────────────────────────────────────────────────

class GridSequenceDialog(QDialog):
    """Generate Multipoint Grid Sequence GUI.

    Parameters
    ----------
    current_xyz_provider:
        Callable that returns the current stage position (x, y, z) in µm.
    sequence:
        Shared :class:`~unmscope.fileio.stage_locations.LocationSequence` that
        "Set Sequence" replaces.
    sequence_changed:
        Qt signal fired after "Set Sequence" (pass the owning dialog's
        ``sequence_changed`` signal here so SPIM MAIN receives Configure Stack).
    log:
        Optional log callback.
    """

    def __init__(self, *,
                 current_xyz_provider: Callable[[], tuple],
                 sequence: LocationSequence,
                 sequence_changed: Signal | None = None,
                 log: Callable[[str], None] | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("Generate Multipoint Grid Sequence")
        self.setFixedSize(780, 570)
        self.setStyleSheet(f"GridSequenceDialog {{ background: {_BG}; }}")
        self._xyz_provider = current_xyz_provider
        self._sequence = sequence
        self._seq_changed = sequence_changed
        self._log = log or (lambda msg: None)
        self._grid_points: list[tuple[float, float, float]] = []
        self._build()

    # ── construction ─────────────────────────────────────────────────────────

    def _build(self) -> None:
        # ── top strip: Start | Relative | Range | End (Calc) ─────────────────
        #   x-ranges: Start 8..135, [Relative 140..220], Range 230..360, End 370..500

        # Start
        _lbl(self, "Start", (8, 6, 50, 16), bold=True)
        self._start: list[QDoubleSpinBox] = []
        for i, letter in enumerate(("X", "Y", "Z")):
            y0 = 26 + i * 26
            _lbl(self, letter, (8, y0 + 3, 12, 16))
            s = _spin(self, (22, y0, 90, 22), lo=-1e7, hi=1e7, step=10.0)
            _lbl(self, "um", (114, y0 + 3, 22, 16))
            self._start.append(s)
        _btn(self, "Current\nPosition", (8, 104, 118, 28), self._on_current_position)

        # Relative toggle
        self._relative_btn = QPushButton("Relative", self)
        self._relative_btn.setGeometry(140, 50, 76, 28)
        self._relative_btn.setCheckable(True)
        self._relative_btn.setChecked(False)
        self._relative_btn.toggled.connect(self._on_relative_toggled)

        # Range
        _lbl(self, "Range", (230, 6, 50, 16), bold=True)
        self._range: list[QDoubleSpinBox] = []
        for i, letter in enumerate(("X", "Y", "Z")):
            y0 = 26 + i * 26
            _lbl(self, letter, (230, y0 + 3, 12, 16))
            s = _spin(self, (244, y0, 90, 22), lo=0, hi=1e7, step=10.0)
            s.valueChanged.connect(self._update_end_calc)
            _lbl(self, "um", (336, y0 + 3, 22, 16))
            self._range.append(s)

        # End (Calc) — read-only
        _lbl(self, "End (Calc)", (370, 6, 80, 16), bold=True)
        self._end_calc: list[QDoubleSpinBox] = []
        for i, letter in enumerate(("X", "Y", "Z")):
            y0 = 26 + i * 26
            _lbl(self, letter, (370, y0 + 3, 12, 16))
            s = _spin(self, (384, y0, 90, 22), ro=True)
            _lbl(self, "um", (476, y0 + 3, 22, 16))
            self._end_calc.append(s)

        # Wire start changes → update end calc
        for s in self._start:
            s.valueChanged.connect(self._update_end_calc)

        # ── separator ────────────────────────────────────────────────────────
        sep = QFrame(self); sep.setGeometry(8, 136, 764, 1)
        sep.setFrameShape(QFrame.HLine); sep.setStyleSheet("border: 1px solid #aaaaaa;")

        # ── left panel: Overlap %, Tile Size, Acq Settings info ──────────────
        _lbl(self, "Overlap %", (8, 144, 70, 16))
        self._overlap = _spin(self, (8, 162, 76, 22), lo=0, hi=99, step=1.0, decimals=1)

        _lbl(self, "Tile Size", (8, 192, 60, 16), bold=True)
        _lbl(self, "(µm per axis)", (8, 208, 100, 14))
        self._tile: list[QDoubleSpinBox] = []
        for i, letter in enumerate(("X", "Y", "Z")):
            y0 = 226 + i * 26
            _lbl(self, letter, (8, y0 + 3, 12, 16))
            s = _spin(self, (22, y0, 90, 22), lo=0.001, hi=1e6, step=10.0)
            s.setValue(100.0)
            _lbl(self, "um", (114, y0 + 3, 22, 16))
            self._tile.append(s)

        _lbl(self, "Current Acq Settings", (8, 306, 134, 16), bold=True)
        self._acq_text = QTextEdit(self)
        self._acq_text.setGeometry(8, 324, 222, 220)
        self._acq_text.setReadOnly(True)
        self._acq_text.setStyleSheet(
            f"background: {_WHITE}; border: 1px solid {_BORDER}; font-size: 11px;")
        self._acq_text.setPlainText(
            "Tile sizes are set manually above.\n\n"
            "When the acquisition pipeline is connected, Size X|Y will be\n"
            "Image Size (px) × µm/px, and Size Z will be Z step × # slices.")

        # ── center: Grid Sequence Stage Positions table ───────────────────────
        _lbl(self, "Grid Sequence Stage Positions", (238, 144, 210, 16), bold=True)
        self._table = QTableWidget(0, 4, self)
        self._table.setGeometry(238, 162, 360, 382)
        self._table.setHorizontalHeaderLabels(["#", "X um", "Y um", "Z um"])
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(_ROW_H)
        hh = self._table.horizontalHeader()
        hh.setFixedHeight(_HEADER_H)
        hh.setSectionResizeMode(QHeaderView.Fixed)
        hh.setHighlightSections(False)
        for i, w in enumerate((38, 100, 100, 100)):
            self._table.setColumnWidth(i, w)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setStyleSheet(
            f"QTableWidget {{ gridline-color: {_HEADER}; background: white;"
            f" border: 1px solid #777; selection-background-color: {_SELECT};"
            f" selection-color: white; }}"
            f"QHeaderView::section {{ background: {_HEADER}; border: 0;"
            f" border-right: 1px solid #fff; padding-left: 4px; }}")

        # ── right buttons ─────────────────────────────────────────────────────
        self._gen_btn = _btn(self, "Generate Grid\nSequence",
                             (608, 176, 162, 40), self._on_generate)
        f = self._gen_btn.font(); f.setBold(True); self._gen_btn.setFont(f)

        _lbl(self, "# points", (668, 230, 60, 16))
        self._npts_label = QLabel("0", self)
        self._npts_label.setGeometry(608, 228, 58, 20)
        self._npts_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._npts_label.setStyleSheet("font-weight: bold;")

        self._set_btn = _btn(self, "Set Sequence",
                             (608, 340, 162, 40), self._on_set_sequence)
        self._set_btn.setEnabled(False)

        _btn(self, "Close", (608, 514, 162, 40), self.close)

    # ── slots ─────────────────────────────────────────────────────────────────

    def _on_current_position(self) -> None:
        """Fill Start spinboxes from the stage's current position."""
        try:
            xyz = self._xyz_provider()
        except Exception as exc:
            self._log(f"Grid: cannot get stage position: {exc}")
            return
        for s, v in zip(self._start, xyz):
            s.setValue(float(v))

    def _on_relative_toggled(self, checked: bool) -> None:
        self._relative_btn.setText("Relative" if checked else "Absolute")

    def _update_end_calc(self) -> None:
        for s_sp, r_sp, e_sp in zip(self._start, self._range, self._end_calc):
            e_sp.setValue(s_sp.value() + r_sp.value())

    def _on_generate(self) -> None:
        """Compute the grid and populate the table."""
        start_xyz = tuple(s.value() for s in self._start)
        range_xyz = tuple(r.value() for r in self._range)
        size_xyz  = tuple(t.value() for t in self._tile)
        overlap   = self._overlap.value()

        # If Relative: offset each start by the current stage position
        if self._relative_btn.isChecked():
            try:
                cur = self._xyz_provider()
                start_xyz = tuple(s + c for s, c in zip(start_xyz, cur))
            except Exception as exc:
                self._log(f"Grid: cannot get stage position: {exc}")

        points = generate_grid(start_xyz, range_xyz, size_xyz, overlap)
        self._grid_points = points

        # Populate table
        self._table.setRowCount(0)
        self._table.setRowCount(len(points))
        for r, (x, y, z) in enumerate(points):
            for c, val in enumerate((str(r + 1), f"{x:.2f}", f"{y:.2f}", f"{z:.2f}")):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self._table.setItem(r, c, item)

        self._npts_label.setText(str(len(points)))
        self._set_btn.setEnabled(len(points) > 0)
        self._log(f"Grid Sequence: {len(points)} points generated")

    def _on_set_sequence(self) -> None:
        """Replace the Location Sequence with the generated grid and notify."""
        if not self._grid_points:
            return
        new_rows = []
        for i, (x, y, z) in enumerate(self._grid_points):
            row6 = format_location_row(f"Grid {i + 1}", x, y, z, None, None)
            new_rows.append(["#"] + row6)
        self._sequence.write(new_rows)
        if self._seq_changed is not None:
            try:
                self._seq_changed.emit()
            except Exception:
                pass
        self._log(f"Grid Sequence set: {len(self._grid_points)} locations")

    def push_acq_settings(self, *, size_x_um: float, size_y_um: float, size_z_um: float,
                          description: str = "") -> None:
        """Called by the main window when acquisition settings change.

        Sets the Tile Size spinboxes and updates the Current Acq Settings text
        so the user can see where the values came from.
        """
        self._tile[0].setValue(size_x_um)
        self._tile[1].setValue(size_y_um)
        self._tile[2].setValue(size_z_um)
        self._acq_text.setPlainText(
            f"Size X: {size_x_um:.2f} µm\n"
            f"Size Y: {size_y_um:.2f} µm\n"
            f"Size Z: {size_z_um:.2f} µm\n"
            + (f"\n{description}" if description else ""))

    def closeEvent(self, event) -> None:
        """Hide rather than destroy (LouisXIV: FP.State = Hidden on Exit)."""
        event.ignore()
        self.hide()
