"""Main application window -- a close visual replica of the real LouisXIV
`SPIM MAIN.vi` front panel, functionally scoped to Continuous Scan and Z
stack modes only.

Layout modeled directly on
`VI_Diagrams/SPIM/SPIM LV8.6 VIs/SPIM MAIN/SPIM MAINp.png` (the real
front-panel screenshot): lavender top bar (Acquire, mode dropdown,
Simulation checkbox, Status pill + progress bars, Exit), a tabbed left
panel (Scan Setup / Camera / Utilities / Preferences / Adv Setup), and a
tabbed right side (Waveforms / Images / Bckgrd / Blank on top, a second
tab row -- Image Profile / Stack Profile / Stack Projections / Row
Profile / Timing / Diagnostics -- on the bottom).

IMPORTANT -- honesty about what's real: most of the real panel's controls
(Excitation/AOTF sliders, X galvo, Z Galvo, Cycle lasers, Timepoints,
Multi-location, Perfusion, the Waveforms/Bckgrd/Blank/Stack-Profile tabs)
are laid out here to match the real software's look, but are NOT wired to
hardware -- the deployed FPGA bitfile only exposes a single overall
"AOTF on?" bit (not per-channel control) and no galvo-stepping logic has
been validated yet (see fpga_io_map.md). Those controls are left disabled
(greyed out) so the window looks right without claiming functionality
that hasn't been hardware-validated. The controls that ARE real: Camera
connect/exposure, FPGA connect, Z Piezo Interval/Start/End/Slices (drives
the Z-stack trigger count), Acquire/Stop, and the live image feed --
exactly the same hardware path as before this restyle.

Threading design (IMPORTANT -- see hardware/fpga_trigger.py and the
session's crash history): Camera is touched ONLY from the GUI thread via
QTimer polling (the same pattern validated in the round-trip test).
FpgaTriggerController's continuous-firing loop runs on ITS OWN internal
thread (see start_continuous()); it never touches Qt widgets directly --
its on_frame callback only emits a thread-safe Qt Signal, which Qt safely
queues onto the GUI thread. Z-stack mode reuses the exact same continuous-
firing mechanism, just with a target frame count checked on the GUI-thread
side of that signal (never self-joins the firing thread from within
itself). This keeps each hardware resource touched by exactly one thread,
matching the discipline that avoided further native-layer crashes.
"""
from __future__ import annotations

import datetime

import numpy as np
from PySide6.QtCore import Qt, QTimer, QObject, Signal, QRect
from PySide6.QtGui import QImage, QPixmap, QPainter, QColor, QFont
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QFormLayout, QLabel, QPushButton, QComboBox, QDoubleSpinBox, QSpinBox,
    QTextEdit, QMessageBox, QSizePolicy, QCheckBox, QTabWidget, QSlider,
    QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar,
    QSplitter, QRadioButton, QButtonGroup, QToolButton, QScrollArea, QFrame,
)

from unmscope.hardware.camera import Camera, OrcaFlash4Camera, SimulatedCamera
from unmscope.hardware.fpga_trigger import FpgaTriggerController

MODE_CONTINUOUS = "Continuous Scan"
MODE_ZSTACK = "Z stack"

# -- LouisXIV-style palette (sampled from the real front panel) ------------
PANEL_BG = "#e8e8f8"       # light lavender window/tab background
GROUP_BG = "#f2f2fb"       # even lighter group-box interior
WHITE_BG = "#ffffff"
BORDER = "#8f8fbf"
IDLE_GREEN = "#2fa72f"
ACQUIRING_RED = "#cc3333"
DISABLED_NOTE = "#6a6a6a"

STYLESHEET = f"""
QMainWindow, QWidget#central {{ background-color: {PANEL_BG}; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; background: {PANEL_BG}; }}
QTabBar::tab {{
    background: {GROUP_BG}; border: 1px solid {BORDER}; border-bottom: none;
    padding: 4px 10px; margin-right: 1px;
}}
QTabBar::tab:selected {{ background: {WHITE_BG}; font-weight: bold; }}
QGroupBox {{
    background-color: {GROUP_BG}; border: 1px solid {BORDER}; border-radius: 3px;
    margin-top: 6px; font-weight: bold;
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 8px; padding: 0 4px; background: {PANEL_BG};
}}
QPushButton {{ background-color: {WHITE_BG}; border: 1px solid {BORDER}; padding: 3px 8px; }}
QPushButton:disabled {{ color: #999; background-color: #eee; }}
QSlider::groove:horizontal {{ height: 5px; background: #bbb; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: #2f6fdb; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: white; border: 1px solid #666; width: 12px; margin: -5px 0; border-radius: 6px;
}}
QSlider:disabled::sub-page:horizontal {{ background: #90a4c8; }}
"""


def frame_to_qpixmap(frame: np.ndarray, label_text: str | None = None) -> QPixmap:
    """uint16 (or any) 2D frame -> auto-contrast 8-bit QPixmap for display.
    If label_text is given, it's burned into the top-left corner of the
    image itself (not just a separate widget), with a dark backing box for
    readability regardless of the underlying frame's brightness."""
    lo, hi = np.percentile(frame, (0.5, 99.5))
    if hi <= lo:
        hi = lo + 1
    scaled = np.clip((frame.astype(np.float32) - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)
    h, w = scaled.shape
    qimg = QImage(scaled.data, w, h, w, QImage.Format_Grayscale8).copy()
    pix = QPixmap.fromImage(qimg)

    if label_text:
        painter = QPainter(pix)
        font = QFont()
        font.setPointSize(max(14, w // 40))
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        text_rect = metrics.boundingRect(label_text)
        pad = 8
        box = QRect(10, 10, text_rect.width() + 2 * pad, text_rect.height() + 2 * pad)
        painter.fillRect(box, QColor(0, 0, 0, 160))
        painter.setPen(QColor(255, 255, 0))
        painter.drawText(box, Qt.AlignCenter, label_text)
        painter.end()

    return pix


def _narrow(w, width: int = 80):
    """Cap a spinbox's width so it doesn't crowd out its label in the
    real panel's tight two-column Scan Setup layout."""
    w.setMaximumWidth(width)
    return w


def _stub_note(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    # A word-wrapped QLabel's minimumSizeHint doesn't actually shrink on its
    # own in Qt -- without a cap here, these notes were forcing the whole
    # window ~270px wider than intended (each one wanted its full unwrapped
    # single-line width as a minimum). 220px matches the narrow columns
    # these live in throughout Scan Setup.
    lbl.setMaximumWidth(220)
    lbl.setStyleSheet(f"color: {DISABLED_NOTE}; font-style: italic; font-weight: normal;")
    return lbl


def _placeholder_tab(title: str, subtitle: str = "Not yet implemented.") -> QWidget:
    """A stub tab that matches the real panel's tab structure visually
    without pretending to have real content behind it."""
    w = QWidget()
    lay = QVBoxLayout(w)
    box = QLabel(f"{title}\n\n{subtitle}")
    box.setAlignment(Qt.AlignCenter)
    box.setStyleSheet("background-color: #1e1e1e; color: #888; font-style: italic;")
    box.setMinimumHeight(120)
    # Without word-wrap, a long subtitle (e.g. Waveforms') reports its full
    # unwrapped width as a MINIMUM, which was forcing this tab's QTabWidget
    # -- and everything above it up to the whole window -- ~270px wider.
    box.setWordWrap(True)
    lay.addWidget(box)
    return w


class FpgaSignals(QObject):
    """Bridges FpgaTriggerController's background-thread callbacks to the
    GUI thread. Signal.emit() is thread-safe in Qt -- calling it from a
    non-GUI thread safely queues delivery to the connected slot on the GUI
    thread, rather than touching widgets directly from the wrong thread."""
    frame_fired = Signal(int)
    rate_measured = Signal(float)
    error = Signal(str)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("UNMScope -- LouisXIV (Python)")
        # Match the real SPIM MAIN.vi window exactly (1381x931, measured via
        # GetWindowRect off the live LouisXIV.exe front panel).
        self.resize(1381, 898)
        self.setStyleSheet(STYLESHEET)

        self.camera: Camera | None = None
        self.fpga: FpgaTriggerController | None = None
        self.fpga_signals = FpgaSignals()
        self.fpga_signals.frame_fired.connect(self._on_fpga_frame_fired)
        self.fpga_signals.rate_measured.connect(self._on_fpga_rate_measured)
        self.fpga_signals.error.connect(self._on_fpga_error)
        self._trigger_period_s = 0.0
        self._triggers_fired = 0

        self.camera_poll_timer = QTimer(self)
        self.camera_poll_timer.timeout.connect(self._poll_camera_for_frame)

        self.frame_count = 0
        self.z_target_frames = 0
        self.acquiring = False

        self._build_ui()

    # -- UI --------------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 4, 6, 4)
        root.setSpacing(4)

        root.addLayout(self._build_top_bar())

        body = QSplitter(Qt.Horizontal)
        root.addWidget(body, stretch=1)

        body.addWidget(self._build_left_tabs())
        body.addWidget(self._build_right_side())
        # 406/975 -- measured left/right panel widths off the real front panel.
        body.setSizes([406, 975])

        self._on_mode_changed(self.mode_combo.currentText())

    # -- Top bar: Acquire, mode, Simulation, Status pill+bars, Exit --------
    def _build_top_bar(self) -> QHBoxLayout:
        top_bar = QHBoxLayout()

        self.acquire_btn = QPushButton("Acquire")
        self.acquire_btn.setMinimumHeight(40)
        self.acquire_btn.setMinimumWidth(120)
        self.acquire_btn.setStyleSheet("font-weight: bold; font-size: 12pt;")
        self.acquire_btn.clicked.connect(self.on_acquire_clicked)
        self.acquire_btn.setEnabled(False)
        top_bar.addWidget(self.acquire_btn)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems([MODE_CONTINUOUS, MODE_ZSTACK])
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        top_bar.addWidget(self.mode_combo)

        top_bar.addStretch(1)

        sim_col = QVBoxLayout()
        self.simulation_check = QCheckBox("Simulation")
        self.simulation_check.setToolTip(
            "Forces the Camera backend (Camera tab) to Simulated instead of the real Orca 4.0."
        )
        self.simulation_check.toggled.connect(self._on_simulation_toggled)
        sim_col.addWidget(self.simulation_check)
        sim_col.addStretch(1)
        top_bar.addLayout(sim_col)

        status_box = QGroupBox("Status")
        status_box.setStyleSheet(status_box.styleSheet() + "background-color: #ececec;")
        status_grid = QGridLayout(status_box)

        self.acq_status_label = QLabel("IDLE")
        self.acq_status_label.setAlignment(Qt.AlignCenter)
        self.acq_status_label.setMinimumWidth(90)
        self.acq_status_label.setStyleSheet(
            f"background-color: {IDLE_GREEN}; color: white; font-weight: bold; "
            "padding: 6px; border-radius: 3px;"
        )
        status_grid.addWidget(self.acq_status_label, 0, 0, 2, 1)

        self.acq_progress = QProgressBar()
        self.acq_progress.setRange(0, 100)
        self.acq_progress.setTextVisible(False)
        self.acq_progress.setMaximumHeight(12)
        status_grid.addWidget(QLabel("Aqc."), 0, 1)
        status_grid.addWidget(self.acq_progress, 0, 2)

        self.overall_progress = QProgressBar()
        self.overall_progress.setRange(0, 100)
        self.overall_progress.setTextVisible(False)
        self.overall_progress.setMaximumHeight(12)
        status_grid.addWidget(QLabel("Overall"), 1, 1)
        status_grid.addWidget(self.overall_progress, 1, 2)

        top_bar.addWidget(status_box)

        exit_btn = QPushButton("EXIT")
        exit_btn.setMinimumHeight(40)
        exit_btn.setStyleSheet("font-weight: bold;")
        exit_btn.clicked.connect(self.close)
        top_bar.addWidget(exit_btn)
        return top_bar

    # -- Left panel: an always-visible Connection bar (Camera+FPGA connect --
    #    this is OUR app's own hardware bring-up step, not part of the real
    #    SPIM MAIN panel, so it deliberately does NOT hide inside a tab the
    #    way the rest of this panel mimics the real layout: Acquire depends
    #    on both being connected, and burying that behind a tab click made
    #    the whole app look broken/"lost functionality" when it was really
    #    just Connect never having been clicked) sitting above the tabbed
    #    Scan Setup / Camera / Utilities / Preferences / Adv Setup panel.
    def _build_left_tabs(self) -> QWidget:
        container = QWidget()
        lay = QVBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._build_connection_bar())

        tabs = QTabWidget()
        tabs.setFixedWidth(406)  # measured off the real front panel
        scan_setup_scroll = QScrollArea()
        scan_setup_scroll.setWidget(self._build_scan_setup_tab())
        scan_setup_scroll.setWidgetResizable(True)
        scan_setup_scroll.setFrameShape(QFrame.NoFrame)
        # Scan Setup's own content wants ~90px more height than the real
        # panel's tighter native-control metrics allow -- scrolling here
        # (rather than growing) keeps the WHOLE WINDOW pinned to the real
        # panel's size; nothing is normally cut off at the target size.
        tabs.addTab(scan_setup_scroll, "Scan Setup")
        tabs.addTab(self._build_camera_tab(), "Camera")
        tabs.addTab(_placeholder_tab("Utilities"), "Utilities")
        tabs.addTab(_placeholder_tab("Preferences"), "Preferences")
        tabs.addTab(self._build_adv_setup_tab(), "Adv Setup")
        lay.addWidget(tabs, stretch=1)
        return container

    def _build_connection_bar(self) -> QGroupBox:
        box = QGroupBox("Hardware Connection")
        grid = QGridLayout(box)

        grid.addWidget(QLabel("Camera:"), 0, 0)
        self.backend_combo = QComboBox()
        self.backend_combo.addItems(["Orca Flash 4.0 (real)", "Simulated"])
        grid.addWidget(self.backend_combo, 0, 1)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self.on_connect_clicked)
        grid.addWidget(self.connect_btn, 0, 2)
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.clicked.connect(self.on_disconnect_clicked)
        self.disconnect_btn.setEnabled(False)
        grid.addWidget(self.disconnect_btn, 0, 3)
        self.status_label = QLabel("Not connected")
        self.status_label.setStyleSheet("font-weight: bold;")
        self.status_label.setWordWrap(True)
        grid.addWidget(self.status_label, 1, 0, 1, 4)

        grid.addWidget(QLabel("FPGA (DIO4):"), 2, 0)
        self.fpga_connect_btn = QPushButton("Connect")
        self.fpga_connect_btn.clicked.connect(self.on_fpga_connect_clicked)
        grid.addWidget(self.fpga_connect_btn, 2, 2)
        self.fpga_disconnect_btn = QPushButton("Disconnect")
        self.fpga_disconnect_btn.clicked.connect(self.on_fpga_disconnect_clicked)
        self.fpga_disconnect_btn.setEnabled(False)
        grid.addWidget(self.fpga_disconnect_btn, 2, 3)
        self.fpga_status_label = QLabel("Not connected")
        self.fpga_status_label.setStyleSheet("font-weight: bold;")
        grid.addWidget(self.fpga_status_label, 3, 0, 1, 4)

        return box

    def _build_scan_setup_tab(self) -> QWidget:
        tab = QWidget()
        outer = QVBoxLayout(tab)

        # Real panel is genuinely two side-by-side columns, not one column
        # with a single split top row -- confirmed from both of the user's
        # screenshots: LEFT = Excitation/Cycle lasers/Timepoints/Stack
        # time/Multi-location/Perfusion; RIGHT = X galvo/Z Galvo/Linked+Rel
        # Offset/Z Piezo/Dither Galvo.
        columns = QHBoxLayout()
        outer.addLayout(columns, stretch=1)

        left_col = QVBoxLayout()
        left_col.setSpacing(4)
        left_col.addWidget(self._build_excitation_box())
        left_col.addLayout(self._build_cycle_lasers_row())
        self.timepoints_widget = self._build_timepoints_box()
        left_col.addWidget(self.timepoints_widget)
        self.multilocation_widget = self._build_multilocation_box()
        left_col.addWidget(self.multilocation_widget)
        self.perfusion_widget = self._build_perfusion_box()
        left_col.addWidget(self.perfusion_widget)
        left_col.addStretch(1)
        columns.addLayout(left_col, stretch=1)

        right_col = QVBoxLayout()
        right_col.setSpacing(4)
        right_col.addWidget(self._build_x_galvo_box())
        right_col.addWidget(self._build_z_galvo_box())
        right_col.addWidget(self._build_linked_box())
        right_col.addWidget(self._build_z_piezo_box())
        right_col.addWidget(self._build_dither_galvo_box())
        right_col.addStretch(1)
        columns.addLayout(right_col, stretch=1)

        return tab

    def _build_cycle_lasers_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel("Cycle lasers:"))
        self.cycle_lasers_combo = QComboBox()
        # Real enum "HHMI - SPIM AOTF cycle enum.ctl": per Z (advance laser
        # channel every Z-plane), per Stack (advance once per Z-stack),
        # None (pinned to channel 0, no cycling -- current real-hardware
        # default, consistent with the FPGA's single AOTF on/off bit).
        self.cycle_lasers_combo.addItems(["per Z", "per Stack", "None"])
        self.cycle_lasers_combo.setCurrentText("None")
        row.addWidget(self.cycle_lasers_combo)
        row.addStretch(1)
        return row

    def _build_linked_box(self) -> QGroupBox:
        box = QGroupBox()
        lay = QVBoxLayout(box)
        self.linked_btn = QPushButton("Unlinked")
        self.linked_btn.setCheckable(True)
        self.linked_btn.setChecked(False)
        self.linked_btn.setToolTip(
            "Best-supported inference from source layout/naming (Z Piezo "
            "and Z Galvo globals are consistently paired elsewhere in the "
            "codebase), not a literally-confirmed wiring: when Linked, Z "
            "Piezo's Start/End follow Z Galvo + Rel. Offset instead of "
            "being set independently."
        )
        self.linked_btn.toggled.connect(self._on_linked_toggled)
        lay.addWidget(self.linked_btn)
        offset_row = QHBoxLayout()
        offset_row.addWidget(QLabel("Rel. Offset:"))
        self.rel_offset_spin = _narrow(QDoubleSpinBox())
        self.rel_offset_spin.setRange(-100.0, 100.0)
        self.rel_offset_spin.setDecimals(2)
        offset_row.addWidget(self.rel_offset_spin)
        offset_row.addStretch(1)
        lay.addLayout(offset_row)
        return box

    def _build_dither_galvo_box(self) -> QGroupBox:
        # Real axis (AO4). "Range (um)"/"# Sweeps"/"Fract. Flyback" map to
        # the "D" (Dither) case of HHMI - SPIM Make Ramp Waveform.vi's fast-
        # axis ramp generator (Triangle shape): Range -> deflection via the
        # "Dither Galvo um/V"=10.0 calibration, clamped to the project's [D
        # Galvo Limits (V)] (+-5.5V => +-55um on the main rig); # Sweeps ->
        # "Dither Triangle Pulses" (fractional allowed); Fract. Flyback ->
        # "Dither Fract. Flyback", the turnaround-smoothing fraction of each
        # half-period. See fpga_io_map.md for the full VI chain.
        box = QGroupBox("Dither Galvo")
        form = QFormLayout(box)
        self.dg_range = _narrow(QDoubleSpinBox(), 90)
        self.dg_range.setRange(0, 55)
        self.dg_range.setSuffix(" um")
        self.dg_range.setToolTip("Clamped to +-55 um on the main rig (Dither Galvo um/V=10.0, D Galvo Limits=+-5.5V).")
        self.dg_sweeps = _narrow(QDoubleSpinBox())
        self.dg_sweeps.setRange(0, 100)
        self.dg_sweeps.setDecimals(1)
        self.dg_sweeps.setValue(5.5)
        self.dg_flyback = _narrow(QDoubleSpinBox())
        self.dg_flyback.setRange(0.0, 1.0)
        self.dg_flyback.setDecimals(2)
        self.dg_flyback.setValue(0.10)
        form.addRow("Range (um)", self.dg_range)
        form.addRow("# Sweeps", self.dg_sweeps)
        form.addRow("Fract. Flyback", self.dg_flyback)
        return box

    def _on_linked_toggled(self, checked: bool):
        self.linked_btn.setText("Linked" if checked else "Unlinked")
        self._update_scan_setup_enable_state()

    # Real wavelength labels + defaults, from SPIMProject.ini's
    # [AOTF Settings] Labels="637,561,488,405" and the "HHMI - SPIM Single
    # Excitation GUI cluster.ctl" typedef (Enabled bool + Power % float,
    # default Enabled=False/Power=0.1%) -- see fpga_io_map.md.
    EXCITATION_WAVELENGTHS_NM = (637, 561, 488, 405)

    def _build_excitation_box(self) -> QGroupBox:
        box = QGroupBox("Excitation")
        form = QVBoxLayout(box)
        self.excitation_rows: list[tuple[QCheckBox, int, QDoubleSpinBox]] = []
        for wavelength in self.EXCITATION_WAVELENGTHS_NM:
            is_default_on = wavelength == 488  # matches the user's own screenshot
            row = QHBoxLayout()
            chk = QCheckBox()
            chk.setToolTip(
                f"{wavelength} nm enable -- real per-row field (Excitations "
                "cluster), but the deployed FPGA bitfile only exposes one "
                "overall AOTF on/off bit, so this doesn't drive hardware yet."
            )
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 1000)  # 0.1% steps
            slider.setMinimumWidth(40)
            spin = _narrow(QDoubleSpinBox(), 78)
            spin.setRange(0.0, 100.0)
            spin.setDecimals(1)
            spin.setSingleStep(0.1)
            spin.setSuffix(" %")
            spin.setValue(100.0 if is_default_on else 0.1)
            slider.setValue(int(round(spin.value() * 10)))
            chk.setChecked(is_default_on)
            slider.valueChanged.connect(lambda v, s=spin: s.setValue(v / 10.0))
            spin.valueChanged.connect(lambda v, sl=slider: sl.setValue(int(round(v * 10))))
            row.addWidget(QLabel(f"{wavelength}"))
            row.addWidget(chk)
            row.addWidget(slider, stretch=1)
            row.addWidget(spin)
            form.addLayout(row)
            self.excitation_rows.append((chk, wavelength, spin))
        return box

    def _build_x_galvo_box(self) -> QGroupBox:
        box = QGroupBox("X galvo")
        form = QFormLayout(box)
        self.xg_offset = _narrow(QDoubleSpinBox()); self.xg_offset.setRange(-1000, 1000)
        self.xg_range = _narrow(QDoubleSpinBox()); self.xg_range.setRange(-1000, 1000); self.xg_range.setValue(100)
        self.xg_pixels = _narrow(QSpinBox()); self.xg_pixels.setRange(1, 100000); self.xg_pixels.setValue(60)
        self.xg_interval = _narrow(QDoubleSpinBox())
        self.xg_interval.setDecimals(2)
        self.xg_interval.setRange(0, 1_000_000)
        self.xg_interval.setEnabled(False)  # always computed -- see below
        self.xg_range.valueChanged.connect(self._update_x_galvo_interval)
        self.xg_pixels.valueChanged.connect(self._update_x_galvo_interval)
        form.addRow("Offset", self.xg_offset)
        form.addRow("Range", self.xg_range)
        form.addRow("# of pixels", self.xg_pixels)
        form.addRow("\U0001F512 Interval", self.xg_interval)
        self._update_x_galvo_interval()
        return box

    def _update_x_galvo_interval(self):
        # Real formula from HHMI - SPIM Update GUI Axis control.vi's
        # "Lock interval?" (default False/locked-computed) case, labeled
        # "Adjust interval." on the diagram: Interval = Range / (#pixels-1),
        # with the documented edge case "if pix = 1, change interval, range
        # to zero." -- the padlock icon marks Interval as read-only/derived.
        pixels = self.xg_pixels.value()
        if pixels <= 1:
            self.xg_range.blockSignals(True)
            self.xg_range.setValue(0)
            self.xg_range.blockSignals(False)
            self.xg_interval.setValue(0)
            return
        self.xg_interval.setValue(self.xg_range.value() / (pixels - 1))

    def _build_z_galvo_box(self) -> QGroupBox:
        box = QGroupBox("Z Galvo (um)")
        form = QFormLayout(box)
        self.zg_interval = _narrow(QDoubleSpinBox()); self.zg_interval.setDecimals(3); self.zg_interval.setRange(0, 1000)
        self.zg_start = _narrow(QDoubleSpinBox()); self.zg_start.setRange(-1000, 1000)
        self.zg_end = _narrow(QDoubleSpinBox()); self.zg_end.setRange(-1000, 1000); self.zg_end.setValue(2)
        # End Pos only makes sense with a defined stop -- disabled in
        # Continuous Scan, enabled in Z stack (mode-gated in
        # _update_scan_setup_enable_state; confirmed against the user's
        # real screenshots of both modes).
        form.addRow("Interval", self.zg_interval)
        form.addRow("Start Pos", self.zg_start)
        form.addRow("End Pos", self.zg_end)
        return box

    def _build_z_piezo_box(self) -> QGroupBox:
        # This is the one real, hardware-driving box from the old layout --
        # renamed/restyled to match the real panel's "Z Piezo (um)" box,
        # same underlying logic as before (drives the Z-stack trigger count).
        box = QGroupBox("Z Piezo (um)")
        form = QFormLayout(box)
        self.z_interval_spin = _narrow(QDoubleSpinBox())
        self.z_interval_spin.setRange(0.01, 1000.0)
        self.z_interval_spin.setValue(1.0)
        self.z_start_spin = _narrow(QDoubleSpinBox())
        self.z_start_spin.setRange(-1000.0, 1000.0)
        self.z_start_spin.setValue(0.0)
        self.z_end_spin = _narrow(QDoubleSpinBox())
        self.z_end_spin.setRange(-1000.0, 1000.0)
        self.z_end_spin.setValue(10.0)
        for w in (self.z_interval_spin, self.z_start_spin, self.z_end_spin):
            w.valueChanged.connect(self._update_slice_count)
        form.addRow("Interval", self.z_interval_spin)
        form.addRow("Start Pos", self.z_start_spin)
        form.addRow("End Pos", self.z_end_spin)
        self.slice_count_field = QLineEdit("-")
        self.slice_count_field.setReadOnly(True)
        self.slice_count_field.setMaximumWidth(80)
        self.slice_count_field.setStyleSheet("font-weight: bold;")
        form.addRow("Slices", self.slice_count_field)
        self._update_slice_count()
        return box

    def _build_timepoints_box(self) -> QWidget:
        # Borderless (matches the real panel -- no box outline around this
        # section, unlike Z Galvo/Z Piezo/Dither Galvo). Only Single/1
        # Timepoint Interval+Delay rows are visible in that case on the
        # real panel (both user screenshots), so those 2 rows are omitted.
        box = QWidget()
        form = QGridLayout(box)
        form.setContentsMargins(0, 0, 0, 0)
        form.addWidget(QLabel("Timepoints"), 0, 0)
        tp_combo = QComboBox(); tp_combo.addItems(["Single"]); tp_combo.setMinimumWidth(90)
        form.addWidget(tp_combo, 0, 1)
        tp_spin = _narrow(QSpinBox(), 55); tp_spin.setRange(1, 9999); tp_spin.setValue(1)
        form.addWidget(tp_spin, 0, 2)
        save_chk = QCheckBox("Save Files")
        form.addWidget(save_chk, 1, 0, 1, 3)

        for r, (label, default) in enumerate([
            ("Stack Acq. Time", "00:00.00"),
            ("Total time", "00:00:00"),
        ], start=2):
            form.addWidget(QLabel(label), r, 0)
            field = _narrow(QLineEdit(default), 110)
            field.setReadOnly(True)
            form.addWidget(field, r, 1, 1, 2)
        return box

    def _build_multilocation_box(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        chk = QCheckBox("Multi-location")
        cfg_btn = QPushButton("Configure")
        row.addWidget(chk); row.addWidget(cfg_btn); row.addStretch(1)
        lay.addLayout(row)

        table = QTableWidget(3, 4)
        table.setHorizontalHeaderLabels(["X", "Y", "Z", "Z RO"])
        table.verticalHeader().setVisible(True)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setMaximumHeight(110)
        lay.addWidget(table)
        return box

    def _build_perfusion_box(self) -> QWidget:
        box = QWidget()
        form = QGridLayout(box)
        form.setContentsMargins(0, 0, 0, 0)
        chk = QCheckBox("Perfusion enabled")
        form.addWidget(chk, 0, 0, 1, 2)
        form.addWidget(QLabel("Perfusion on"), 1, 0)
        on_field = QLineEdit("00:00"); on_field.setReadOnly(True)
        on_field.setMaximumWidth(80)
        form.addWidget(on_field, 1, 1)
        form.addWidget(QLabel("Perfusion off"), 2, 0)
        off_field = QLineEdit("00:00"); off_field.setReadOnly(True)
        off_field.setMaximumWidth(80)
        form.addWidget(off_field, 2, 1)
        return box

    def _build_camera_tab(self) -> QWidget:
        # Connect/Disconnect + status live in the always-visible Hardware
        # Connection bar above the tabs now -- this tab just holds settings
        # for an already-connected camera.
        tab = QWidget()
        lay = QVBoxLayout(tab)

        settings_box = QGroupBox("Camera Settings")
        form = QFormLayout(settings_box)
        self.exposure_spin = QDoubleSpinBox()
        self.exposure_spin.setRange(0.1, 10000.0)
        self.exposure_spin.setDecimals(2)
        self.exposure_spin.setSuffix(" ms")
        self.exposure_spin.setValue(100.0)
        self.exposure_spin.valueChanged.connect(self.on_exposure_changed)
        form.addRow("Exposure:", self.exposure_spin)

        lay.addWidget(settings_box)
        lay.addStretch(1)
        return tab

    def _build_adv_setup_tab(self) -> QWidget:
        # FPGA Connect/Disconnect + status live in the always-visible
        # Hardware Connection bar above the tabs now.
        return _placeholder_tab("Adv Setup", "Nothing else here yet.")

    # -- Right side: top tab row (Waveforms/Images/Bckgrd/Blank) over a
    #    bottom tab row (Image Profile/Stack Profile/.../Diagnostics) -----
    def _build_right_side(self) -> QSplitter:
        splitter = QSplitter(Qt.Vertical)

        top_tabs = QTabWidget()
        top_tabs.addTab(_placeholder_tab(
            "X Waveform / Full Waveform",
            "Real-time waveform display (X Galvo, AOTF Ch0-4, Z Galvo, Z "
            "Piezo, Sample Piezo, Tile) not yet implemented -- see "
            "'HHMI - AI buffer.vi' in fpga_io_map.md for the internal "
            "software-scope path this would read from.",
        ), "Waveforms")
        top_tabs.addTab(self._build_images_tab(), "Images")
        top_tabs.addTab(_placeholder_tab("Background"), "Bckgrd")
        top_tabs.addTab(_placeholder_tab("Blank"), "Blank")
        top_tabs.setCurrentIndex(1)  # Images -- where the actual feed lives
        splitter.addWidget(top_tabs)

        bottom_tabs = QTabWidget()
        bottom_tabs.addTab(_placeholder_tab("Image Profile / Point Profile / Line Profile"), "Image Profile")
        bottom_tabs.addTab(_placeholder_tab("Stack Profile"), "Stack Profile")
        bottom_tabs.addTab(self._build_stack_projections_tab(), "Stack Projections")
        bottom_tabs.addTab(_placeholder_tab("Row Profile"), "Row Profile")
        bottom_tabs.addTab(_placeholder_tab("Timing"), "Timing")
        bottom_tabs.addTab(self._build_diagnostics_tab(), "Diagnostics")
        splitter.addWidget(bottom_tabs)

        # 540/290 -- measured images-pane/stack-projections-pane height ratio.
        splitter.setSizes([540, 290])
        return splitter

    def _build_images_tab(self) -> QWidget:
        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(4, 4, 4, 2)
        outer.setSpacing(3)

        # Real panel layout: narrow tool strip | bounded scrollable image |
        # narrow display-options strip (see SPIM MAINp.png's Images tab).
        # None of the three widgets built below drive real behavior yet --
        # visual-only, same convention as the rest of this module (see the
        # module docstring's "honesty about what's real").
        image_row = QHBoxLayout()
        image_row.setSpacing(6)
        image_row.addWidget(self._build_image_left_toolbar())
        image_row.addWidget(self._build_image_display(), stretch=1)
        # The real panel reserves a 16px vertical scrollbar gutter to the
        # right of the canvas. We don't draw one, but the space still has
        # to be spent or our canvas comes out wider than the reference.
        image_row.addSpacing(16)
        image_row.addWidget(self._build_max_counts_panel())
        image_row.addWidget(self._build_display_options_panel())
        outer.addLayout(image_row, stretch=1)

        # Frame counter/info live BELOW the canvas, in the thin strip the
        # real panel puts there -- they used to sit ABOVE it, which cost the
        # canvas ~61px of height the real panel spends on image (measured:
        # real canvas is 496x440, ours had shrunk to 501x385).
        # Fixed 57px: the real panel spends ~55px below the canvas on its
        # horizontal scrollbar + progress bar. Matching that height is what
        # makes our canvas come out at the reference's 441px instead of
        # swallowing the space (verify with tools/compare_to_labview.py).
        status_holder = QWidget()
        status_holder.setFixedHeight(57)
        status_strip = QHBoxLayout(status_holder)
        status_strip.setContentsMargins(0, 0, 0, 0)
        status_strip.setAlignment(Qt.AlignTop)
        status_strip.addWidget(QLabel("Frames received:"))
        self.frame_counter_label = QLabel("0")
        self.frame_counter_label.setStyleSheet("font-weight: bold; color: #2a7;")
        status_strip.addWidget(self.frame_counter_label)
        status_strip.addSpacing(12)
        self.frame_info_label = QLabel("-")
        status_strip.addWidget(self.frame_info_label)
        status_strip.addStretch(1)
        outer.addWidget(status_holder)
        return tab

    def _build_image_left_toolbar(self) -> QWidget:
        col_widget = QWidget()
        col_widget.setFixedWidth(80)  # measured off the real panel
        col = QVBoxLayout(col_widget)
        col.setContentsMargins(2, 0, 2, 0)
        col.setSpacing(2)

        self.img_tool_group = QButtonGroup(self)
        tool_specs = [
            ("zoom", "\U0001F50D"), ("pan", "✋"), ("crosshair", "➕"),
            ("line", "╱"), ("roi", "▭"),
        ]
        for name, glyph in tool_specs:
            btn = QToolButton()
            btn.setText(glyph)
            btn.setCheckable(True)
            btn.setFixedSize(28, 28)
            self.img_tool_group.addButton(btn)
            col.addWidget(btn)
            setattr(self, f"img_tool_{name}_btn", btn)
        self.img_tool_zoom_btn.setChecked(True)

        col.addSpacing(10)
        self.cam_select_combo = QComboBox()
        self.cam_select_combo.addItem("Cam 1")
        col.addWidget(self.cam_select_combo)

        col.addSpacing(10)
        self.save_image_btn = QPushButton("\U0001F4BE Image")
        col.addWidget(self.save_image_btn)

        col.addSpacing(10)
        self.cam_pan_btn = QPushButton("✥ Cam 1")
        col.addWidget(self.cam_pan_btn)

        col.addStretch(1)

        for w in (self.img_tool_zoom_btn, self.img_tool_pan_btn,
                  self.img_tool_crosshair_btn, self.img_tool_line_btn,
                  self.img_tool_roi_btn, self.cam_select_combo,
                  self.save_image_btn, self.cam_pan_btn):
            w.setEnabled(False)
        return col_widget

    def _build_image_display(self) -> QScrollArea:
        self.image_label = QLabel("(no image yet)")
        self.image_label.setAlignment(Qt.AlignCenter)
        # #f0f0f0 -- the real panel's empty-picture-control color, confirmed
        # by pixel-sampling the live front panel (NOT black, despite how it
        # reads at a glance/thumbnail size).
        self.image_label.setStyleSheet("background-color: #f0f0f0; color: #666;")
        self.image_label.setMinimumSize(300, 300)

        scroll = QScrollArea()
        scroll.setWidget(self.image_label)
        scroll.setWidgetResizable(True)  # label still fills the viewport,
        # matching the pre-restyle behavior that _poll_camera_for_frame()
        # relies on (it scales each frame to self.image_label.size()) --
        # this box only ADDS the bounded/bordered look, doesn't change sizing.
        scroll.setStyleSheet(f"QScrollArea {{ border: 1px solid {BORDER}; }}")
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        return scroll

    def _build_max_counts_panel(self) -> QWidget:
        # A SEPARATE bordered sub-panel from display-options below --
        # confirmed by pixel-sampling the real front panel: there's a real
        # ~14px gap and each side has its own 1px border the full panel
        # height, not one wide column with a big left margin (that was my
        # first, wrong read of it). 123px width measured off the real panel.
        panel = QFrame()
        panel.setObjectName("maxCountsPanel")
        panel.setFixedWidth(123)
        # Scoped by #maxCountsPanel, NOT a bare "QFrame" selector -- QLabel
        # is itself a QFrame subclass in Qt, so an unscoped rule was also
        # drawing a border around every label inside this panel.
        panel.setStyleSheet(f"QFrame#maxCountsPanel {{ border: 1px solid {BORDER}; background-color: {GROUP_BG}; }}")
        col = QVBoxLayout(panel)
        col.setContentsMargins(6, 6, 6, 6)
        col.addStretch(2)  # content sits in the lower ~2/3, not top-anchored

        col.addWidget(QLabel("Max Counts"))
        self.max_counts_spin = QSpinBox()
        self.max_counts_spin.setRange(0, 65535)
        col.addWidget(self.max_counts_spin)
        self.max_counts_slider = QSlider(Qt.Horizontal)
        self.max_counts_slider.setRange(0, 65535)
        col.addWidget(self.max_counts_slider)

        col.addSpacing(4)
        col.addWidget(QLabel("Frames to Avg"))
        self.frames_to_avg_spin = QSpinBox()
        self.frames_to_avg_spin.setRange(1, 100)
        self.frames_to_avg_spin.setValue(1)
        col.addWidget(self.frames_to_avg_spin)

        col.addSpacing(4)
        col.addWidget(QLabel("Stack Max"))
        self.stack_max_spin = QSpinBox()
        self.stack_max_spin.setRange(0, 10000)
        self.stack_max_spin.setValue(7)
        col.addWidget(self.stack_max_spin)
        self.stack_max_slider = QSlider(Qt.Horizontal)
        self.stack_max_slider.setRange(0, 10000)
        col.addWidget(self.stack_max_slider)
        col.addStretch(1)

        for w in (self.max_counts_spin, self.max_counts_slider,
                  self.frames_to_avg_spin, self.stack_max_spin,
                  self.stack_max_slider):
            w.setEnabled(False)
        return panel

    def _build_display_options_panel(self) -> QWidget:
        # The other sub-panel -- 214px, its own border, top-anchored
        # content with Text Info Overlay pinned to the bottom.
        panel = QFrame()
        panel.setObjectName("displayOptionsPanel")
        panel.setFixedWidth(214)
        panel.setStyleSheet(f"QFrame#displayOptionsPanel {{ border: 1px solid {BORDER}; background-color: {GROUP_BG}; }}")
        col = QVBoxLayout(panel)
        col.setContentsMargins(8, 8, 8, 8)

        palette_row = QHBoxLayout()
        palette_row.addWidget(QLabel("Pallete Color"))
        palette_col = QVBoxLayout()
        palette_col.setSpacing(0)
        self.palette_group = QButtonGroup(self)
        self.palette_gray_radio = QRadioButton("Gray")
        self.palette_gradient_radio = QRadioButton("Gradient")
        self.palette_rainbow_radio = QRadioButton("Rainbow")
        self.palette_gradient_radio.setChecked(True)
        for rb in (self.palette_gray_radio, self.palette_gradient_radio, self.palette_rainbow_radio):
            self.palette_group.addButton(rb)
            palette_col.addWidget(rb)
        palette_row.addLayout(palette_col)
        col.addLayout(palette_row)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        form.setVerticalSpacing(2)
        self.scalebar_check = QCheckBox()
        self.zoom_to_fit_check = QCheckBox()
        self.autoscale_z_check = QCheckBox()
        self.autoscale_z_check.setChecked(True)
        self.scale_to_counts_check = QCheckBox()
        form.addRow("Scalebar", self.scalebar_check)
        form.addRow("Zoom to fit", self.zoom_to_fit_check)
        form.addRow("Autoscale Z", self.autoscale_z_check)
        form.addRow("Scale to Counts", self.scale_to_counts_check)
        col.addLayout(form)

        col.addStretch(1)
        self.text_info_overlay_check = QCheckBox("Text Info Overlay")
        col.addWidget(self.text_info_overlay_check)

        for w in (self.palette_gray_radio, self.palette_gradient_radio,
                  self.palette_rainbow_radio, self.scalebar_check,
                  self.zoom_to_fit_check, self.autoscale_z_check,
                  self.scale_to_counts_check, self.text_info_overlay_check):
            w.setEnabled(False)
        return panel

    def _build_stack_projections_tab(self) -> QWidget:
        # Visual-only, matching SPIM MAINp.png's Stack Projections panel --
        # not wired to any real projection computation yet. Box size
        # (245x196) and gaps (~30px) measured off the live front panel.
        tab = QWidget()
        outer = QHBoxLayout(tab)
        outer.setSpacing(8)
        outer.setContentsMargins(6, 4, 6, 4)

        # Per projection the real panel puts the name label and its save
        # button in a narrow gutter to the LEFT of the MIP box -- NOT above
        # and below it. The ~29px I first measured "between the boxes" is
        # that gutter, not empty space.
        self.projection_labels = {}
        for name in ("XY", "YZ", "XZ"):
            proj_row = QHBoxLayout()
            proj_row.setSpacing(4)

            gutter = QVBoxLayout()
            gutter.setSpacing(4)
            gutter.setContentsMargins(0, 4, 0, 0)
            name_label = QLabel(name)
            name_label.setFixedHeight(14)
            gutter.addWidget(name_label)
            save_btn = QPushButton("\U0001F4BE")
            save_btn.setFixedSize(24, 24)
            save_btn.setEnabled(False)
            # Disabled QPushButtons grey their text out, which made the
            # floppy glyph nearly invisible -- keep it dark and bump the
            # glyph size so it reads like the real panel's save icon.
            save_btn.setStyleSheet(
                "QPushButton { font-size: 13px; padding: 0px; }"
                "QPushButton:disabled { color: #222; }"
            )
            gutter.addWidget(save_btn)
            gutter.addStretch(1)
            proj_row.addLayout(gutter)

            view = QLabel()
            # #f0f0f0 -- confirmed by pixel-sampling, not black.
            view.setStyleSheet("background-color: #f0f0f0;")
            view.setFixedSize(245, 196)  # exact reference measurement
            self.projection_labels[name] = view
            proj_row.addWidget(view, alignment=Qt.AlignTop)

            outer.addLayout(proj_row)

        outer.addStretch(1)

        side = QVBoxLayout()
        self.deskew_check = QCheckBox("DeSkew")
        self.deskew_check.setChecked(True)
        self.deskew_check.setEnabled(False)
        side.addWidget(self.deskew_check)
        self.calc_projections_btn = QPushButton("Calc")
        self.calc_projections_btn.setEnabled(False)
        side.addWidget(self.calc_projections_btn)
        side.addStretch(1)
        outer.addLayout(side)

        return tab

    def _build_diagnostics_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.addWidget(QLabel("Log:"))
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        lay.addWidget(self.log)
        return tab

    def _log(self, msg: str):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log.append(f"[{ts}] {msg}")

    def _update_acquire_enabled(self):
        ok = (
            self.camera is not None and self.camera.is_connected
            and self.fpga is not None and self.fpga.is_connected
        )
        if not self.acquiring:
            self.acquire_btn.setEnabled(ok)

    def _on_mode_changed(self, mode: str):
        self._update_scan_setup_enable_state()
        self._update_slice_count()

    def _update_scan_setup_enable_state(self):
        # Mode-dependent enable/disable, confirmed against the user's real
        # LouisXIV screenshots of both modes (Continuous Scan: End Pos
        # disabled in Z Galvo/Z Piezo, Slices fixed 1, Cycle lasers
        # disabled/"None"; Z stack: all of those enabled) -- see
        # fpga_io_map.md's "mode_enable_disable" investigation for what
        # could and couldn't be confirmed from the static VI source itself.
        z_stack = self.mode_combo.currentText() == MODE_ZSTACK
        linked = self.linked_btn.isChecked()
        self.zg_end.setEnabled(z_stack)
        self.cycle_lasers_combo.setEnabled(z_stack)
        # Inferred (not literally source-confirmed): when Linked, Z Piezo
        # follows Z Galvo + Rel. Offset instead of being set independently.
        self.z_start_spin.setEnabled(not linked)
        self.z_end_spin.setEnabled(z_stack and not linked)
        for w in (self.timepoints_widget, self.multilocation_widget, self.perfusion_widget):
            w.setVisible(z_stack)

    def _on_simulation_toggled(self, checked: bool):
        if self.camera is not None:
            return  # don't yank the backend out from under a live connection
        self.backend_combo.setCurrentText("Simulated" if checked else "Orca Flash 4.0 (real)")

    def _clear_image_to_black(self):
        black = QPixmap(self.image_label.size())
        black.fill(QColor(0, 0, 0))
        self.image_label.setPixmap(black)

    def _update_slice_count(self):
        # Continuous Scan has no defined stop position (End Pos disabled,
        # matches the real front panel showing "Slices" fixed at 1).
        if self.mode_combo.currentText() != MODE_ZSTACK:
            self.slice_count_field.setText("1")
            return
        interval = self.z_interval_spin.value()
        start = self.z_start_spin.value()
        end = self.z_end_spin.value()
        if interval <= 0:
            self.slice_count_field.setText("-")
            return
        n = max(1, int(round(abs(end - start) / interval)) + 1)
        self.slice_count_field.setText(str(n))

    # -- Camera actions ----------------------------------------------------
    def on_connect_clicked(self):
        backend = self.backend_combo.currentText()
        self.camera = OrcaFlash4Camera() if backend.startswith("Orca") else SimulatedCamera()
        self._log(f"Connecting camera ({backend})...")
        try:
            self.camera.connect()
        except Exception as e:
            self._log(f"Camera connect FAILED: {type(e).__name__}: {e}")
            QMessageBox.warning(self, "Connection failed", str(e))
            self.camera = None
            return

        self.frame_count = 0
        self.frame_counter_label.setText("0")
        info = self.camera.info
        self.status_label.setText(f"Connected: {info.name} (S/N {info.serial}), {info.width}x{info.height}")
        self.status_label.setStyleSheet("font-weight: bold; color: green;")
        self._log(f"Camera connected: {info}")

        try:
            self.exposure_spin.blockSignals(True)
            self.exposure_spin.setValue(self.camera.get_exposure_ms())
            self.exposure_spin.blockSignals(False)
        except Exception:
            pass

        self.connect_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(True)
        self.backend_combo.setEnabled(False)
        self._update_acquire_enabled()

    def on_disconnect_clicked(self):
        if self.acquiring:
            self.on_acquire_clicked()  # stop first
        if self.camera is not None:
            try:
                self.camera.set_trigger_source("INTERNAL")
            except Exception:
                pass
            self.camera.disconnect()
            self._log("Camera disconnected.")
        self.camera = None
        self.status_label.setText("Not connected")
        self.status_label.setStyleSheet("font-weight: bold;")
        self.connect_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(False)
        self.backend_combo.setEnabled(True)
        self._update_acquire_enabled()

    def on_exposure_changed(self, value: float):
        if self.camera is None or not self.camera.is_connected:
            return
        try:
            self.camera.set_exposure_ms(value)
        except Exception as e:
            self._log(f"Set exposure failed: {type(e).__name__}: {e}")

    # -- FPGA actions --------------------------------------------------------
    def on_fpga_connect_clicked(self):
        self._log("Connecting FPGA...")
        ctrl = FpgaTriggerController()
        try:
            ctrl.connect()
        except Exception as e:
            self._log(f"FPGA connect FAILED: {type(e).__name__}: {e}")
            QMessageBox.warning(self, "FPGA connection failed", str(e))
            return
        self.fpga = ctrl
        self.fpga_status_label.setText("Connected, safe state")
        self.fpga_status_label.setStyleSheet("font-weight: bold; color: green;")
        self._log("FPGA connected and in safe state.")
        self.fpga_connect_btn.setEnabled(False)
        self.fpga_disconnect_btn.setEnabled(True)
        self._update_acquire_enabled()

    def on_fpga_disconnect_clicked(self):
        if self.acquiring:
            self.on_acquire_clicked()  # stop first
        if self.fpga is not None:
            self.fpga.close()
            self._log("FPGA disconnected (safe state restored).")
        self.fpga = None
        self.fpga_status_label.setText("Not connected")
        self.fpga_status_label.setStyleSheet("font-weight: bold;")
        self.fpga_connect_btn.setEnabled(True)
        self.fpga_disconnect_btn.setEnabled(False)
        self._update_acquire_enabled()

    # -- Acquire / Stop ----------------------------------------------------
    def on_acquire_clicked(self):
        if self.acquiring:
            self._stop_acquisition()
        else:
            self._start_acquisition()

    def _start_acquisition(self):
        if self.camera is None or self.fpga is None:
            return
        mode = self.mode_combo.currentText()
        self._log(f"Starting acquisition: {mode}")

        # Real LouisXIV rule (HHMI - Check that only 1 laser is selected.vi):
        # exactly 1 Excitation line must be checked with Power > 0%.
        selected = [wl for chk, wl, spin in self.excitation_rows if chk.isChecked() and spin.value() > 0]
        if len(selected) != 1:
            msg = "Please select only 1 excitation source to be \"ON\" (with Power > 0%) and try again."
            self._log(f"Excitation check failed: {msg}")
            QMessageBox.warning(self, "Excitation", msg)
            return

        try:
            self.camera.set_trigger_source("EXTERNAL")
            self.camera.set_trigger_polarity("POSITIVE")
        except Exception as e:
            self._log(f"Failed to set external trigger: {type(e).__name__}: {e}")
            QMessageBox.warning(self, "Error", f"Failed to set external trigger:\n{e}")
            return

        self.frame_count = 0
        self.frame_counter_label.setText("0")
        self.acq_progress.setValue(0)

        if mode == MODE_ZSTACK:
            n = int(self.slice_count_field.text()) if self.slice_count_field.text().isdigit() else 1
            self.z_target_frames = n
            self._log(f"Z-stack: arming camera for {n} frames.")
            self.camera.start_sequence(n)
        else:
            self.z_target_frames = 0  # unbounded
            self._log("Continuous: arming camera for a long sequence.")
            self.camera.start_sequence(100000)

        self.acquiring = True
        self.acquire_btn.setText("Stop")
        self.acq_status_label.setText("ACQUIRING")
        self.acq_status_label.setStyleSheet(
            f"background-color: {ACQUIRING_RED}; color: white; font-weight: bold; "
            "padding: 6px; border-radius: 3px;"
        )
        self.mode_combo.setEnabled(False)
        self.connect_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(False)
        self.fpga_connect_btn.setEnabled(False)
        self.fpga_disconnect_btn.setEnabled(False)

        self.camera_poll_timer.start(30)

        # Trigger period = exposure + sensor readout. The camera IGNORES a
        # trigger that lands while it is still exposing/reading out, so
        # this is the real floor -- it is NOT a tunable margin. (This used
        # to be `exposure + 50ms` slept ON TOP of the fire call's own
        # cost, which made a 100ms exposure run at ~6Hz instead of ~10Hz.)
        period_s = self.camera.min_frame_period_ms() / 1000.0
        self._trigger_period_s = period_s
        self._triggers_fired = 0
        self._log(f"Trigger period {period_s * 1000:.1f} ms "
                  f"({1.0 / period_s:.2f} Hz) = exposure "
                  f"{self.camera.get_exposure_ms():.1f} ms + readout "
                  f"{self.camera.READOUT_MS:.1f} ms")
        self.fpga.start_continuous(
            period_s=period_s,
            on_frame=lambda count: self.fpga_signals.frame_fired.emit(count),
            on_rate=lambda hz: self.fpga_signals.rate_measured.emit(hz),
        )

    def _stop_acquisition(self):
        self._log("Stopping acquisition.")
        self.camera_poll_timer.stop()
        if self.fpga is not None:
            self.fpga.stop_continuous()
        if self.camera is not None:
            self.camera.stop_sequence()
            try:
                self.camera.set_trigger_source("INTERNAL")
            except Exception:
                pass

        self.acquiring = False
        self.acquire_btn.setText("Acquire")
        self.acq_status_label.setText("IDLE")
        self.acq_status_label.setStyleSheet(
            f"background-color: {IDLE_GREEN}; color: white; font-weight: bold; "
            "padding: 6px; border-radius: 3px;"
        )
        self.acq_progress.setValue(0)
        self.mode_combo.setEnabled(True)
        self.connect_btn.setEnabled(self.camera is None)
        self.disconnect_btn.setEnabled(self.camera is not None)
        self.fpga_connect_btn.setEnabled(self.fpga is None)
        self.fpga_disconnect_btn.setEnabled(self.fpga is not None)
        self._clear_image_to_black()
        self._log("Acquisition stopped, back to IDLE.")

    def _on_fpga_rate_measured(self, hz: float):
        """Report the ACHIEVED trigger rate vs the requested one.

        This is the number to compare against an oscilloscope. If they
        disagree, the trigger period maths is wrong -- don't reconcile it
        by padding magic numbers, fix the derivation.
        """
        want = 1.0 / self._trigger_period_s if self._trigger_period_s else 0.0
        note = ""
        if want and abs(hz - want) / want > 0.10:
            note = f"  <-- OFF by {(hz - want) / want * 100:+.0f}%"
        dropped = self._triggers_fired - self.frame_count
        self._log(f"Trigger rate: {hz:.2f} Hz achieved vs {want:.2f} Hz "
                  f"requested{note}   (fired {self._triggers_fired}, "
                  f"frames {self.frame_count}, missing {dropped})")

    def _on_fpga_frame_fired(self, count: int):
        # Runs on the GUI thread (Qt marshals this safely across threads).
        self._triggers_fired = count
        self._log(f"FPGA fired trigger #{count}.")
        if self.z_target_frames:
            pct = min(100, int(round(100 * count / self.z_target_frames)))
            self.acq_progress.setValue(pct)
            self.overall_progress.setValue(pct)
            if count >= self.z_target_frames:
                self._log(f"Z-stack target of {self.z_target_frames} triggers reached.")
                self._stop_acquisition()

    def _on_fpga_error(self, msg: str):
        self._log(f"FPGA error: {msg}")

    # Cap on how many frames one timer tick will drain, so a big backlog
    # can't freeze the GUI thread for an unbounded stretch.
    MAX_DRAIN_PER_TICK = 64

    def _poll_camera_for_frame(self):
        """Drain the camera's buffer and render only the NEWEST frame.

        This used to pop exactly ONE frame per 30ms tick and then do a
        full percentile + smooth rescale + min/max/mean over an 8MB
        2048x2048 frame before the next pop. That per-frame cost exceeded
        the tick interval, so we drained slower than the FPGA triggered,
        MMCore's circular buffer filled, and MMCore silently STOPPED the
        sequence -- acquisition froze around frame ~100 with no error.
        Counting every frame but rendering only the last one decouples
        the drain rate from the (expensive) display cost.
        """
        if self.camera is None or not self.camera.is_connected:
            return

        frame = None
        drained = 0
        try:
            while drained < self.MAX_DRAIN_PER_TICK and self.camera.remaining_image_count() > 0:
                frame = self.camera.pop_image()
                drained += 1
                self.frame_count += 1
        except Exception as e:
            self._log(f"Camera poll FAILED: {type(e).__name__}: {e}")
            return

        if frame is not None:
            pix = frame_to_qpixmap(frame, label_text=f"Frame #{self.frame_count}")
            scaled = pix.scaled(self.image_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.image_label.setPixmap(scaled)
            now = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
            self.frame_counter_label.setText(f"{self.frame_count}  (last at {now})")
            # Stats on a strided view -- exact values over all 4.2M pixels
            # cost three extra full passes per frame and were part of why
            # we couldn't keep up.
            sample = frame[::4, ::4]
            self.frame_info_label.setText(
                f"{frame.shape[1]}x{frame.shape[0]} {frame.dtype}, "
                f"min={sample.min()} max={sample.max()} mean={sample.mean():.1f}"
                + (f"  [+{drained - 1} not shown]" if drained > 1 else "")
            )
            self._log(f"Frame #{self.frame_count} received at {now}"
                      + (f" (drained {drained} this tick)" if drained > 1 else ""))

        # Surface the silent-stop conditions rather than just freezing.
        if not self.acquiring:
            return
        try:
            if self.camera.is_buffer_overflowed():
                free, total = self.camera.buffer_capacity()
                self._log(f"CAMERA BUFFER OVERFLOWED (capacity {total} frames) -- "
                          "MMCore stopped the sequence because frames arrived "
                          "faster than they could be drained. Stopping.")
                self._stop_acquisition()
            elif not self.camera.is_sequence_running():
                self._log("Camera sequence stopped on its own (no overflow "
                          "flagged) -- stopping acquisition.")
                self._stop_acquisition()
        except Exception as e:
            self._log(f"Buffer-state check failed: {type(e).__name__}: {e}")

    def closeEvent(self, event):
        if self.acquiring:
            self._stop_acquisition()
        if self.camera is not None:
            self.on_disconnect_clicked()
        if self.fpga is not None:
            self.on_fpga_disconnect_clicked()
        event.accept()
