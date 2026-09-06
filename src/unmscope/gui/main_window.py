"""Main application window -- a close visual replica of the real LouisXIV
`SPIM MAIN.vi` front panel, functionally scoped to Continuous Scan and Z
stack modes only.

Layout modeled directly on
`VI_Diagrams/SPIM/SPIM LV8.6 VIs/SPIM MAIN/SPIM MAINp.png` (the real
front-panel screenshot): lavender top bar (Acquire, mode dropdown,
Simulation checkbox, Status pill + progress bars, Exit), a tabbed left
panel (Scan Setup / Camera / Utilities), and a tabbed right side
(Waveforms / Images on top, Stack Projections / Diagnostics on the
bottom). LouisXIV's other tabs (Preferences, Adv Setup, Bckgrd, Blank,
Image Profile, Stack Profile, Row Profile, Timing) were dropped on the
user's 2026-09-05 cleanup call -- see CLAUDE.md "Cleanup decisions".

IMPORTANT -- honesty about what's real: controls laid out to match the
real panel but not wired to anything real are left disabled (greyed out)
so the window looks right without claiming functionality it doesn't have.
Wired to hardware today: Camera connect/exposure/ROI/sensor mode (Camera
tab); FPGA connect; Scan Setup's Excitation (the one checked row sets its
AOTF channel level -- see docs/aotf.md; LouisXIV's "one laser at a time"
gate is enforced), X galvo, Z galvo, Z piezo and Dither galvo (all feed
hardware.louisxiv_waveform.build_louisxiv_waveform, the words the FPGA
plays); Acquire/Stop and the live image display pipeline (gui/display.py:
Scale mapping, palette, Frames to Avg, Zoom to fit); the Waveforms tab
(FpgaScopePanel fed by FpgaScope); Stack Projections Calc/Save; and six of
the Utilities tools (um per V calibration, Sample Stage Control, Camera
Debug Panel, FPGA Scope, Reset HW, HW Config). Left greyed: the Timepoints
and Multi-location boxes (kept for later), the Cycle lasers combo, the
Images tab's drawing tools and camera selectors, and the five Utilities
tools not yet ported (View Z Lookup Table, X&Z Galvo offsets per AOTF ch,
FPGA Monitor, X Galvo Z Corrections, Imagine Optics).

Threading design (IMPORTANT -- see hardware/fpga_trigger.py and the
session's crash history): Camera is touched ONLY from the GUI thread via
QTimer polling (camera_poll_timer). FpgaTriggerController.start_free_run()
arms the FPGA once and lets it time the whole trigger train; the
controller's own background threads (the Wvfrm2 refill thread and the
monitor thread that reads the FPGA's trigger counter) never touch Qt
widgets -- their on_trigger_count/on_status/on_error callbacks only emit
thread-safe FpgaSignals, which Qt queues onto the GUI thread. Z-stack mode
uses the same free run with a bounded trigger count (the FPGA stops itself
when it is reached); the target is checked on the GUI thread in
_on_fpga_frame_fired, never by joining a controller thread from within a
callback. This keeps each hardware resource touched by exactly one
thread, matching the discipline that avoided further native-layer
crashes.
"""
from __future__ import annotations

import datetime
import re
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QTimer, QObject, Signal, QPoint
from PySide6.QtGui import QPixmap, QColor
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QFormLayout, QLabel, QPushButton, QComboBox, QDoubleSpinBox, QSpinBox,
    QTextEdit, QMessageBox, QSizePolicy, QCheckBox, QTabWidget, QSlider,
    QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar,
    QSplitter, QRadioButton, QButtonGroup, QToolButton, QScrollArea, QFrame,
    QApplication, QFileDialog, QToolTip,
)

from unmscope.hardware.camera import Camera, OrcaFlash4Camera, SimulatedCamera, other_camera_holders
from unmscope.hardware.fpga_trigger import FpgaTriggerController, TICKS_PER_S
from unmscope.hardware.fpga_scope import FpgaScope
from unmscope.hardware.waveform import COUNTS_PER_VOLT
from unmscope.hardware.louisxiv_waveform import build_louisxiv_waveform
from unmscope.config.calibration import load_calibration
from unmscope.analysis.projections import DEFAULT_STAGE_ANGLE_DEG, stack_projections
from unmscope.fileio.tiff_stack import save_tiff_stack, stack_path, write_acq_info
from unmscope.gui.scope_view import FpgaScopePanel
from unmscope.gui.camera_tab import CameraTab
from unmscope.gui.display import FrameAverager, display_range, render_frame
from unmscope.gui.utilities_tab import UtilitiesTab
from unmscope.gui.camera_debug_panel import CameraDebugPanel, CameraDebugStatus
from unmscope.gui.calibration_tab import CalibrationTab
from unmscope.gui.hw_config_dialog import show_hw_config_dialog
from unmscope.gui.sample_stage_dialog import SampleStageDialog
from unmscope.config.um_per_volt import load_calibration_from_unmscope_ini
from unmscope.config.waveform_config import AxisSettings, WaveformConfig
from unmscope.fileio.tiff_stack import read_tiff_stack
from unmscope.gui.widgets import bring_to_front

MODE_CONTINUOUS = "Continuous Scan"
MODE_ZSTACK = "Z stack"
# Trigger-period margins live on the Camera classes
# (Camera.EDGE_PERIOD_MARGIN_MS / SYNCREADOUT_PERIOD_MARGIN_MS), measured
# on the Orca 2026-09-03 -- see Camera.trigger_period_ms().

# -- LouisXIV-style palette (sampled from the real front panel) ------------
PANEL_BG = "#e8e8f8"       # light lavender window/tab background
GROUP_BG = "#f2f2fb"       # even lighter group-box interior
WHITE_BG = "#ffffff"
BORDER = "#8f8fbf"
IDLE_GREEN = "#2fa72f"
ACQUIRING_RED = "#cc3333"

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
QPushButton:hover:enabled {{ background-color: #f4f4ff; }}
QPushButton:pressed {{ background-color: #c8c8e8; border: 1px solid #5a5a9a; padding: 4px 7px 2px 9px; }}
QPushButton:disabled {{ color: #999; background-color: #eee; }}
QSlider::groove:horizontal {{ height: 5px; background: #bbb; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: #2f6fdb; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: white; border: 1px solid #666; width: 12px; margin: -5px 0; border-radius: 6px;
}}
QSlider:disabled::sub-page:horizontal {{ background: #90a4c8; }}
"""


def _narrow(w, width: int = 80):
    """Cap a spinbox's width so it doesn't crowd out its label in the
    real panel's tight two-column Scan Setup layout."""
    w.setMaximumWidth(width)
    return w


def _bordered_panel(name: str, width: int, margin: int) -> tuple[QFrame, QVBoxLayout]:
    """A bordered sub-panel of the panel background colour, fixed width,
    with a QVBoxLayout of ``margin`` on every side -- the Images tab's Max
    Counts and Display Options boxes are both this, at their own measured
    width/margin. The stylesheet is scoped by objectName (a bare "QFrame"
    rule would also border every QLabel, which is itself a QFrame)."""
    panel = QFrame()
    panel.setObjectName(name)
    panel.setFixedWidth(width)
    panel.setStyleSheet(f"QFrame#{name} {{ border: 1px solid {BORDER}; background-color: {GROUP_BG}; }}")
    col = QVBoxLayout(panel)
    col.setContentsMargins(margin, margin, margin, margin)
    return panel, col


class FpgaSignals(QObject):
    """Bridges FpgaTriggerController's background-thread callbacks to the
    GUI thread. Signal.emit() is thread-safe in Qt -- calling it from a
    non-GUI thread safely queues delivery to the connected slot on the GUI
    thread, rather than touching widgets directly from the wrong thread."""
    frame_fired = Signal(int)      # the FPGA's '# of triggers read' changed
    status = Signal(object)        # a FreeRunStatus snapshot, ~20/s
    error = Signal(str)


class MainWindow(QMainWindow):
    #: The class used by "FPGA Connect". Tests swap in
    #: unmscope.hardware.fake_fpga.FakeFpgaTriggerController to run the
    #: whole acquisition flow with no hardware.
    fpga_controller_factory = FpgaTriggerController

    def __init__(self):
        super().__init__()
        self.setWindowTitle("UNMScope -- LouisXIV (Python)")
        # Match the real SPIM MAIN.vi window exactly (1381x931, measured via
        # GetWindowRect off the live LouisXIV.exe front panel).
        self.resize(1381, 898)
        self.setStyleSheet(STYLESHEET)

        self.camera: Camera | None = None
        self.fpga: FpgaTriggerController | None = None
        self.scope: FpgaScope | None = None      # FPGA Scope, streams while the FPGA is connected
        # LouisXIV's um/V calibrations + voltage limits (SPIMProject.ini).
        # Calibrations come from UNMScope's own copy of SPIMProject.ini (the
        # um/V Cal tab edits that copy; LouisXIV's file is read-only input).
        try:
            self.calibration = load_calibration_from_unmscope_ini()
        except Exception as e:
            self.calibration = load_calibration()
            print(f"um/V calibration copy unavailable ({e}); using LouisXIV's ini read-only")
        # LouisXIV's 'Waveform' cluster (Low-Level Waveform Config), persisted
        # per user; the live fields feed build_louisxiv_waveform at scan start.
        self.waveform_config = WaveformConfig.load()
        self.last_waveform = None                # ScanWaveform of the current/last acquisition
        self.fpga_signals = FpgaSignals()
        self.fpga_signals.frame_fired.connect(self._on_fpga_frame_fired)
        self.fpga_signals.status.connect(self._on_fpga_status)
        self.fpga_signals.error.connect(self._on_fpga_error)
        self._trigger_period_s = 0.0
        self._triggers_fired = 0
        self._arm_time = 0.0           # perf_counter() when the FPGA was armed
        self._finishing = False        # Z-stack: all triggers fired, waiting for frames
        self._run_closing = 0          # extra closing triggers this run (1 in SYNCREADOUT)
        self._warmup_remaining = 0     # leading frames to discard this run
        self._warmup_discarded = 0
        self._sim_fed = 0              # "simulate on FPGA": FPGA trigger count already fed to the sim camera
        # SYNCREADOUT bookkeeping: True when the camera holds an exposure
        # that the next trigger will read out as a garbage frame -- after
        # any sync run (its last edge opened one) or an FPGA reset while
        # the camera is connected (the reset puts an edge on DIO4). False
        # only for a freshly connected camera. Measured, spikes/24.
        self._sync_exposure_open = False
        self._last_status_log_t = 0.0
        # Name of the blocking driver call that owns this thread, or None.
        # See _begin_blocking() for what it is guarding against.
        self._blocking_op: str | None = None
        # True while _stop_acquisition() is unwinding a run. Separate from
        # _blocking_op because a stop also runs from inside a blocking call.
        self._stopping = False
        # Set when a close arrives while a blocking call owns the thread;
        # _end_blocking re-issues it once the call has unwound.
        self._close_when_idle = False

        self.camera_poll_timer = QTimer(self)
        self.camera_poll_timer.timeout.connect(self._poll_camera_for_frame)

        self.frame_count = 0
        self.z_target_frames = 0
        # Frames kept in memory for the whole of a Z-stack, so the stack can
        # be saved (TIFF) or projected (Calc) after it finishes. None when not
        # retaining -- Continuous scan is unbounded and must never retain.
        # One entry per accepted slice, oldest first; warm-up/stale frames are
        # dropped before they reach here (see _poll_camera_for_frame).
        self._stack_frames: list | None = None
        self._last_frame = None                  # newest displayed frame, for 'Save Image'
        self._last_error_text = ""
        self.camera_debug_panel = None            # Utilities > Camera Debug Panel (LouisXIV's Debug Panel)
        self._hw_config_dlg = None                # Utilities > HW Config
        self.sample_stage_dialog = None           # Utilities > Sample Stage Control / Scan Setup > Configure
        self._frame_averager = FrameAverager(1)  # Images tab 'Frames to Avg'
        self._last_shown_frame = None
        self._last_shown_label = None
        self._last_projections: dict | None = None
        # Saving, the LouisXIV way: a data folder chosen once per session
        # (Prompt for Save Path.vi), and inside it one 'Cell<N>' folder per
        # stack (Find Next Experiment Folder Number.vi, prefix 'Cell').
        self._data_dir: Path | None = None
        self._current_exp_dir: Path | None = None
        self._save_base = "img"
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
    #    Scan Setup / Camera / Utilities panel.
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
        self.utilities_tab = UtilitiesTab(fpga_scope=self._show_fpga_scope,
                                          camera_debug=self._show_camera_debug_panel,
                                          hw_config=self._show_hw_config, sample_stage=self._show_sample_stage,
                                          um_per_volt=self._show_calibration_tab,
                                          reset_hw=self.on_reset_hw_clicked,
                                          waveform_config=self.waveform_config)
        self.utilities_tab.waveform_panel.changed.connect(self._on_waveform_config_changed)
        # The Scan Setup Dither box's sweeps / flyback ARE the cluster's Dither
        # Triangle Pulses / Dither Fract. Flyback: keep the two views in step.
        self.dg_sweeps.valueChanged.connect(self.utilities_tab.waveform_panel.dither_triangle_pulses.setValue)
        self.dg_flyback.valueChanged.connect(self.utilities_tab.waveform_panel.dither_fract_flyback.setValue)
        self._sync_dither_spins(self.waveform_config)
        tabs.addTab(self.utilities_tab, "Utilities")
        # um per V calibration: LouisXIV's Microns per Volt Settings GUI, opened
        # from the Utilities grid as its own window (the user dropped the
        # separate left tab, 2026-09-05); Save reloads the Calibration the
        # scan uses.
        self.calibration_tab = CalibrationTab(log=self._log)
        self.calibration_tab.saved.connect(self._on_calibration_saved)
        self.calibration_window = QWidget(self, Qt.Window)
        self.calibration_window.setWindowTitle("Microns per Volt Settings")
        cal_lay = QVBoxLayout(self.calibration_window)
        cal_lay.setContentsMargins(0, 0, 0, 0)
        cal_lay.addWidget(self.calibration_tab)
        self.calibration_window.resize(self.calibration_tab.minimumSize())
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
        # screenshots: LEFT = Excitation/Cycle lasers/Timepoints/Multi-
        # location (LouisXIV also has Stack time and Perfusion here;
        # dropped on the user's cleanup call); RIGHT = X galvo/Z Galvo/
        # Linked+Rel Offset/Z Piezo/Dither Galvo.
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
                f"{wavelength} nm enable. At Acquire this row's Power % becomes the level of "
                f"AOTF channel {self.EXCITATION_WAVELENGTHS_NM.index(wavelength)} (0..5 V from the ini's "
                "AOTF limits) for the whole run, 0 V at Stop. Exactly one row may be on. "
                "Forced off in simulate-on-FPGA mode. See docs/aotf.md."
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
        self.save_files_chk = QCheckBox("Save Files")
        form.addWidget(self.save_files_chk, 1, 0, 1, 3)

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
        # LouisXIV: 'Configure' shares event case [4] with the Utilities
        # 'Sample Stage Control' button -- both open the stage panel.
        cfg_btn.clicked.connect(self._show_sample_stage)
        self.multilocation_chk = chk
        self.multilocation_configure_btn = cfg_btn
        row.addWidget(chk); row.addWidget(cfg_btn); row.addStretch(1)
        lay.addLayout(row)

        self.locations_table = table = QTableWidget(3, 4)
        table.setHorizontalHeaderLabels(["X", "Y", "Z", "Z RO"])
        table.verticalHeader().setVisible(True)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setMaximumHeight(110)
        lay.addWidget(table)
        return box

    def _build_camera_tab(self) -> QWidget:
        # Connect/Disconnect + status live in the always-visible Hardware
        # Connection bar above the tabs; this tab is LouisXIV's Camera tab
        # (unmscope.gui.camera_tab) for an already-connected camera.
        self.camera_tab = CameraTab(
            get_camera=lambda: self.camera,
            pixel_size_um=lambda binning=1: self.calibration.detection.pixel_size_um(binning=binning),
            log=self._log)
        self.exposure_spin = self.camera_tab.exposure_spin
        self.exposure_spin.valueChanged.connect(self.on_exposure_changed)
        # LouisXIV runs this Orca in DCAM SYNCREADOUT trigger mode
        # (SPIMProject.ini [Cam1.Camera Settings] Sync Readout = TRUE): the
        # FPGA trigger interval IS the exposure. Applied at Acquire.
        self.sync_readout_chk = self.camera_tab.sync_readout_chk
        # Same reason as Scan Setup: keep the window at the real panel's size
        # and scroll the page (the connection bar above the tabs costs 119 px
        # of page height the real panel has).
        scroll = QScrollArea()
        scroll.setWidget(self.camera_tab)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        return scroll

    # -- Right side: top tab row (Waveforms/Images) over a
    #    bottom tab row (Stack Projections/Diagnostics) -----
    def _build_right_side(self) -> QSplitter:
        splitter = QSplitter(Qt.Vertical)

        self.top_tabs = top_tabs = QTabWidget()
        # The FPGA Scope (LouisXIV's 'HHMI - AI buffer.vi' front panel):
        # live traces from the FPGA's 'AI data' stream -- docs/fpga_scope.md.
        self.scope_panel = FpgaScopePanel()
        top_tabs.addTab(self.scope_panel, "Waveforms")
        top_tabs.addTab(self._build_images_tab(), "Images")
        top_tabs.setCurrentIndex(1)  # Images -- where the actual feed lives
        splitter.addWidget(top_tabs)

        bottom_tabs = QTabWidget()
        bottom_tabs.addTab(self._build_stack_projections_tab(), "Stack Projections")
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
        # Restored + wired on the user's call (2026-09-05): the display
        # controls drive the pipeline in unmscope.gui.display; the drawing
        # tools and the camera selectors stay greyed until they do something.
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
            ("zoom", "\U0001F50D"), ("pan", "\u270b"), ("crosshair", "\u2795"),
            ("line", "\u2571"), ("roi", "\u25ad"),
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
        self.save_image_btn.setToolTip("Save the displayed frame as a TIFF")
        self.save_image_btn.clicked.connect(self._on_save_image)
        col.addWidget(self.save_image_btn)

        col.addSpacing(10)
        self.cam_pan_btn = QPushButton("\u2725 Cam 1")
        col.addWidget(self.cam_pan_btn)

        col.addStretch(1)

        # Not wired: the drawing tools (profiles do not exist yet) and the
        # camera selectors (one camera).
        for w in (self.img_tool_zoom_btn, self.img_tool_pan_btn,
                  self.img_tool_crosshair_btn, self.img_tool_line_btn,
                  self.img_tool_roi_btn, self.cam_select_combo, self.cam_pan_btn):
            w.setEnabled(False)
        return col_widget

    def _build_max_counts_panel(self) -> QWidget:
        # A SEPARATE bordered sub-panel from display-options below --
        # confirmed by pixel-sampling the real front panel: there's a real
        # ~14px gap and each side has its own 1px border the full panel
        # height. 123px width measured off the real panel.
        panel, col = _bordered_panel("maxCountsPanel", 123, 6)
        col.addStretch(2)  # content sits in the lower ~2/3, not top-anchored

        # "Bright max" of LouisXIV's Scale = "By constant" (Given Range 0..Max
        # Counts); used when Scale to Counts is on.
        col.addWidget(QLabel("Max Counts"))
        self.max_counts_spin = QSpinBox()
        self.max_counts_spin.setRange(1, 65535)
        self.max_counts_spin.setValue(4000)
        col.addWidget(self.max_counts_spin)
        self.max_counts_slider = QSlider(Qt.Horizontal)
        self.max_counts_slider.setRange(1, 65535)
        self.max_counts_slider.setValue(4000)
        col.addWidget(self.max_counts_slider)
        self.max_counts_spin.valueChanged.connect(self._on_max_counts_spin)
        self.max_counts_slider.valueChanged.connect(self._on_max_counts_slider)

        col.addSpacing(4)
        col.addWidget(QLabel("Frames to Avg"))
        self.frames_to_avg_spin = QSpinBox()
        self.frames_to_avg_spin.setRange(1, 100)
        self.frames_to_avg_spin.setValue(1)
        self.frames_to_avg_spin.valueChanged.connect(self._on_frames_to_avg)
        col.addWidget(self.frames_to_avg_spin)

        col.addSpacing(4)
        # Indicator: the max count of the last completed stack (LouisXIV
        # computes it in its "calc stack max" thread).
        col.addWidget(QLabel("Stack Max"))
        self.stack_max_spin = QSpinBox()
        self.stack_max_spin.setRange(0, 65535)
        self.stack_max_spin.setValue(0)
        self.stack_max_spin.setReadOnly(True)
        self.stack_max_spin.setButtonSymbols(QSpinBox.NoButtons)
        col.addWidget(self.stack_max_spin)
        self.stack_max_slider = QSlider(Qt.Horizontal)
        self.stack_max_slider.setRange(0, 65535)
        self.stack_max_slider.setEnabled(False)
        col.addWidget(self.stack_max_slider)
        col.addStretch(1)
        return panel

    def _build_display_options_panel(self) -> QWidget:
        # The other sub-panel -- 214px, its own border, top-anchored
        # content with Text Info Overlay pinned to the bottom.
        panel, col = _bordered_panel("displayOptionsPanel", 214, 8)

        # "Pallete Color" -> IMAQ palette: Gray / Gradient / Rainbow
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
            rb.toggled.connect(self._on_display_option_changed)
        palette_row.addLayout(palette_col)
        col.addLayout(palette_row)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        form.setVerticalSpacing(2)
        self.scalebar_check = QCheckBox()
        self.zoom_to_fit_check = QCheckBox()
        self.zoom_to_fit_check.setChecked(True)
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
        self.text_info_overlay_check.setChecked(True)
        col.addWidget(self.text_info_overlay_check)
        for w in (self.scalebar_check, self.zoom_to_fit_check, self.autoscale_z_check,
                  self.scale_to_counts_check, self.text_info_overlay_check):
            w.toggled.connect(self._on_display_option_changed)
        return panel

    # -- Images tab: the display pipeline (LouisXIV "Set User Palette") ------
    def current_palette(self) -> str:
        if self.palette_gray_radio.isChecked():
            return "Gray"
        if self.palette_rainbow_radio.isChecked():
            return "Rainbow"
        return "Gradient"

    def _display_range_for(self, frame) -> tuple[float, float]:
        return display_range(frame, autoscale=self.autoscale_z_check.isChecked(),
                             scale_to_counts=self.scale_to_counts_check.isChecked(),
                             max_counts=self.max_counts_spin.value())

    def _scalebar_um_per_px(self) -> float | None:
        if not self.scalebar_check.isChecked():
            return None
        binning = self.camera.get_binning() if self.camera is not None else 1
        return self.calibration.detection.pixel_size_um(binning=binning)

    def _render(self, frame, label_text: str | None = None,
               scalebar_um_per_px: float | None = None) -> QPixmap:
        """The display pipeline for one frame: this window's Scale mapping
        and palette choice, plus whatever overlay the caller asks for.
        Shared by the live/loaded-frame display and the Calc projections."""
        lo, hi = self._display_range_for(frame)
        return render_frame(frame, palette=self.current_palette(), lo=lo, hi=hi,
                            label_text=label_text, scalebar_um_per_px=scalebar_um_per_px)

    def _display_frame(self, frame, label_text: str | None = None) -> None:
        """Show a raw frame through Frames to Avg, the Scale mapping, the
        palette, the overlays and Zoom to fit. Acquisition, saving and Calc
        keep using the raw frames."""
        shown = self._frame_averager.push(frame)
        self._last_shown_frame, self._last_shown_label = frame, label_text
        pix = self._render(shown, label_text=label_text if self.text_info_overlay_check.isChecked() else None,
                           scalebar_um_per_px=self._scalebar_um_per_px())
        if self.zoom_to_fit_check.isChecked():
            self.image_scroll.setWidgetResizable(True)
            self.image_label.setPixmap(pix.scaled(self.image_label.size(), Qt.KeepAspectRatio,
                                                  Qt.SmoothTransformation))
        else:
            self.image_scroll.setWidgetResizable(False)     # 1:1 pixels, scrollable
            self.image_label.setPixmap(pix)
            self.image_label.adjustSize()

    def _on_display_option_changed(self, *_):
        if getattr(self, "_last_shown_frame", None) is not None:
            self._frame_averager.reset()
            self._display_frame(self._last_shown_frame, self._last_shown_label)

    def _on_max_counts_spin(self, v: int):
        self.max_counts_slider.blockSignals(True)
        self.max_counts_slider.setValue(v)
        self.max_counts_slider.blockSignals(False)
        if self.scale_to_counts_check.isChecked():
            self._on_display_option_changed()

    def _on_max_counts_slider(self, v: int):
        self.max_counts_spin.blockSignals(True)
        self.max_counts_spin.setValue(v)
        self.max_counts_spin.blockSignals(False)
        if self.scale_to_counts_check.isChecked():
            self._on_display_option_changed()

    def _on_frames_to_avg(self, n: int):
        self._frame_averager.set_n(n)

    def _build_image_display(self) -> QScrollArea:
        self.image_label = QLabel("(no image yet)")
        self.image_label.setAlignment(Qt.AlignCenter)
        # #f0f0f0 -- the real panel's empty-picture-control color, confirmed
        # by pixel-sampling the live front panel (NOT black, despite how it
        # reads at a glance/thumbnail size).
        self.image_label.setStyleSheet("background-color: #f0f0f0; color: #666;")
        self.image_label.setMinimumSize(300, 300)

        self.image_scroll = scroll = QScrollArea()
        scroll.setWidget(self.image_label)
        scroll.setWidgetResizable(True)  # label still fills the viewport,
        # matching the pre-restyle behavior that _poll_camera_for_frame()
        # relies on (it scales each frame to self.image_label.size()) --
        # this box only ADDS the bounded/bordered look, doesn't change sizing.
        scroll.setStyleSheet(f"QScrollArea {{ border: 1px solid {BORDER}; }}")
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        return scroll

    def _build_stack_projections_tab(self) -> QWidget:
        # Layout matches SPIM MAINp.png's Stack Projections panel (box size
        # 245x196, gaps ~30px, measured off the live front panel); Calc/
        # DeSkew/Save are wired, see _on_calc_projections / _save_projection.
        tab = QWidget()
        outer = QHBoxLayout(tab)
        outer.setSpacing(8)
        outer.setContentsMargins(6, 4, 6, 4)

        # Per projection the real panel puts the name label and its save
        # button in a narrow gutter to the LEFT of the MIP box -- NOT above
        # and below it. The ~29px I first measured "between the boxes" is
        # that gutter, not empty space.
        self.projection_labels = {}
        self.projection_save_btns = {}
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
            save_btn.clicked.connect(lambda _c=False, n=name: self._on_save_projection(n))
            self.projection_save_btns[name] = save_btn
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
        side.addWidget(self.deskew_check)
        # Always enabled, like LouisXIV's [56] "Max Projs" latch: it projects
        # whatever stack is in memory and just logs when there is none.
        self.calc_projections_btn = QPushButton("Calc")
        self.calc_projections_btn.clicked.connect(self._on_calc_projections)
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
        if "FAILED" in msg or "ERROR" in msg or "Error" in msg:
            self._last_error_text = msg            # shown by the Camera Debug Panel

    # -- Save / Calc: the way LouisXIV does it ----------------------------------
    def _selected_channel_index(self) -> int:
        """LouisXIV's CH index in the filename: the one enabled Excitation row."""
        for i, (chk, _wl, spin) in enumerate(self.excitation_rows):
            if chk.isChecked() and spin.value() > 0:
                return i
        return 0

    def _ensure_data_dir(self) -> Path | None:
        """Prompt once per session for where to save (Prompt for Save Path.vi)."""
        if self._data_dir is None:
            chosen = QFileDialog.getExistingDirectory(self, "Choose the data folder")
            if not chosen:
                return None
            self._data_dir = Path(chosen)
        return self._data_dir

    @staticmethod
    def next_experiment_folder(data_dir: Path, prefix: str = "Cell") -> Path:
        """Find Next Experiment Folder Number.vi: scan for the highest
        '<prefix><N>' folder (e.g. Cell24) and return '<prefix><N+1>'."""
        highest = 0
        pat = re.compile(r"^" + re.escape(prefix) + r"(\d+)$")
        if data_dir.exists():
            for p in data_dir.iterdir():
                m = pat.match(p.name)
                if p.is_dir() and m:
                    highest = max(highest, int(m.group(1)))
        return data_dir / f"{prefix}{highest + 1}"

    def _acq_info(self) -> dict:
        cal = self.calibration
        sel = self._selected_channel_index()
        chk, wl, spin = self.excitation_rows[sel]
        info = {
            "Mode": self.mode_combo.currentText(),
            "Exposure (ms)": self.exposure_spin.value(),
            "Trigger mode": "SYNCREADOUT" if self.sync_readout_chk.isChecked() else "EDGE",
            "Slices": self.frame_count,
            "Z Piezo Interval (um)": self.z_interval_spin.value(),
            "Z Piezo Start (um)": self.z_start_spin.value(),
            "Z Piezo End (um)": self.z_end_spin.value(),
            "Excitation": f"{wl} nm at {spin.value():g} %",
            "XY pixel size (um)": round(cal.detection.xy_pixel_um, 5),
            "Stage angle (deg)": DEFAULT_STAGE_ANGLE_DEG,
        }
        if self.camera is not None and self.camera.info is not None:
            i = self.camera.info
            info["Camera"] = {"Model": i.name, "Serial": i.serial, "Width": i.width, "Height": i.height}
        return info

    def _on_stack_finished(self):
        """After any stop: offer the stack to Calc, and save it if Save Files
        is on and the stack is complete (partials are not kept, as LouisXIV's
        'Close Files and Delete Partials' does)."""
        stack = self.acquired_stack()
        have = stack is not None
        if not have:
            return
        stack_max = int(stack.max())                       # LouisXIV's "calc stack max"
        self.stack_max_spin.setValue(stack_max)
        self.stack_max_slider.setValue(stack_max)
        complete = bool(self.z_target_frames) and self.frame_count >= self.z_target_frames
        if self.save_files_chk.isChecked() and complete:
            self._save_stack(stack)

    def _save_stack(self, stack) -> Path | None:
        data_dir = self._ensure_data_dir()
        if data_dir is None:
            self._log("Save cancelled: no data folder chosen.")
            return None
        exp = self.next_experiment_folder(data_dir)
        cal = self.calibration
        ch = self._selected_channel_index()
        path = stack_path(exp, self._save_base, ch, 0)
        try:
            save_tiff_stack(path, stack, ome=True, pixel_size_um=cal.detection.xy_pixel_um,
                            z_step_um=self.z_interval_spin.value(),
                            channel_name=self.excitation_rows[ch][1])
            write_acq_info(exp, self._acq_info())
        except Exception as e:
            self._log(f"Stack save FAILED: {type(e).__name__}: {e}")
            QMessageBox.warning(self, "Save failed", str(e))
            return None
        self._current_exp_dir = exp
        self._log(f"Saved {stack.shape[0]}-slice stack: {path}  (+ AcqInfo.txt)")
        return path

    def _on_calc_projections(self):
        stack = self.acquired_stack()
        btn = self.calc_projections_btn
        if stack is None:
            self._log("Calc: no Z-stack in memory (a completed Z stack is needed; Continuous runs are not retained).")
            QToolTip.showText(btn.mapToGlobal(QPoint(0, -34)),
                              "No Z-stack in memory: acquire a Z stack first.", btn)
            return
        btn.setText("Calc…")            # visible while the projections compute
        btn.setEnabled(False)
        QApplication.processEvents()
        try:
            self._calc_projections(stack)
        finally:
            btn.setText("Calc")
            btn.setEnabled(True)

    def _calc_projections(self, stack):
        cal = self.calibration
        # The per-slice Sample Piezo positions LouisXIV reads back from each
        # image's Position cluster: Scan Setup start, stepping by the interval
        # towards the end position (signed), one per retained slice.
        start, end, step = self.z_start_spin.value(), self.z_end_spin.value(), self.z_interval_spin.value()
        signed = step if end >= start else -step
        s_positions = [start + k * signed for k in range(stack.shape[0])]
        projs = stack_projections(stack, s_positions=s_positions,
                                  xy_pixel_um=cal.detection.pixel_size_um(binning=1),
                                  deskew=self.deskew_check.isChecked())
        self._last_projections = projs
        for name, view in self.projection_labels.items():
            pix = self._render(projs[name])
            view.setPixmap(pix.scaled(view.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.projection_save_btns[name].setEnabled(True)
        self._log("Projections: " + ", ".join(f"{k} {v.shape[1]}x{v.shape[0]}" for k, v in projs.items())
                  + (" (deskewed)" if self.deskew_check.isChecked() else " (no deskew)"))

    def _projection_path(self, name: str) -> Path | None:
        folder = self._current_exp_dir or self._ensure_data_dir()
        if folder is None:
            return None
        ch, t = self._selected_channel_index(), 0
        return folder / f"{self._save_base}_CH{ch:02d}_{t:06d}_{name}MIP.tif"

    def _on_save_projection(self, name: str):
        if not self._last_projections or name not in self._last_projections:
            return
        path = self._projection_path(name)
        if path is None:
            return
        save_tiff_stack(path, self._last_projections[name], ome=True,
                        pixel_size_um=self.calibration.detection.xy_pixel_um)
        self._log(f"Saved {name} projection: {path}")

    def _save_frame_to(self, path) -> Path:
        return save_tiff_stack(path, self._last_frame, ome=True,
                               pixel_size_um=self.calibration.detection.xy_pixel_um)

    def _on_save_image(self):
        """HHMI - SPIM Save Image.vi: prompt for a path, save the image as U16 TIFF."""
        if self._last_frame is None:
            QMessageBox.information(self, "Save Image", "No image to save yet.")
            return
        start = str((self._data_dir or Path.home()) / f"{self._save_base}.tif")
        chosen, _f = QFileDialog.getSaveFileName(self, "Save image as TIFF", start, "TIFF (*.tif)")
        if not chosen:
            return
        self._log(f"Saved image: {self._save_frame_to(chosen)}")

    def acquired_stack(self):
        """The last retained Z-stack as an (n, H, W) array, or None if there
        is no stack in memory (never acquired, or Continuous mode)."""
        if not self._stack_frames:
            return None
        return np.stack(self._stack_frames)

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
        for w in (self.timepoints_widget, self.multilocation_widget):
            w.setVisible(z_stack)

    def _on_simulation_toggled(self, checked: bool):
        if self._blocking_op is not None or self.camera is not None:
            # Not just a live connection: self.camera is None for the whole
            # of a blocking open now, so the flag is what stops a pumped
            # toggle from swapping the backend underneath one.
            return
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

    # -- Blocking driver calls: the re-entrancy guard -----------------------
    #
    # Connect and Disconnect call into drivers that block this thread for
    # seconds (a cold Orca spends ~10 s inside DCAM's initializeDevice).
    # Windows drivers pump the native message queue while they block, so Qt
    # goes on delivering clicks that land in that window -- including a
    # second click on a button that still looks live. That is what killed
    # the 2026-09-04 05:47 run: the second click re-entered
    # on_connect_clicked() from inside the first, opened a second DCAM
    # session, rebound self.camera, and the access violation fired when
    # control unwound back into the half-initialised first open. See
    # docs/known_issues.md.
    #
    # The flag, not the disabled button, is the fix. Disabling a QPushButton
    # stops that button's own click; the flag makes re-entry a no-op from
    # every other path too (the other three buttons, a queued event, a
    # window close, a script calling the handler directly).
    def _begin_blocking(self, name: str) -> bool:
        """Claim the GUI thread for a blocking driver call.

        False means one is already running, and the caller must return at
        once without touching any hardware.
        """
        if self._blocking_op is not None:
            self._log(f"{name} ignored: {self._blocking_op} is still in progress "
                      "-- wait for it to finish.")
            return False
        self._blocking_op = name
        try:
            self._grey_out_for_blocking_call()
        except Exception:
            # A wedged flag would make every button a silent no-op AND, via
            # closeEvent, make the window unclosable with the hardware live.
            self._blocking_op = None
            raise
        return True

    def _grey_out_for_blocking_call(self):
        for w in (self.connect_btn, self.disconnect_btn, self.backend_combo,
                  self.fpga_connect_btn, self.fpga_disconnect_btn, self.acquire_btn):
            w.setEnabled(False)
        self.scope_panel.set_interactive(False)
        # The driver is about to block the event loop, so paint the greyed
        # buttons and the wait cursor now -- otherwise the user stares at a
        # live-looking button for ten seconds, which is why they click twice.
        # repaint() paints synchronously without dispatching input events;
        # processEvents() here would re-deliver the very click we are guarding.
        for w in (self.connect_btn, self.disconnect_btn,
                  self.fpga_connect_btn, self.fpga_disconnect_btn):
            w.repaint()
        QApplication.setOverrideCursor(Qt.WaitCursor)

    def _end_blocking(self):
        # Flag first: if anything below raises, the GUI must not be left
        # wedged in a state where every button is a silent no-op.
        self._blocking_op = None
        QApplication.restoreOverrideCursor()
        self._update_connection_buttons()
        if self._close_when_idle:
            # The user pressed X (or EXIT) during the call and closeEvent had
            # to refuse it. Honour it now, once this call has unwound.
            self._close_when_idle = False
            QTimer.singleShot(0, self.close)

    def _update_connection_buttons(self):
        """Put every connection control back in step with the real state.

        Called on the way out of every blocking call, so a failed connect
        re-enables its button without each error path remembering to.
        """
        if self._blocking_op is not None:
            # A call is still in flight -- an inner _stop_acquisition() must
            # not flicker the controls back to life mid-flight. _end_blocking
            # clears the flag first, then calls this, so nothing is lost.
            return
        self.scope_panel.set_interactive(True)
        if self.acquiring:
            # _start_acquisition greys all four for a reason: disconnecting
            # the camera or the FPGA out from under a run would leave the
            # galvos driven and the AOTF open. Only Stop stays live.
            for b in (self.connect_btn, self.disconnect_btn,
                      self.fpga_connect_btn, self.fpga_disconnect_btn):
                b.setEnabled(False)
            self.backend_combo.setEnabled(False)
            self.acquire_btn.setEnabled(True)
            return
        cam = self.camera is not None
        self.connect_btn.setEnabled(not cam)
        self.disconnect_btn.setEnabled(cam)
        self.backend_combo.setEnabled(not cam)
        fpga = self.fpga is not None
        self.fpga_connect_btn.setEnabled(not fpga)
        self.fpga_disconnect_btn.setEnabled(fpga)
        self._update_acquire_enabled()

    # -- Camera actions ----------------------------------------------------
    def on_connect_clicked(self):
        if not self._begin_blocking("Camera connect"):
            return
        try:
            self._connect_camera()
        finally:
            self._end_blocking()

    def _connect_camera(self):
        backend = self.backend_combo.currentText()
        # Built locally and published to self.camera only once it is open:
        # anything that runs while connect() blocks (a poll timer, a
        # re-entered handler) then finds either no camera or a working one,
        # never a half-initialised one.
        camera = OrcaFlash4Camera() if backend.startswith("Orca") else SimulatedCamera()
        if backend.startswith("Orca"):
            holders = other_camera_holders()
            if holders:
                # Not a hard gate: LouisXIV can be open without having
                # connected. But if it DOES hold the camera, a second DCAM
                # open can fault inside dcamapi.dll and kill this process
                # outright -- see docs/known_issues.md.
                self._log(f"WARNING: {', '.join(holders)} running. If it holds the camera, "
                          "connecting here can crash the DCAM driver (docs/known_issues.md).")
        self._log(f"Connecting camera ({backend})...")
        try:
            camera.connect()
        except Exception as e:
            self._log(f"Camera connect FAILED: {type(e).__name__}: {e}")
            try:
                camera.disconnect()      # drop a half-opened handle before a retry
            except Exception:
                pass
            QMessageBox.warning(self, "Connection failed", str(e))
            return
        self.camera = camera

        self.frame_count = 0
        self.frame_counter_label.setText("0")
        self._sync_exposure_open = False   # fresh device: nothing exposing
        info = self.camera.info
        self.status_label.setText(f"Connected: {info.name} (S/N {info.serial}), {info.width}x{info.height}")
        self.status_label.setStyleSheet("font-weight: bold; color: green;")
        self._log(f"Camera connected: {info}")

        try:
            self.exposure_spin.blockSignals(True)
            self.exposure_spin.setValue(self.camera.get_exposure_ms())
        except Exception:
            pass
        finally:
            self.exposure_spin.blockSignals(False)
        self.camera_tab.refresh_from_camera()

    def on_disconnect_clicked(self):
        if not self._begin_blocking("Camera disconnect"):
            return
        try:
            self._disconnect_camera()
        finally:
            self._end_blocking()

    def _disconnect_camera(self):
        if self.acquiring:
            self._stop_acquisition()
        # Un-published and the poll timer stopped BEFORE the blocking close,
        # the mirror of publishing only after a successful open. _stop_acquisition
        # deliberately leaves camera_poll_timer running for a grace window to
        # count in-flight frames, and camera.disconnect() pumps the native
        # queue -- so without this the 30 ms tick fires into a half-closed
        # device that still reports is_connected. That is the most likely
        # mechanism for the one-off "access violation right after Disconnect"
        # already recorded in docs/known_issues.md.
        self.camera_poll_timer.stop()
        camera, self.camera = self.camera, None
        if camera is not None:
            try:
                camera.set_trigger_active(camera.TRIGGER_EDGE)
                camera.set_trigger_source("INTERNAL")
            except Exception:
                pass
            camera.disconnect()
            self._log("Camera disconnected.")
        self.camera_tab.on_disconnected()
        self.status_label.setText("Not connected")
        self.status_label.setStyleSheet("font-weight: bold;")

    def on_exposure_changed(self, value: float):
        if self._blocking_op is not None:
            return  # a pumped spin-box event during a blocking driver call
        if self.camera is None or not self.camera.is_connected:
            return
        try:
            self.camera.set_exposure_ms(value)
        except Exception as e:
            self._log(f"Set exposure failed: {type(e).__name__}: {e}")
        self.camera_tab.refresh_actuals()

    # -- FPGA actions --------------------------------------------------------
    def on_fpga_connect_clicked(self):
        if not self._begin_blocking("FPGA connect"):
            return
        try:
            self._connect_fpga()
        finally:
            self._end_blocking()

    def _connect_fpga(self):
        self._log("Connecting FPGA...")
        ctrl = self.fpga_controller_factory()
        try:
            ctrl.connect()
        except Exception as e:
            self._log(f"FPGA connect FAILED: {type(e).__name__}: {e}")
            QMessageBox.warning(self, "FPGA connection failed", str(e))
            return
        self.fpga = ctrl
        if self.camera is not None and self.camera.reacts_to_dio4 and self.camera.is_sequence_running():
            # connect() did reset()+run(): that puts an edge on DIO4. A
            # camera that is armed (sequence running) in SYNCREADOUT now
            # holds an open exposure; one that is not capturing ignores it.
            self._sync_exposure_open = True
        if self.camera is not None and not self.camera.reacts_to_dio4:
            # Simulated camera + real FPGA = "simulate on FPGA": nothing may
            # move, so clamp every AO output right away, before any arm.
            clamped = ctrl.set_ao_clamp(True)
            self._log(f"SIMULATE ON FPGA: AO outputs clamped to 0 (AO Limit Max/Min = 0) -> "
                      f"{'verified' if clamped else 'READBACK MISMATCH'}.")
        self.fpga_status_label.setText("Connected, safe state")
        self.fpga_status_label.setStyleSheet("font-weight: bold; color: green;")
        self._log("FPGA connected and in safe state.")
        # Start the FPGA Scope on the same session (the Waveforms tab).
        try:
            self.scope = FpgaScope(ctrl)
            self.scope.start()
            self.scope_panel.set_scope(self.scope)
            self._log(f"FPGA Scope streaming {self.scope.channels} channels at {self.scope.fs_hz:,.0f} S/s "
                      "(Waveforms tab).")
        except Exception as e:
            self.scope = None
            self._log(f"FPGA Scope could not start: {type(e).__name__}: {e}")

    def on_fpga_disconnect_clicked(self):
        if not self._begin_blocking("FPGA disconnect"):
            return
        try:
            self._disconnect_fpga()
        finally:
            self._end_blocking()

    def _disconnect_fpga(self):
        if self.acquiring:
            self._stop_acquisition()
        if self.scope is not None:
            self.scope_panel.set_scope(None)
            try:
                self.scope.stop()
            except Exception:
                pass
            self.scope = None
        if self.fpga is not None:
            self.fpga.close()
            self._log("FPGA disconnected (safe state restored).")
        self.fpga = None
        self.fpga_status_label.setText("Not connected")
        self.fpga_status_label.setStyleSheet("font-weight: bold;")

    # -- Acquire / Stop ----------------------------------------------------
    def on_acquire_clicked(self):
        # The same guard as Connect, for the same reason. _start_acquisition
        # makes a long chain of blocking DCAM calls -- set_trigger_source,
        # set_exposure_ms, repair_exposure_if_lost (which re-initialises the
        # device in place: the very call that faulted) and prepare_sequence --
        # and only sets self.acquiring at the very end. Without this, a second
        # click during the arm re-entered _start_acquisition and armed twice.
        # The QMessageBox warnings in that path each run a nested event loop,
        # which is a re-entry vector on its own, slow driver or not.
        if not self._begin_blocking("Stop" if self.acquiring else "Acquire"):
            return
        try:
            if self.acquiring:
                self._stop_acquisition()
            else:
                self._start_acquisition()
        finally:
            self._end_blocking()

    # -- Utilities tab ---------------------------------------------------------
    def _on_calibration_saved(self, cal) -> None:
        """um/V Cal Save: LouisXIV's 'Force Waveform Recalc' -- the next
        Acquire builds the waveform from the saved numbers."""
        self.calibration = cal
        self._log(f"Calibration saved: X galvo {cal.x_galvo.um_per_volt:g} um/V, Z galvo "
                  f"{cal.z_galvo.um_per_volt:g} um/V, Z piezo {cal.z_piezo.um_per_volt:g} um/V "
                  f"(source: {cal.source}).")

    def _show_calibration_tab(self) -> None:
        """Utilities > um per V calibration: LouisXIV's [31] 'Edit um/V Cal'
        launches the settings GUI as its own window."""
        bring_to_front(self.calibration_window)

    def on_reset_hw_clicked(self) -> None:
        """Utilities > Reset HW. LouisXIV's [69] "Reset HW" sends the engine to
        its "Reset HW" state ("do a hardware reset on DAQ boards, reinitialize
        everything": Start hardware 1, Load Locals, Set Camera, Enable-disable,
        Start UI Loop). Here: stop a run, close and re-open the FPGA (reset +
        safe state) and the camera (same backend, settings re-applied), then
        the enable pass."""
        if not self._begin_blocking("Reset HW"):
            return
        try:
            self._reset_hw()
        finally:
            self._end_blocking()

    def _reset_hw(self) -> None:
        self._log("Reset HW: reinitialising everything.")
        if self.acquiring:
            self._stop_acquisition()
        had_fpga = self.fpga is not None
        had_camera = self.camera is not None
        if had_fpga:
            self._disconnect_fpga()                      # 'Reset DAQ cards': close = safe state
        if had_camera:
            self._disconnect_camera()
        if had_camera:
            self._connect_camera()                       # 'Set Camera': exposure / ROI / mode re-applied
        if had_fpga:
            self._connect_fpga()                         # 'Start hardware 1': reset + run + safe state
        self._update_connection_buttons()                # 'Enable-disable'
        self._log("Reset HW done: "
                  + ("camera reconnected, " if had_camera and self.camera is not None else "no camera, ")
                  + ("FPGA reconnected." if had_fpga and self.fpga is not None else "no FPGA."))

    def _show_hw_config(self) -> None:
        """Utilities > HW Config: LouisXIV's HW Configuration GUI on UNMScope's
        own ini copy; Apply only records the change here (LouisXIV's full
        Reset HW is not performed -- reconnect to pick the camera keys up)."""
        dlg = show_hw_config_dialog(parent=self, existing=self._hw_config_dlg)
        if dlg is not self._hw_config_dlg:
            dlg.applied.connect(self._on_hw_config_applied)
            self._hw_config_dlg = dlg

    def _on_hw_config_applied(self, cfg) -> None:
        cam = cfg.cameras[0] if getattr(cfg, "cameras", None) else None
        self._log("HW Config applied to UNMScope's SPIMProject.ini copy"
                  + (f" (Cam1: {cam.model_name if hasattr(cam, 'model_name') else ''} sync readout "
                     f"{getattr(cam, 'sync_readout', '?')}, binning {getattr(cam, 'binning', '?')})" if cam else "")
                  + "; takes effect on the next camera Connect.")

    def _show_sample_stage(self) -> None:
        """Utilities > Sample Stage Control and Scan Setup > Configure (both
        SPIM MAIN event case [4]): one non-modal panel, simulated stage."""
        if self.sample_stage_dialog is None:
            self.sample_stage_dialog = SampleStageDialog(
                rel_offset_provider=lambda: self.rel_offset_spin.value(), log=self._log, parent=self)
            self.sample_stage_dialog.rel_offset_recalled.connect(self.rel_offset_spin.setValue)
            self.sample_stage_dialog.sequence_changed.connect(self._refresh_locations_table)
            self._refresh_locations_table()
        bring_to_front(self.sample_stage_dialog)

    def _refresh_locations_table(self) -> None:
        """SPIM MAIN 'Update Positions': the Scan Setup table mirrors the
        Location Sequence (Name column dropped, 'no room')."""
        if self.sample_stage_dialog is None:
            return
        seq = self.sample_stage_dialog.sequence
        xyz = seq.positions_um()
        rel = seq.rel_offsets_um()
        self.locations_table.setRowCount(max(3, len(xyz)))
        for r in range(self.locations_table.rowCount()):
            vals = (f"{xyz[r][0]:.2f}", f"{xyz[r][1]:.2f}", f"{xyz[r][2]:.2f}", f"{rel[r]:.2f}") if r < len(xyz) else ("", "", "", "")
            for c, v in enumerate(vals):
                self.locations_table.setItem(r, c, QTableWidgetItem(v))

    def _camera_debug_status(self) -> CameraDebugStatus:
        """The counters LouisXIV's Debug Panel shows, from what this window
        keeps; camera reads are skipped during a blocking driver call."""
        cam = self.camera
        connected = cam is not None and cam.is_connected
        backlog = slots = 0
        if connected and self._blocking_op is None:
            try:
                backlog = int(cam.remaining_image_count())
                slots = int(cam.buffer_capacity()[1])
            except Exception as e:
                self._last_error_text = f"Camera status read FAILED: {type(e).__name__}: {e}"
        return CameraDebugStatus(
            exposures_downloaded=int(self.frame_count), total_expected=int(self.z_target_frames or 0),
            exposures_acquired=int(getattr(self, "_triggers_fired", 0)), download_backlog=backlog,
            save_backlog=0, image_buffer_slots=slots, open_file_refs=0,
            dcam_api=bool(connected and getattr(cam, "reacts_to_dio4", False)),
            error_text=self._last_error_text, connected=connected)

    def _show_camera_debug_panel(self):
        """Utilities > Camera Debug Panel: one non-modal window, raised if open."""
        if self.camera_debug_panel is None:
            self.camera_debug_panel = CameraDebugPanel(self._camera_debug_status, parent=self)
        bring_to_front(self.camera_debug_panel)

    def _show_fpga_scope(self):
        """Utilities > FPGA Scope: the Waveforms tab is the FPGA scope."""
        self.top_tabs.setCurrentWidget(self.scope_panel)

    def _sync_dither_spins(self, cfg: WaveformConfig) -> None:
        for spin, val in ((self.dg_sweeps, cfg.dither_triangle_pulses), (self.dg_flyback, cfg.dither_fract_flyback)):
            spin.blockSignals(True)
            try:
                spin.setValue(val)
            finally:
                spin.blockSignals(False)

    def _on_waveform_config_changed(self, cfg: WaveformConfig) -> None:
        self.waveform_config = cfg
        self._sync_dither_spins(cfg)
        try:
            cfg.save()
        except OSError as e:
            self._log(f"Could not save the waveform config: {e}")

    def load_stack_from_file(self, path: str) -> bool:
        """Load a saved stack and hold it as the retained stack, so the Images
        view shows it and Calc / Save work on it exactly as after an
        acquisition. (The Utilities 'View TIF stack' button was removed on the
        user's call, 2026-09-05; this stays for the launcher and tests.)"""
        if self.acquiring:
            self._log("View TIF stack: stop the acquisition first.")
            return False
        try:
            stack = read_tiff_stack(path)
        except Exception as e:
            self._log(f"View TIF stack FAILED: {type(e).__name__}: {e}")
            QMessageBox.warning(self, "View TIF stack", f"Could not read {path}:\n{e}")
            return False
        self._stack_frames = [np.ascontiguousarray(f) for f in stack]
        n = len(self._stack_frames)
        self.z_target_frames = n
        self.frame_count = n
        self._last_frame = self._stack_frames[-1]
        self._frame_averager.reset()
        self._display_frame(self._stack_frames[0], label_text=f"{Path(path).name} [1/{n}]")
        self.frame_counter_label.setText(f"{n}  (loaded {Path(path).name})")
        stack_max = int(stack.max())
        self.stack_max_spin.setValue(stack_max)
        self.stack_max_slider.setValue(stack_max)
        self._log(f"Loaded {n} x {stack.shape[1]}x{stack.shape[2]} from {path}.")
        return True

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
            if self.camera_tab.apply_to_camera(self.camera):
                self._log("Camera ROI / sensor mode applied at scan start.")
        except Exception as e:
            self._log(f"Failed to apply camera ROI / sensor mode: {type(e).__name__}: {e}")
            QMessageBox.warning(self, "Error", f"Failed to apply camera settings:\n{e}")
            return

        try:
            self.camera.set_trigger_source("EXTERNAL")
            self.camera.set_trigger_polarity("POSITIVE")
            self.camera.set_trigger_active(
                self.camera.TRIGGER_SYNCREADOUT if self.sync_readout_chk.isChecked()
                else self.camera.TRIGGER_EDGE)
            if self.camera.trigger_active == self.camera.TRIGGER_EDGE:
                # Push the exposure every start, AFTER the trigger mode. Seen
                # 2026-09-03: after a Z-stack (sequence + stop + back to
                # INTERNAL) the Orca reported exposure 0.0 ms on the next
                # EXTERNAL run and really did run at ~0 ms. The spin box is
                # the source of truth, exactly like LabVIEW pushes settings
                # at scan start. (SYNCREADOUT ignores the exposure setting:
                # the trigger interval is the exposure.)
                self.camera.set_exposure_ms(self.exposure_spin.value())
                if self.camera.repair_exposure_if_lost():
                    self._log("Camera had lost its exposure after the previous sequence stop "
                              "(Micro-Manager Hamamatsu adapter quirk, docs/known_issues.md); "
                              "re-initialised the camera in place and re-applied settings.")
                got = self.camera.get_exposure_ms()
                if abs(got - self.exposure_spin.value()) > 0.01 * self.exposure_spin.value():
                    self._log(f"WARNING: camera exposure reads {got:.3f} ms after setting "
                              f"{self.exposure_spin.value():.3f} ms.")
        except Exception as e:
            self._log(f"Failed to set external trigger: {type(e).__name__}: {e}")
            QMessageBox.warning(self, "Error", f"Failed to set external trigger:\n{e}")
            return

        self.frame_count = 0
        self.frame_counter_label.setText("0")
        self.acq_progress.setValue(0)
        self._frame_averager.reset()

        if mode == MODE_ZSTACK:
            n = int(self.slice_count_field.text()) if self.slice_count_field.text().isdigit() else 1
            self.z_target_frames = n
            self._stack_frames = []          # retain this stack for save / Calc
            self._log(f"Z-stack: arming camera; the FPGA will fire exactly {n} triggers.")
        else:
            self.z_target_frames = 0  # unbounded
            self._stack_frames = None        # Continuous: unbounded, do not retain
            self._log("Continuous: arming camera.")
        # The camera sequence is started once and left running until
        # Disconnect: stopping it is what loses the exposure (see
        # Camera.repair_exposure_if_lost) and, in SYNCREADOUT, does not even
        # clear an open exposure. The FPGA gates every frame and bounds a
        # Z-stack by itself, so the sequence never needs an exact count.
        sync = self.camera.trigger_active == self.camera.TRIGGER_SYNCREADOUT
        was_running = self.camera.is_sequence_running()
        self._warmup_remaining = self.camera.prepare_sequence()
        if not was_running:
            self._log("Camera sequence started (stays armed until Disconnect).")
        if sync and self._sync_exposure_open:
            # The first edge will read out the exposure left open by the
            # previous run's last edge (or by an FPGA reset): undefined
            # exposure, not a slice. Measured, spikes/24 E4/E3.
            self._warmup_remaining += 1
        self._warmup_discarded = 0
        stale = self.camera.discard_buffered_frames()
        if stale:
            self._log(f"Discarded {stale} leftover frame(s) from the camera buffer before arming.")
        if self._warmup_remaining:
            self._log(f"SYNCREADOUT: an exposure is already open; the first {self._warmup_remaining} "
                      "frame(s) will be discarded as warm-up.")

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

        # Trigger period = exposure + sensor readout + a small margin. The
        # camera silently IGNORES a trigger that lands while it is still
        # exposing/reading out. readout_ms() is the camera's own figure
        # (33.3 ms on this Orca -- not the 9.7 ms datasheet value that used
        # to be hard-coded, which made it drop every second trigger).
        exposure_ms = self.exposure_spin.value()
        period_ms = self.camera.trigger_period_ms(exposure_ms)
        period_s = period_ms / 1000.0
        sync = self.camera.trigger_active == self.camera.TRIGGER_SYNCREADOUT
        # free_run_timing() only needs exposure <= period; in SYNCREADOUT the
        # interval itself is the exposure.
        exposure_s = min(exposure_ms, period_ms) / 1000.0
        self._trigger_period_s = period_s
        self._triggers_fired = 0
        self._finishing = False
        self._run_closing = self.camera.closing_triggers()
        self._last_status_log_t = 0.0
        if sync:
            self._log(f"SYNCREADOUT: trigger period {period_ms:.2f} ms ({1000.0 / period_ms:.2f} Hz) "
                      f"= actual exposure (requested {exposure_ms:.2f} ms; camera floor "
                      f"{self.camera.readout_ms():.1f} ms readout + "
                      f"{self.camera.syncreadout_margin_ms():.2f} ms)"
                      + ("  <-- requested exposure is below the floor and was lengthened"
                         if period_ms > exposure_ms + 1e-6 else ""))
        else:
            self._log(f"EDGE: trigger period {period_ms:.2f} ms ({1000.0 / period_ms:.2f} Hz) = exposure "
                      f"{exposure_ms:.2f} ms + readout {self.camera.readout_ms():.1f} ms + margin "
                      f"{self.camera.edge_margin_ms():.2f} ms")

        # FPGA-timed free run: arm ONCE and let the FPGA's 40 MHz counter
        # time every pulse (LabVIEW's own scheme; docs/trigger_free_run_plan.md).
        # Bounded for a Z-stack (the FPGA stops itself after n), continuous
        # otherwise. Callbacks arrive on the controller's monitor thread and
        # are marshalled to the GUI thread through fpga_signals.
        # Bounded: the FPGA fires exactly frames + closing triggers
        # (SYNCREADOUT needs one more edge to read out the last frame).
        n_triggers = (self.z_target_frames + self._run_closing) if self.z_target_frames else None
        # "Simulate on FPGA": a simulated camera driven by the REAL FPGA's
        # trigger counter, with every AO output clamped to 0 by the FPGA's
        # own limit registers. Real timing, nothing moves, no camera.
        sim_on_fpga = not self.camera.reacts_to_dio4
        self._sim_fed = 0

        # ---- AO waveform for this scan (docs/wvfrm2_packing.md) -------------
        # X galvo: one sweep of the Scan Setup Range around Offset per
        # trigger, over the exposure, + flyback. Z piezo / Z galvo: constant
        # per slice, stepping by their Interval per slice in a Z stack.
        # Microns -> volts with LouisXIV's own calibrations; volts clamped
        # to the ini limits. Points at 100 us (4000 ticks).
        cal = self.calibration
        n_slices = self.z_target_frames or 1
        x_range_v = cal.x_galvo.um_to_v(self.xg_range.value())
        x_offset_v = cal.x_galvo.clamp_v(cal.x_galvo.um_to_v(self.xg_offset.value()))
        zp_start_v = cal.z_piezo.clamp_v(cal.z_piezo.um_to_v(self.z_start_spin.value()))
        zp_dir = 1.0 if self.z_end_spin.value() >= self.z_start_spin.value() else -1.0
        zp_step_v = cal.z_piezo.um_to_v(self.z_interval_spin.value()) * zp_dir if n_slices > 1 else 0.0
        zg_start_v = cal.z_galvo.clamp_v(cal.z_galvo.um_to_v(self.zg_start.value()))
        zg_dir = 1.0 if self.zg_end.value() >= self.zg_start.value() else -1.0
        zg_step_v = cal.z_galvo.um_to_v(self.zg_interval.value()) * zg_dir if n_slices > 1 else 0.0
        dither_range_v = cal.dither_galvo.um_to_v(self.dg_range.value())
        # LouisXIV's "Calculate Waveforms" (hardware/louisxiv_waveform.py): one
        # fast-axis line per trigger -- cubic accel, linear sweep, cubic decel,
        # flyback -- at the AO rate LouisXIV computes from exposure + cycle
        # time; slow axes stepped with an S-curve. Knobs from the Low-Level
        # Waveform Config (Utilities).
        wcfg = self.utilities_tab.waveform_panel.config()
        zg_moves, zp_moves = wcfg.z_axes_moving()
        try:
            lx = build_louisxiv_waveform(
                exposure_s=exposure_s, cycle_s=period_s, x_range_v=x_range_v, x_offset_v=x_offset_v,
                x_pixels=self.xg_pixels.value(), update_rate=wcfg.updates_per_pix,
                fractional_smoothing=wcfg.fract_smoothing, fractional_flyback=wcfg.fractional_flyback,
                x_bidirectional=not wcfg.x_single_direction, n_slices=n_slices,
                z_galvo_start_v=zg_start_v, z_galvo_step_v=zg_step_v if zg_moves else 0.0,
                z_piezo_start_v=zp_start_v, z_piezo_step_v=zp_step_v if zp_moves else 0.0,
                dither_range_v=dither_range_v, dither_pulses=wcfg.dither_triangle_pulses,
                dither_flyback_fraction=wcfg.dither_fract_flyback,
                cycle_margin_s=0.0)        # LouisXIV's rule: the block fills the cycle (measured OK on the card)
        except ValueError as e:
            self._log(f"Waveform calculation failed: {e}")
            QMessageBox.warning(self, "Waveform", str(e))
            return
        wf = lx.scan
        self.last_waveform = wf
        self._log(f"LouisXIV ramp: {lx.line.total_points} pts/line = accel {lx.line.tau_elem} + linear "
                  f"{lx.line.linear_points} + decel {lx.line.tau_elem} + return {lx.line.return_points}; "
                  f"AO rate {lx.rate.ao_rate_hz / 1e3:.3f} kHz (exposure rule {lx.rate.rate_for_exposure_hz:.0f}, "
                  f"flyback rule {lx.rate.rate_for_flyback_hz:.0f} pts/s, option {lx.rate.option}); "
                  f"Pix/ms {lx.rate.pixel_per_ms:.3f}; S-curve {lx.s_curve_points} pts."
                  + ("".join(" NOTE: " + n for n in lx.notes)))
        # Indicators on the cluster panel: Cam exp, Cycle time, Pixel/ms (Compute
        # AO rate from Cycle Time.vi) and the axes as Scan Setup has them.
        self.utilities_tab.waveform_panel.set_indicators(
            cam_exp_s=exposure_s, cycle_time_s=period_s, pixel_per_ms=lx.rate.pixel_per_ms,
            axes={"x": AxisSettings(0, self.xg_offset.value(), self.xg_interval.value(), self.xg_pixels.value()),
                  "xwvfrm": AxisSettings(0, self.xg_offset.value(), self.xg_interval.value(), self.xg_pixels.value()),
                  "z": AxisSettings(0, self.zg_start.value(), self.zg_interval.value(), n_slices),
                  "zpiezo": AxisSettings(0, self.z_start_spin.value(), self.z_interval_spin.value(), n_slices),
                  "dither": AxisSettings(0, 0.0, self.dg_range.value(), 1)})
        block_ticks = wf.points_per_trigger * wf.ticks_between_points
        # The Int-Sync high time must cover the block or the FPGA aborts the
        # block after the last trigger of a bounded run (spikes/29b, 29c).
        trigger_up_ticks = block_ticks + 40_000
        self._log(f"Waveform: {wf.points_per_trigger} points/trigger ({block_ticks / TICKS_PER_S * 1e3:.1f} ms) x "
                  f"{wf.n_slices} slice(s) = {len(wf.words)} words; X sweep {x_range_v * 1e3:+.1f} mV around "
                  f"{x_offset_v * 1e3:+.1f} mV ({self.xg_range.value():g} um @ {cal.x_galvo.um_per_volt:g} um/V); "
                  f"Z piezo {zp_start_v * 1e3:+.1f} mV step {zp_step_v * 1e3:+.2f} mV; "
                  f"Z galvo {zg_start_v * 1e3:+.1f} mV step {zg_step_v * 1e3:+.2f} mV; "
                  f"dither {dither_range_v * 1e3:.1f} mV pk-pk x {self.dg_sweeps.value():g} sweeps "
                  f"(flyback {self.dg_flyback.value():.2f}) (calibration: {cal.source}).")
        clamp_counts = self.scope_panel.test_clamp_counts() if sim_on_fpga else 0
        if sim_on_fpga:
            self._log("SIMULATE ON FPGA: AO clamp " + (f"+-{clamp_counts} counts (scope test clamp)" if clamp_counts
                                                        else "0 -- every AO output frozen at 0 V."))

        # ---- AOTF excitation level (docs/aotf.md) --------------------------
        # The one selected Excitation row (enforced above) sets its AOTF
        # channel to Power% -> volts -> DAC counts; every other channel 0.
        # The FPGA drives 'AOTF ch (V)' as a DC level while armed, so the
        # laser is on for the whole run. In simulate-on-FPGA mode
        # start_free_run() forces all AOTF levels to 0 (nothing may light).
        aotf_levels: dict[int, int] = {}
        aotf_desc = "off"
        for i, (chk, wl, spin) in enumerate(self.excitation_rows):
            if chk.isChecked() and spin.value() > 0:
                ch = cal.aotf.channel_for_row(i)
                v = cal.aotf.power_pct_to_v(spin.value())
                counts = int(round(v * COUNTS_PER_VOLT))
                aotf_levels[ch] = counts
                aotf_desc = (f"{wl} nm at {spin.value():g} % -> AOTF ch {ch} = {v:.3f} V ({counts} counts); "
                             f"other channels 0")
        if sim_on_fpga and aotf_levels:
            self._log(f"SIMULATE ON FPGA: AOTF forced OFF (would have been {aotf_desc}).")
        else:
            self._log(f"AOTF: {aotf_desc}.")

        self._arm_time = time.perf_counter()
        armed = self.fpga.start_free_run(
            period_s, exposure_s, n_triggers=n_triggers, clamp_ao=sim_on_fpga, clamp_counts=clamp_counts,
            ao_points_per_trigger=wf.points_per_trigger, ao_ticks_between_points=wf.ticks_between_points,
            ao_words=wf.words, trigger_up_ticks=trigger_up_ticks, aotf_levels=aotf_levels,
            on_trigger_count=lambda count: self.fpga_signals.frame_fired.emit(count),
            on_status=lambda st: self.fpga_signals.status.emit(st),
            on_error=lambda msg: self.fpga_signals.error.emit(msg),
        )
        if not armed:
            self._log(f"FPGA free-run arm FAILED: {self.fpga.last_error}")
            self._stop_acquisition()
            QMessageBox.warning(self, "FPGA", f"Could not arm the FPGA:\n{self.fpga.last_error}")
            return
        cycle, up = self.fpga.cycle_ticks, self.fpga.trigger_up_ticks
        self._log(f"FPGA armed once and free-running: Cycle(Ticks)={cycle} "
                  f"({cycle / TICKS_PER_S * 1000:.3f} ms), Trigger up (ticks)={up} "
                  f"({up / TICKS_PER_S * 1000:.1f} ms, covers the AO block); "
                  + (f"bounded to {n_triggers} triggers." if n_triggers else "continuous until Stop."))
        if sim_on_fpga:
            self.acq_status_label.setText("ACQUIRING (SIM on FPGA)")
            self._log("SIMULATE ON FPGA: frames come from the simulated camera, one per FPGA trigger "
                      f"(fed from '# of triggers read'); AO clamp verified = {self.fpga.ao_clamped}.")

    def _stop_acquisition(self):
        # Reached from the Stop click, the FPGA error signal, the camera poll
        # timer's buffer-state check, both disconnect paths and closeEvent --
        # and a native driver pump can deliver any of those from inside
        # another. Stopping twice would disarm an already-disarmed FPGA and
        # re-run the whole teardown, so a nested stop is a no-op.
        if self._stopping:
            return
        self._stopping = True
        try:
            self._stop_acquisition_body()
        finally:
            self._stopping = False

    def _stop_acquisition_body(self):
        self._log("Stopping acquisition.")
        self._finishing = False
        self._arm_time = 0.0
        if self.fpga is not None:
            if self.fpga.free_run_active:
                final = self.fpga.stop_free_run()
                self._triggers_fired = final
                if self._run_closing and final > 0:
                    # SYNCREADOUT: the last edge started an exposure nobody
                    # will read out until the next run's first edge.
                    self._sync_exposure_open = True
                self._log(f"FPGA disarmed (final accepted-trigger count {final}, "
                          f"frames received {self.frame_count}).")
            else:
                self.fpga.stop()
        # The camera sequence deliberately stays running (see
        # _start_acquisition). Keep polling for a grace period so the
        # frame(s) still exposing / in USB flight at Stop are counted
        # rather than abandoned, then stop the timer.
        if self.camera is not None and self._trigger_period_s > 0:
            QTimer.singleShot(int(2 * self._trigger_period_s * 1000) + 200, self._end_stop_grace)
        else:
            self.camera_poll_timer.stop()

        self.acquiring = False
        self.acquire_btn.setText("Acquire")
        self.acq_status_label.setText("IDLE")
        self.acq_status_label.setStyleSheet(
            f"background-color: {IDLE_GREEN}; color: white; font-weight: bold; "
            "padding: 6px; border-radius: 3px;"
        )
        self.acq_progress.setValue(0)
        self.mode_combo.setEnabled(True)
        self._update_connection_buttons()
        self._clear_image_to_black()
        self._log("Acquisition stopped, back to IDLE.")
        self._on_stack_finished()

    def _on_fpga_status(self, st):
        """~20/s snapshot of the FPGA's own counters while free-running,
        logged once a second. The achieved rate is from the hardware
        trigger counter, so it IS what an oscilloscope would show -- if it
        disagrees with the requested rate, fix the derivation, don't pad."""
        now = time.perf_counter()
        if now - self._last_status_log_t < 1.0:
            return
        self._last_status_log_t = now
        want = 1.0 / self._trigger_period_s if self._trigger_period_s else 0.0
        hz = st.achieved_hz
        note = ""
        # The estimate is quantised by the 50 ms monitor sampling, so only
        # flag a deviation once enough periods have accumulated.
        if want and hz and st.triggers_read >= 20 and abs(hz - want) / want > 0.05:
            note = f"  <-- OFF by {(hz - want) / want * 100:+.1f}%"
        self._log(f"FPGA free-run: {st.triggers_read} triggers, {hz:.3f} Hz achieved vs "
                  f"{want:.3f} Hz requested{note}; frames {self.frame_count}; "
                  f"ignored {st.triggers_ignored}; AO pts {st.ao_generated}; "
                  f"refill {st.refill_words_written} words")

    def _on_fpga_frame_fired(self, count: int):
        # Runs on the GUI thread (Qt marshals this safely across threads).
        # `count` is the FPGA's own '# of triggers read'.
        self._triggers_fired = count
        self._log(f"FPGA fired trigger #{count}.")
        if self.camera is not None and not self.camera.reacts_to_dio4 and count > self._sim_fed:
            # "Simulate on FPGA": hand the real edges to the simulated camera.
            self.camera.external_trigger(count - self._sim_fed)
            self._sim_fed = count
        if self.z_target_frames:
            total = self.z_target_frames + self._run_closing
            pct = min(100, int(round(100 * count / total)))
            self.acq_progress.setValue(pct)
            self.overall_progress.setValue(pct)
            if count >= total and not self._finishing:
                # The FPGA has stopped itself (bounded mode). The last
                # frame is still exposing/reading out -- stopping the
                # camera now would lose it, so wait for the frames.
                self._finishing = True
                self._log(f"Z-stack: all {total} triggers fired"
                          + (f" ({self._run_closing} closing)" if self._run_closing else "")
                          + "; waiting for the last frame(s) to land.")
                QTimer.singleShot(int(3 * self._trigger_period_s * 1000) + 500,
                                  self._finish_if_still_waiting)

    def _end_stop_grace(self):
        if self.acquiring:
            return  # a new acquisition started meanwhile and owns the timer
        self.camera_poll_timer.stop()
        self._log(f"Final: {self.frame_count} frames from {self._triggers_fired} triggers"
                  + (f" ({self._warmup_discarded} warm-up frame discarded)" if self._warmup_discarded else "")
                  + ".")

    def _finish_if_still_waiting(self):
        if self.acquiring and self._finishing:
            self._log(f"Z-stack: timed out waiting for frames "
                      f"({self.frame_count} of {self.z_target_frames} received). Stopping.")
            self._stop_acquisition()

    def _is_stale_pre_trigger_frame(self) -> bool:
        """True while it is physically too early for a triggered frame to
        exist: less than half a trigger period since the FPGA was armed.
        Only a real camera can have stale frames; the simulated one gets
        its edges in software and delivers instantly."""
        if self.camera is None or not self.camera.reacts_to_dio4:
            return False
        return (self._arm_time > 0 and self._trigger_period_s > 0
                and time.perf_counter() - self._arm_time < 0.5 * self._trigger_period_s)

    def _on_fpga_error(self, msg: str):
        self._log(f"FPGA error: {msg}")
        if self.acquiring:
            self._log("Stopping acquisition after FPGA error.")
            self._stop_acquisition()

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
        if self._blocking_op is not None:
            # A blocking driver call owns this thread and its native message
            # pump is what delivered this tick. Draining frames now would
            # re-enter MMCore from inside camera.disconnect().
            return
        if self.camera is None or not self.camera.is_connected:
            return

        frame = None
        drained = 0
        try:
            while drained < self.MAX_DRAIN_PER_TICK and self.camera.remaining_image_count() > 0:
                img = self.camera.pop_image()
                if self._is_stale_pre_trigger_frame():
                    # The Orca hands over one leftover frame right after a
                    # sequence starts in EXTERNAL mode (measured: it lands
                    # ~10 ms after arming, which no real exposure can).
                    # Counting it put every frame count off by one.
                    self._log("Discarded a stale pre-trigger frame from the camera buffer.")
                    continue
                if self._warmup_remaining > 0:
                    # Leading frame the camera backend told us to drop
                    # (Camera.prepare_sequence) -- undefined exposure.
                    self._warmup_remaining -= 1
                    self._warmup_discarded += 1
                    self._log("Discarded a leading warm-up frame (undefined exposure).")
                    continue
                frame = img
                drained += 1
                self.frame_count += 1
                if self._stack_frames is not None:
                    # Copy: pop_image may hand back a reused backing buffer.
                    # Bounded by the FPGA's exact trigger count, so this holds
                    # one full stack and no more.
                    self._stack_frames.append(np.ascontiguousarray(img).copy())
        except Exception as e:
            self._log(f"Camera poll FAILED: {type(e).__name__}: {e}")
            return

        if frame is not None:
            self._last_frame = frame
        if frame is not None and self.acquiring:   # during the stop grace period: count only
            self._display_frame(frame, label_text=f"Frame #{self.frame_count}")
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

        if not self.acquiring:
            return
        if self._finishing and self.frame_count >= self.z_target_frames:
            self._log(f"Z-stack complete: {self.frame_count} frames from "
                      f"{self._triggers_fired} triggers.")
            self._stop_acquisition()
            return
        # Surface the silent-stop conditions rather than just freezing.
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
        if self.sample_stage_dialog is not None:
            self.sample_stage_dialog.shutdown()
        # A close can arrive from the native message pump while a driver
        # call is still blocking this thread. Tearing the hardware down from
        # inside that call is the same nested-driver fault the guard exists
        # to prevent, so refuse the close and let the call finish.
        if self._blocking_op is not None:
            self._log(f"Close deferred: {self._blocking_op} is still in progress; "
                      "the window will close as soon as it finishes.")
            self._close_when_idle = True
            event.ignore()
            return
        if self.acquiring:
            self._stop_acquisition()
        if self.camera is not None:
            self.on_disconnect_clicked()
        if self.fpga is not None:
            self.on_fpga_disconnect_clicked()
        event.accept()
