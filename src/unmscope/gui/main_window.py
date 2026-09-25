"""Main application window -- a close visual replica of the real LouisXIV
`SPIM MAIN.vi` front panel, functionally scoped to Continuous Scan and Z
stack modes only.

Layout modeled directly on
`VI_Diagrams/SPIM/SPIM LV8.6 VIs/SPIM MAIN/SPIM MAINp.png` (the real
front-panel screenshot): lavender top bar (Acquire, mode dropdown,
Status pill + progress bars, Exit), a tabbed left
panel (Scan Setup / Camera / Utilities), and a tabbed right side
(Waveforms / Images on top, Stack Projections / Diagnostics on the
bottom). LouisXIV's other tabs (Preferences, Adv Setup, Bckgrd, Blank,
Image Profile, Stack Profile, Row Profile, Timing) were dropped on the
user's 2026-09-05 cleanup call -- see CLAUDE.md "Cleanup decisions".

IMPORTANT -- honesty about what's real: controls laid out to match the
real panel but not wired to anything real are left disabled (greyed out)
so the window looks right without claiming functionality it doesn't have.
Wired to hardware today: Camera connect/exposure/ROI/sensor mode (Camera
tab); FPGA connect; Scan Setup's Excitation (each checked row sets its
AOTF channel level -- see docs/aotf.md; LouisXIV's "one laser at a time"
rule is logged, not enforced), X galvo, Z galvo, Z piezo and Dither galvo
(all feed hardware.louisxiv_waveform.build_louisxiv_waveform, the words
the FPGA plays); Acquire/Stop and the live image display pipeline
(gui/display.py: Scale mapping, palette, Frames to Avg, Zoom to fit); the
Waveforms tab (FpgaScopePanel fed by FpgaScope); Stack Projections
Calc/Save; and six of the Utilities tools (um per V calibration, Sample
Stage Control, Camera Debug Panel, FPGA Scope, Reset HW, HW Config).
Left greyed: the Timepoints and Multi-location boxes (kept for later),
the Cycle lasers combo, the Images tab's drawing tools and camera
selectors, and the five Utilities tools not yet ported (View Z Lookup
Table, X&Z Galvo offsets per AOTF ch, FPGA Monitor, X Galvo Z
Corrections, Imagine Optics).

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
import math
import re
import threading
import time
from dataclasses import dataclass, field, replace
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
    QApplication, QFileDialog, QToolTip, QDialog,
)

from unmscope.hardware.camera import Camera, OrcaFlash4Camera, SimulatedCamera, other_camera_holders
from unmscope.hardware.fpga_trigger import FpgaTriggerController, MIN_TRIGGER_UP_TICKS, TICKS_PER_S
from unmscope.hardware.fpga_scope import (
    AI_CHANNEL_NAMES, DIGITAL_TRUE, IDX_DIO4, FpgaScope, ScopeSnapshot,
)
from unmscope.hardware.waveform import COUNTS_PER_VOLT, unpack_words
from unmscope.hardware.louisxiv_waveform import LouisXivWaveform, build_louisxiv_waveform
from unmscope.config.calibration import load_calibration
from unmscope.config import ui_state
from unmscope.analysis.projections import DEFAULT_STAGE_ANGLE_DEG, stack_projections
from unmscope.fileio.tiff_stack import (
    TIFF_EXTENSION, clean_base_filename, save_tiff_stack, stack_path, write_acq_info,
)
from unmscope.gui.save_image_dialog import SaveImageDialog
from unmscope.gui.scope_view import FpgaScopePanel
from unmscope.gui.camera_tab import CameraTab
from unmscope.gui.zoomable_image import ZoomableImageView
from unmscope.gui.display import FrameAverager, display_range, render_frame
from unmscope.gui.utilities_tab import UtilitiesTab
from unmscope.gui.camera_debug_panel import CameraDebugPanel, CameraDebugStatus
from unmscope.gui.calibration_tab import CalibrationTab
from unmscope.gui.hw_config_dialog import show_hw_config_dialog
from unmscope.gui.sample_stage_dialog import SampleStageDialog
from unmscope.gui.nikon_focus_dialog import real_focus_factory
from unmscope.gui.asi_stage_dialog import real_asi_factory
from unmscope.hardware.stage import StageError, Vec3
from unmscope.config.um_per_volt import load_calibration_from_unmscope_ini
from unmscope.config.waveform_config import (
    AxisSettings, WaveformConfig, camera_cycle_s, engine_times,
)
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


class StackSaveSignals(QObject):
    """Bridges the background stack-save thread back to the GUI thread --
    same reasoning as FpgaSignals. FIXED 2026-09-18 (user: "the GUI takes
    time to save acquired z stack"): an uncompressed multi-GB OME-TIFF write
    (LouisXIV's own format, deliberately uncompressed -- see
    fileio/tiff_stack.py) run synchronously on the GUI thread froze the
    whole window for however long that write took, worse over the network
    share this project lives on. The write itself is unchanged; only its
    thread is."""
    finished = Signal(object, object, int)   # (path, experiment dir, n_slices) on success
    failed = Signal(str)


class SequenceSignals(QObject):
    """Bridges the background sequence-transition thread (stage moves,
    timepoint-round waits) back to the GUI thread -- same reasoning as
    FpgaSignals/StackSaveSignals. Added 2026-09-22 for multi-timepoint/
    multi-position acquisition: the transition between two stacks can
    involve a real stage move (seconds) or a user-set Timepoint Delay
    (up to a full day), and doing either on the GUI thread would freeze
    the window for that whole span -- the exact class of bug the
    StackSaveSignals fix above already exists to avoid for the TIFF write,
    happening here far more often (every position, not just every stack)."""
    advance = Signal()      # transition finished normally -- arm the next stack
    error = Signal(str)     # stage move failed or timed out -- abort the sequence


@dataclass
class _SaveJob:
    """One queued background stack write -- see MainWindow._save_stack's
    2026-09-22 fix (queued, not dropped, when a save is already in flight)."""
    path: Path
    stack: object
    pixel_size_um: float
    z_step_um: float
    channel_name: str
    exp: Path
    acq_fields_extras: tuple[dict, dict]


@dataclass
class _AcqSequence:
    """One multi-timepoint x multi-position acquisition run: an ordered
    walk of (timepoint_idx, position_idx, xyz-or-None) steps. Built once by
    MainWindow._build_acquisition_sequence; advanced one step at a time as
    each stack finishes naturally. ``xyz`` is None when Multi-location is
    off (Timepoints=Multi alone still repeats at the one implicit position,
    xyz never moves)."""
    steps: list[tuple[int, int, Vec3 | None]]
    n_timepoints: int
    n_positions: int
    timepoint_delay_s: float
    index: int = 0

    def total_steps(self) -> int:
        return len(self.steps)

    def current(self) -> tuple[int, int, Vec3 | None]:
        return self.steps[self.index]

    def is_last(self) -> bool:
        return self.index >= len(self.steps) - 1

    def starts_new_timepoint_round(self) -> bool:
        """True when advancing from the current step to the next one
        crosses into a new timepoint round (so the transition should wait
        out timepoint_delay_s first)."""
        if self.is_last():
            return False
        return self.steps[self.index + 1][1] == 0 and self.steps[self.index + 1][0] != self.steps[self.index][0]


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
        self.save_signals = StackSaveSignals()
        self.save_signals.finished.connect(self._on_stack_save_finished)
        self.save_signals.failed.connect(self._on_stack_save_failed)
        self._saving_stack = False     # one background TIFF write at a time
        self._save_queue: list[_SaveJob] = []   # further stacks wait their turn, never dropped
        # -- multi-timepoint / multi-position sequence (2026-09-22) --------
        self.sequence_signals = SequenceSignals()
        self.sequence_signals.advance.connect(self._on_sequence_advance)
        self.sequence_signals.error.connect(self._on_sequence_error)
        self._sequence: _AcqSequence | None = None     # None = plain single-stack run
        self._sequence_abort = threading.Event()
        # True only at the ONE true natural per-stack-completion call site
        # (_on_fpga_frame_fired); every other _stop_acquisition() call site
        # (Stop click, FPGA error, buffer overflow, disconnect, closeEvent,
        # arm failure, frame-timeout) leaves this False, so a sequence
        # defaults to ABORT, not advance, everywhere except the one place
        # that means "this stack really finished" -- the safe direction for
        # something that drives a stage unattended.
        self._stack_finished_naturally = False
        self._trigger_period_s = 0.0
        self._triggers_fired = 0
        self._arm_time = 0.0           # perf_counter() when the FPGA was armed
        self._finishing = False        # Z-stack: all triggers fired, waiting for frames
        self._run_closing = 0          # extra closing triggers this run (1 in SYNCREADOUT)
        self._warmup_remaining = 0     # leading frames to discard this run
        self._warmup_discarded = 0
        self._stale_discarded = 0
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
        self._save_base = "img"          # replaced by what the save dialog is given
        #: Cell folder this run will be written into, fixed when the Save
        #: Image dialog was accepted, and the highest number handed out per
        #: parent folder this session -- see _next_experiment_name.
        self._experiment_dir: Path | None = None
        self._experiment_seen: dict[str, int] = {}
        #: The Save Image dialog's typed fields, kept for AcqInfo / OME.
        self._save_meta: dict = {"root": "", "user_name": "", "cell_type": "",
                                 "cell_labeling": "", "description": ""}
        #: LouisXIV's "Save Multi-position separate folders" global. Nothing
        #: sets it yet -- Multi-location is greyed -- but stack_path honours
        #: it, so wiring the panel switch later is the only step left.
        self.separate_position_folders = False
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
        self._restore_ui_state()

    # -- Remembering the user's own settings between sessions --------------
    #
    # "why do the software keep going to default options that you have
    # stored. Why not keep the last checked options given [by] the user as
    # the starting point for the next session." Everything named here is
    # written to ~/.unmscope/ui_state.json on exit and filled back in at
    # startup; see unmscope.config.ui_state for what is deliberately left
    # out (window geometry, Hold, connection state).

    def _persistent_widgets(self) -> dict:
        w = {
            "backend": self.backend_combo,
            "scan_mode": self.mode_combo,
            "cycle_lasers": self.cycle_lasers_combo,
            "xg_offset": self.xg_offset, "xg_range": self.xg_range, "xg_pixels": self.xg_pixels,
            "zg_interval": self.zg_interval, "zg_start": self.zg_start, "zg_end": self.zg_end,
            "z_interval": self.z_interval_spin, "z_start": self.z_start_spin, "z_end": self.z_end_spin,
            "dg_sweeps": self.dg_sweeps, "dg_flyback": self.dg_flyback,
        }
        for wavelength, (chk, _ch, spin) in zip(self.EXCITATION_WAVELENGTHS_NM, self.excitation_rows):
            w[f"exc_{wavelength}_on"] = chk
            w[f"exc_{wavelength}_pct"] = spin
        # Images tab display options: these are pure viewing preferences, and
        # having them snap back to Gradient/Autoscale every session is exactly
        # the same annoyance as the scope ticks.
        w.update({
            "img_max_counts": self.max_counts_spin,
            "img_frames_to_avg": self.frames_to_avg_spin,
            "img_palette_gray": self.palette_gray_radio,
            "img_palette_gradient": self.palette_gradient_radio,
            "img_palette_rainbow": self.palette_rainbow_radio,
            "img_scalebar": self.scalebar_check,
            "img_zoom_to_fit": self.zoom_to_fit_check,
            "img_autoscale_z": self.autoscale_z_check,
            "img_scale_to_counts": self.scale_to_counts_check,
            "img_text_info": self.text_info_overlay_check,
            "img_deskew": self.deskew_check,
        })
        w.update(self.camera_tab.persistent_widgets())
        w.update(self.scope_panel.persistent_widgets())
        return w

    def _restore_ui_state(self) -> None:
        try:
            state = ui_state.load()
        except Exception as e:                    # a settings file must never stop startup
            self._log(f"Could not read the saved settings: {type(e).__name__}: {e}")
            return
        if not state:
            return
        # The Save Image dialog's typed fields are not widgets on this window,
        # so they ride along by name. Retyping a root directory and a cell
        # type every session is exactly the annoyance the store exists for.
        for k in self._save_meta:
            v = state.get(f"save_{k}")
            if isinstance(v, str):
                self._save_meta[k] = v
        done = ui_state.apply(self._persistent_widgets(), state)
        self._log(f"Restored {len(done)} setting(s) from the last session "
                  f"({ui_state.default_path()}).")

    def _save_ui_state(self) -> None:
        try:
            state = ui_state.collect(self._persistent_widgets())
            state.update({f"save_{k}": v for k, v in self._save_meta.items()})
            ui_state.save(state)
        except OSError as e:
            self._log(f"Could not save the settings: {e}")

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
        # LouisXIV: Cam exp (s) never drives the camera -- the camera drives
        # it (see _push_engine_times). ROI sends "Set Camera" too ([73] ROI),
        # and the camera's own cycle depends on ROI height (camera_cycle_s's
        # vsize), so a sub-array change refreshes Cam exp / Cycle time here.
        self.camera_tab.roi_changed.connect(lambda _roi: self._push_engine_times())
        self.camera_tab.roi_changed.connect(lambda _roi: self._refresh_grid_tile_size())
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
        left_col.addWidget(self._build_aotf_gate_box())
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
                "AOTF limits) for the whole run, 0 V at Stop. Whatever rows are ticked with "
                "Power > 0 % are driven, in every mode including simulate-on-FPGA. "
                "See docs/aotf.md."
            )
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 1000)  # 0.1% steps
            slider.setMinimumWidth(40)
            # FIXED 2026-09-17 (user: "software slows down when some changes
            # are made"): spin.valueChanged below writes an AOTF register to
            # the FPGA AND reads it back (_push_aotf_levels ->
            # set_aotf_levels), synchronously on the GUI thread. Without
            # setTracking(False)/setKeyboardTracking(False), that round trip
            # ran once per drag tick while dragging the slider and once per
            # keystroke while typing a percentage -- the slider/spin now only
            # commit (and so only push to hardware) on release / Enter /
            # focus-loss, matching this codebase's own _spin() convention
            # (camera_tab.py) for the same reason.
            slider.setTracking(False)
            spin = _narrow(QDoubleSpinBox(), 78)
            spin.setRange(0.0, 100.0)
            spin.setDecimals(1)
            spin.setSingleStep(0.1)
            spin.setSuffix(" %")
            spin.setKeyboardTracking(False)
            spin.setValue(100.0 if is_default_on else 0.1)
            slider.setValue(int(round(spin.value() * 10)))
            chk.setChecked(is_default_on)
            slider.valueChanged.connect(lambda v, s=spin: s.setValue(v / 10.0))
            spin.valueChanged.connect(lambda v, sl=slider: sl.setValue(int(round(v * 10))))
            # Name the AOTF channel next to the wavelength: without it there
            # is no way to tell from this panel which line drives which AOTF
            # channel (and so which trace on the Waveforms tab is this laser).
            ch = self.calibration.aotf.channel_for_row(
                self.EXCITATION_WAVELENGTHS_NM.index(wavelength))
            lab = QLabel(f"{wavelength}  (AOTF {ch})")
            lab.setToolTip(f"{wavelength} nm drives AOTF channel {ch} -- the 'AOTF {ch}' trace "
                           "on the Waveforms tab.")
            row.addWidget(lab)
            row.addWidget(chk)
            row.addWidget(slider, stretch=1)
            row.addWidget(spin)
            form.addLayout(row)
            # LouisXIV's "Set AOTF" mode writes the level register live, so a
            # ticked row has to put its voltage on the pin straight away, not
            # only at Acquire (user, 2026-09-08: "I want the AOTF mode giving
            # out appropriate voltage everytime it is checked in"). Measured on
            # the card that day: 'AOTF ch (V)' reaches 'AOTF ch out (V)' with
            # nothing armed at all, so no run is needed for this to work.
            chk.toggled.connect(lambda *_: self._push_aotf_levels())
            spin.valueChanged.connect(lambda *_: self._push_aotf_levels())
            self.excitation_rows.append((chk, wavelength, spin))
        return box

    def _build_aotf_gate_box(self) -> QGroupBox:
        # NOT a LouisXIV control -- there is no equivalent on the real panel.
        # Host-timed 0V/on-volts square wave on whichever AOTF channel(s)
        # have a ticked Excitation row (fpga_trigger.py
        # start_aotf_digital_gate(), docs/aotf.md), built to the user's
        # explicit direction (2026-09-15): a digital gate "irrespective of
        # what mechanism [is] listed in the bitfile", adaptive to whatever
        # period/waveform the run actually uses. Off by default -- it is
        # new/experimental and changes what sits on its gated pins for
        # the whole run, so it should never surprise someone who has not
        # opted in. Verified on real hardware, spikes/39_aotf_digital_gate.py.
        box = QGroupBox("AOTF Digital Gate (TTL, adaptive)")
        box.setToolTip(
            "Host-timed square wave on whichever AOTF channel(s) have a "
            "ticked, driven Excitation row: 0 V when off, the voltage below "
            "when on. An unticked row's channel is never driven by this box. "
            "Leads each camera trigger and holds through the forward scan "
            "(the same on-duration aotf_gate() computes for this waveform), "
            "recomputed from the run's actual period/waveform -- not an "
            "FPGA-timed signal (docs/aotf.md has no engine for this), so "
            "precision is limited by Windows timer jitter (~1 ms) and "
            "degrades gracefully when the trigger-to-trigger gap is smaller "
            "than that."
        )
        form = QFormLayout(box)
        self.aotf_gate_chk = QCheckBox("Enable")
        self.aotf_gate_volts = _narrow(QDoubleSpinBox())
        self.aotf_gate_volts.setRange(0.0, 10.0)
        self.aotf_gate_volts.setDecimals(2)
        self.aotf_gate_volts.setSuffix(" V")
        self.aotf_gate_volts.setValue(3.3)
        self.aotf_gate_status = QLabel("idle")
        form.addRow(self.aotf_gate_chk)
        form.addRow("On level", self.aotf_gate_volts)
        form.addRow("Status", self.aotf_gate_status)
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
        # section, unlike Z Galvo/Z Piezo/Dither Galvo). Single/1 hides the
        # Timepoint Interval/Delay rows below (real panel, both user
        # screenshots); Multi shows them.
        #
        # ADDED 2026-09-22 (user, reference photo of the real panel mid
        # multi-position setup): "Single" was the only option; wired up
        # "Multi" for real multi-timepoint acquisition (see
        # _build_acquisition_sequence). Timepoint Interval is read-only/
        # computed, NOT a separate wait timer -- no VI has been read for
        # this rig's exact semantics, and the reference photo's own numbers
        # (4 positions x 24.52 s Stack Acq Time is close to the shown
        # 1:40.52 Timepoint Interval) suggest it reports how long one full
        # timepoint round takes, not something to pad out to. Treating a
        # possibly-computed field as a real wait if it isn't would silently
        # add unwanted delays -- the safe failure direction is read-only.
        # Timepoint Delay is the one true editable extra-pause-between-
        # rounds field.
        box = QWidget()
        form = QGridLayout(box)
        form.setContentsMargins(0, 0, 0, 0)
        form.addWidget(QLabel("Timepoints"), 0, 0)
        self.tp_combo = tp_combo = QComboBox()
        tp_combo.addItems(["Single", "Multi"])
        tp_combo.setMinimumWidth(90)
        form.addWidget(tp_combo, 0, 1)
        self.tp_spin = tp_spin = _narrow(QSpinBox(), 55)
        tp_spin.setRange(1, 9999)
        tp_spin.setValue(1)
        form.addWidget(tp_spin, 0, 2)
        self.save_files_chk = QCheckBox("Save Files")
        form.addWidget(self.save_files_chk, 1, 0, 1, 3)

        self.tp_interval_field = _narrow(QLineEdit("00:00:00"), 110)
        self.tp_interval_field.setReadOnly(True)
        self.tp_interval_field.setToolTip(
            "Computed: how long one full timepoint round (all positions) "
            "takes. Not independently settable -- see Timepoint Delay for "
            "an extra pause between rounds.")
        self.tp_delay_spin = _narrow(QDoubleSpinBox(), 110)
        self.tp_delay_spin.setRange(0.0, 86400.0)
        self.tp_delay_spin.setDecimals(4)
        self.tp_delay_spin.setSuffix(" s")
        self.tp_delay_spin.setToolTip("Extra pause after finishing all positions in a "
                                      "timepoint round, before the next one starts.")

        self.tp_extra_rows: list[QWidget] = []
        for r, (label, widget) in enumerate([
            ("Timepoint Interval", self.tp_interval_field),
            ("Timepoint Delay", self.tp_delay_spin),
        ], start=2):
            lab = QLabel(label)
            form.addWidget(lab, r, 0)
            form.addWidget(widget, r, 1, 1, 2)
            self.tp_extra_rows += [lab, widget]

        for r, (label, default) in enumerate([
            ("Stack Acq. Time", "00:00:00"),
            ("Total time", "00:00:00"),
        ], start=4):
            form.addWidget(QLabel(label), r, 0)
            field = _narrow(QLineEdit(default), 110)
            field.setReadOnly(True)
            form.addWidget(field, r, 1, 1, 2)
            if label == "Stack Acq. Time":
                self.stack_acq_time_field = field
            else:
                self.total_time_field = field

        tp_combo.currentTextChanged.connect(self._on_timepoints_mode_changed)
        tp_spin.valueChanged.connect(self._update_time_estimates)
        self.tp_delay_spin.valueChanged.connect(self._update_time_estimates)
        self._on_timepoints_mode_changed(tp_combo.currentText())
        return box

    def _on_timepoints_mode_changed(self, text: str) -> None:
        multi = text == "Multi"
        self.tp_spin.setEnabled(multi)
        for w in self.tp_extra_rows:
            w.setVisible(multi)
        self._update_time_estimates()

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
        # ADDED 2026-09-22: previously read from nowhere -- ticking it had
        # no effect at all. The table itself was already a live, correct
        # mirror of the stage's position sequence (_refresh_locations_table);
        # this just makes the checkbox gate whether an Acquire actually
        # steps through it (_build_acquisition_sequence) and grey the table
        # when not in use, matching Single/Multi-location's other enable
        # state (_update_scan_setup_enable_state).
        chk.toggled.connect(lambda on: table.setEnabled(on))
        chk.toggled.connect(self._update_time_estimates)
        table.setEnabled(False)
        return box

    def _build_camera_tab(self) -> QWidget:
        # Connect/Disconnect + status live in the always-visible Hardware
        # Connection bar above the tabs; this tab is LouisXIV's Camera tab
        # (unmscope.gui.camera_tab) for an already-connected camera.
        self.camera_tab = CameraTab(
            get_camera=lambda: self.camera,
            pixel_size_um=lambda binning=1: self.calibration.detection.pixel_size_um(binning=binning),
            log=self._log,
            magnification=self.calibration.detection.magnification,
            camera_pixel_um=self.calibration.detection.camera_pixel_um,
            set_detection=self._on_set_detection)
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
        # FIXED 2026-09-17: with Scale to Counts on, each tick re-renders the
        # full frame (and smooth-scales it if Zoom to fit is on too) -- only
        # do that on commit, not per drag/keystroke tick (same reasoning as
        # the Excitation Power% controls above).
        self.max_counts_spin.setKeyboardTracking(False)
        col.addWidget(self.max_counts_spin)
        self.max_counts_slider = QSlider(Qt.Horizontal)
        self.max_counts_slider.setTracking(False)
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

            # ZoomableImageView (user, 2026-09-23: "stack projections should
            # also be zoomable"): scroll to zoom, drag to pan, double-click
            # to reset -- see gui/zoomable_image.py. Same fixed viewport
            # size as the plain QLabel it replaces, so the box itself still
            # matches the reference measurement.
            view = ZoomableImageView()
            # #f0f0f0 -- confirmed by pixel-sampling, not black.
            view.setStyleSheet("background-color: #f0f0f0; border: none;")
            view.setFixedSize(245, 196)  # exact reference measurement
            view.setToolTip("Scroll to zoom, drag to pan, double-click to reset.")
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
        # FIXED 2026-09-17: unbounded -- a long session (or a control that
        # logs on every commit, like the Excitation rows) grew this
        # indefinitely, and QTextEdit gets progressively slower to append to
        # as its document grows. Cap it; oldest lines drop off.
        self.log.document().setMaximumBlockCount(5000)
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
        """The data folder, asking for it only if nothing is set yet.

        The fallback for paths that run AFTER an acquisition -- saving a
        stack, saving a projection. Those must not put a modal dialog up
        over finished data. Acquire uses `_prompt_save_image`, which asks
        every time.
        """
        return self._data_dir if self._data_dir is not None else self._prompt_save_image()

    def _prompt_save_image(self) -> Path | None:
        """LouisXIV's **Save Image** dialog (`File IO/OME Save Image
        Dialog.vi`), put up now whatever is already set.

        Acquire calls this on EVERY run with Save Files ticked: "everytime I
        hit acquire button and the save file is checked the software needs
        to prompt regarding the save options." It opens on the last values,
        so confirming an unchanged setup is one click, and the Experiment
        box names the Cell folder this run will actually land in.

        The save directory is BUILT from the typed fields, not picked -- the
        VI carries the rule as a comment on its own diagram:
        root / username / celltype / labeling / YYMMDD / cell[n] /
        location[n] / n.tif. This port had a bare save-file dialog instead,
        taking the folder from whatever file name was typed, so User Name /
        Cell Type / Cell Labeling / Experiment Description were never
        collected at all and went into AcqInfo.txt empty.

        What is returned is the DATE folder, the level above ``Cell<N>``:
        `_save_stack` calls `next_experiment_folder` on it for every stack,
        so a second run lands in the next Cell folder instead of on top of
        the first. The dialog's Experiment box is that same calculation.
        """
        dlg = SaveImageDialog(
            self, root=self._save_meta["root"], user_name=self._save_meta["user_name"],
            cell_type=self._save_meta["cell_type"],
            cell_labeling=self._save_meta["cell_labeling"],
            description=self._save_meta["description"],
            next_experiment=self._next_experiment_name,
        )
        if dlg.exec() != QDialog.Accepted:
            return None
        vals = dlg.values()
        folder = dlg.date_folder()
        if folder is None:
            return None
        try:
            folder.mkdir(parents=True, exist_ok=True)     # "created if it doesn't exist"
        except OSError as e:
            self._log(f"Could not create {folder}: {e}")
            QMessageBox.warning(self, "Save Image", f"Could not create\n{folder}\n\n{e}")
            return None
        self._data_dir = folder
        self._experiment_dir = folder / vals["experiment"] if vals["experiment"] else None
        # Remember what was handed out, so the next run in the SAME parent
        # folder gets the next number even if this one never wrote anything
        # (cancelled, aborted, or a partial stack that is not kept).
        n = self._experiment_number(vals["experiment"])
        if n:
            key = str(folder)
            self._experiment_seen[key] = max(self._experiment_seen.get(key, 0), n)
        self._save_meta = {k: vals[k] for k in
                           ("root", "user_name", "cell_type", "cell_labeling", "description")}
        self._log(f"Saving under {self._data_dir} (user '{vals['user_name']}', cell type "
                  f"'{vals['cell_type']}', labeling '{vals['cell_labeling']}'); "
                  f"experiment folder {vals['experiment']}.")
        return self._data_dir

    EXPERIMENT_PREFIX = "Cell"

    @staticmethod
    def _experiment_number(name: str) -> int:
        m = re.match(r"^" + MainWindow.EXPERIMENT_PREFIX + r"(\d+)$", name or "")
        return int(m.group(1)) if m else 0

    def _next_experiment_name(self, base: Path) -> str:
        """The Cell folder a run in ``base`` should use.

        The filesystem alone is not enough: "increase the Cell counter by one
        increment if it is the same parent folder". A run that wrote nothing
        -- cancelled, aborted, or a partial stack, which LouisXIV does not
        keep either -- leaves no folder behind, so a scan would hand out the
        same number again and the next run would look like the first. So the
        number is the higher of what is on disk and what this session has
        already handed out for this parent.
        """
        on_disk = self._experiment_number(self.next_experiment_folder(base).name)
        seen = self._experiment_seen.get(str(base), 0)
        return f"{self.EXPERIMENT_PREFIX}{max(on_disk, seen + 1)}"

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

    def _acq_info(self, stack=None, position_xyz: Vec3 | None = None) -> tuple[dict, dict]:
        """(LouisXIV's AcqInfo fields, our extras block).

        The keys and their order are LouisXIV's, from `Companion Metadata
        Cluster to String.vi` -- see `fileio/tiff_stack.ACQ_INFO_FIELDS`.
        Fields whose panel controls were never ported are written empty
        rather than omitted, because LouisXIV writes them unconditionally
        and an empty panel field there gives an empty value here; only the
        position fields have a real skip.

        The extras block carries what this acquisition used that LouisXIV's
        format has nowhere to put. Dropping it would lose information the
        file used to record, so the user chose to keep it, clearly fenced
        off under its own heading.

        ``position_xyz`` (ADDED 2026-09-22): the stage position this stack
        was taken at, or None for a single-position run. Sets
        Multi-positionAcq and the PositionX/Y/Z_mm fields -- previously
        Multi-positionAcq was unconditionally False and the position keys
        were entirely missing from the dict (not just skipped by
        render_acq_info's own skip? logic), because multi-position was
        never wired up before now.
        """
        cal = self.calibration
        sel = self._selected_channel_index()
        chk, wl, spin = self.excitation_rows[sel]
        exposure_ms = float(self.exposure_spin.value())
        # Frame size from the stack actually being saved where we have it;
        # the camera is the fallback for callers that pass none.
        if stack is not None and getattr(stack, "ndim", 0) == 3:
            slices, height, width = (int(n) for n in stack.shape)
        else:
            info = self.camera.info if self.camera is not None else None
            width = int(info.width) if info else 0
            height = int(info.height) if info else 0
            slices = int(self.frame_count)
        pixel_um = float(cal.detection.xy_pixel_um)
        period_ms = (self.camera.trigger_period_ms(exposure_ms)
                     if self.camera is not None else exposure_ms)

        fields = {
            "SizeX_px": width,
            "SizeY_px": height,
            "SizeZ_px": slices,
            "PhysicalSizeX_um": pixel_um,
            "PhysicalSizeY_um": pixel_um,
            "PhysicalSizeZ_um": float(self.z_interval_spin.value()),
            "Timepoints": self._sequence.n_timepoints if self._sequence is not None else 1,
            "Cameras": 1,
            # LouisXIV always writes 1 (it gates to one laser); this follows
            # what is actually ticked, since that rule is now the user's call.
            "Channels": max(1, sum(1 for chk, _wl, spin in self.excitation_rows
                                   if chk.isChecked() and spin.value() > 0)),
            "AOTFCycleMode": "per Z",           # the cluster's default; we do not cycle
            "TimeIncrement_s": period_ms / 1000.0,
            "Username": self._save_meta["user_name"],
            "CellLabeling": self._save_meta["cell_labeling"],
            "CellType": self._save_meta["cell_type"],
            "ExperimentDescription": self._save_meta["description"],
            "Fluor": [],                        # no panel field ported
            "ExcitationWavelength_nm": [int(wl)],
            "EmissionWavelength_nm": [],        # no panel field ported
            "FilterType": "",                   # no panel field ported
            "CamExposure_s": exposure_ms / 1000.0,
            "Multi-positionAcq": position_xyz is not None,
            # PositionX/Y/Z_mm and StageAngle_deg are skipped by
            # render_acq_info while Multi-positionAcq is false, exactly as
            # the VI's skip? does -- always included here, same as
            # StageAngle_deg already was, rather than only ever writing them
            # when true (position_xyz um -> mm).
            "PositionX_mm": position_xyz[0] / 1000.0 if position_xyz is not None else 0.0,
            "PositionY_mm": position_xyz[1] / 1000.0 if position_xyz is not None else 0.0,
            "PositionZ_mm": position_xyz[2] / 1000.0 if position_xyz is not None else 0.0,
            "StageAngle_deg": float(DEFAULT_STAGE_ANGLE_DEG),
        }

        extras = {
            "Mode": self.mode_combo.currentText(),
            "Trigger mode": "SYNCREADOUT" if self.sync_readout_chk.isChecked() else "EDGE",
            "Excitation (%)": f"{spin.value():g}",
            "Z Piezo Start (um)": self.z_start_spin.value(),
            "Z Piezo End (um)": self.z_end_spin.value(),
        }
        if self.camera is not None and self.camera.info is not None:
            extras["Camera model"] = self.camera.info.name
            extras["Camera serial"] = self.camera.info.serial
        return fields, extras

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
            # ADDED 2026-09-22: defaults to today's (0, None, None) whenever
            # no sequence is active -- unchanged single-stack behaviour.
            timepoint, position, position_xyz = 0, None, None
            if self._sequence is not None:
                timepoint, position, position_xyz = self._sequence.current()
            self._save_stack(stack, timepoint=timepoint, position=position, position_xyz=position_xyz)

    def _save_stack(self, stack, timepoint: int = 0, position: int | None = None,
                    position_xyz: Vec3 | None = None) -> Path | None:
        """Compute this stack's path the way LouisXIV lays them out, and
        write it on a background thread.

        ``timepoint`` and ``position`` are the Build Image Path.vi indices;
        ``position_xyz`` (ADDED 2026-09-22, wiring up Timepoints=Multi and
        Multi-location -- see _build_acquisition_sequence) is the actual
        stage position in um, written into AcqInfo.txt's PositionX/Y/Z_mm.

        FIXED 2026-09-18 (user: "the GUI takes time to save acquired z
        stack"): the write itself -- an uncompressed, potentially multi-GB
        OME-TIFF (LouisXIV's own format; deliberately uncompressed, see
        fileio/tiff_stack.py) -- used to run right here, blocking the GUI
        thread for however long that took, worse over the network share
        this project lives on. It now runs on a daemon thread and reports
        back through ``save_signals`` (StackSaveSignals, same
        thread-to-GUI-signal pattern as FpgaSignals above); everything that
        only needs to know where the stack WILL land -- callers, tests --
        still gets the path back immediately and unchanged, since only the
        actual disk I/O moved, not the path computation.
        """
        data_dir = self._ensure_data_dir()
        if data_dir is None:
            self._log("Save cancelled: no data folder chosen.")
            return None
        # The folder the Save Image dialog named for THIS run. Falling back
        # to a fresh scan keeps the paths that never went through the dialog
        # (loading a stack, saving a projection) working as before.
        exp = self._experiment_dir or self.next_experiment_folder(data_dir)
        cal = self.calibration
        ch = self._selected_channel_index()
        path = stack_path(exp, self._save_base, ch, timepoint, position,
                          self.separate_position_folders)
        job = _SaveJob(path=path, stack=stack, pixel_size_um=cal.detection.xy_pixel_um,
                      z_step_um=self.z_interval_spin.value(), channel_name=self.excitation_rows[ch][1],
                      exp=exp, acq_fields_extras=self._acq_info(stack, position_xyz=position_xyz))
        # FIXED 2026-09-22: a multi-position/timepoint sequence can legitimately
        # finish a stack before the PREVIOUS stack's background write is done
        # (small stacks, a fast stage); this used to just log "Save skipped"
        # and silently drop that stack's data. Now queued instead of dropped --
        # one write in flight at a time, in order, none lost.
        self._save_queue.append(job)
        if not self._saving_stack:
            self._dispatch_next_save_job()
        return path

    def _dispatch_next_save_job(self) -> None:
        if not self._save_queue:
            return
        job = self._save_queue.pop(0)
        acq_fields, acq_extras = job.acq_fields_extras

        def worker():
            try:
                save_tiff_stack(job.path, job.stack, ome=True, pixel_size_um=job.pixel_size_um,
                                z_step_um=job.z_step_um, channel_name=job.channel_name)
                write_acq_info(job.exp, acq_fields, acq_extras)
            except Exception as e:                                      # noqa: BLE001
                self.save_signals.failed.emit(f"{type(e).__name__}: {e}")
            else:
                self.save_signals.finished.emit(job.path, job.exp, int(job.stack.shape[0]))

        self._saving_stack = True
        self._log(f"Saving {job.stack.shape[0]}-slice stack in the background: {job.path}"
                  + (f" ({len(self._save_queue)} more queued)" if self._save_queue else ""))
        threading.Thread(target=worker, name="unmscope-stack-save", daemon=True).start()

    def _on_stack_save_finished(self, path: Path, exp: Path, n_slices: int):
        self._saving_stack = False
        self._current_exp_dir = exp
        self._log(f"Saved {n_slices}-slice stack: {path}  (+ AcqInfo.txt)")
        self._dispatch_next_save_job()

    def _on_stack_save_failed(self, message: str):
        self._saving_stack = False
        self._log(f"Stack save FAILED: {message}")
        QMessageBox.warning(self, "Save failed", message)
        self._dispatch_next_save_job()

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
            view.set_pixmap(pix)
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
        # ADDED 2026-09-22: while acquiring, only mode_combo was force-
        # disabled -- the Timepoints/Multi-location controls (and the
        # sequence they define) were left editable mid-run.
        for w in (self.tp_combo, self.tp_spin, self.tp_delay_spin,
                  self.multilocation_chk, self.multilocation_configure_btn):
            w.setEnabled(not self.acquiring)
        self.locations_table.setEnabled(self.multilocation_chk.isChecked() and not self.acquiring)

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
        self._update_time_estimates()

    @staticmethod
    def _fmt_hms(seconds: float) -> str:
        # Ceiling, not round: a nonzero total (e.g. a 0.5 s test stack)
        # must never display as "00:00:00" -- indistinguishable from the
        # dummy placeholder this whole field used to be stuck at.
        total = 0 if seconds <= 0 else max(1, int(math.ceil(seconds - 1e-9)))
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _tile_size_um(self) -> tuple[float, float, float]:
        """Tile Size (X, Y, Z um) for the Grid Sequence dialog: LouisXIV's
        ``Image Size X|Y * um/px`` and ``Z um/px * # slices`` (see
        gui/grid_sequence_dialog.py's module docstring) -- passed to
        SampleStageDialog as its ``acq_settings_provider`` and called via
        ``refresh_grid_tile_size()`` (deferred 2026-09-23/24: the dialog's
        own ``push_acq_settings()`` existed but nothing called it).
        Uses the Camera tab's configured ROI (works even without a
        connected camera, same as ``_update_time_estimates``'s n_slices)
        and binning 1 when no camera is connected, matching
        ``_scalebar_um_per_px``'s fallback."""
        binning = self.camera.get_binning() if (self.camera is not None and self.camera.is_connected) else 1
        px_um = self.calibration.detection.pixel_size_um(binning=binning)
        roi = self.camera_tab.roi
        size_x_um = roi.width * px_um
        size_y_um = roi.height * px_um
        interval = self.z_interval_spin.value()
        if interval > 0:
            n_slices = max(1, int(round(abs(self.z_end_spin.value() - self.z_start_spin.value()) / interval)) + 1)
        else:
            n_slices = 1
        size_z_um = interval * n_slices
        return size_x_um, size_y_um, size_z_um

    def _refresh_grid_tile_size(self) -> None:
        """Forward the current Tile Size to the Grid Sequence dialog (no-op
        until Sample Stage Control has been opened at least once). Call
        wherever ROI/pixel-size/Z-stack-range/slices change."""
        if self.sample_stage_dialog is not None:
            self.sample_stage_dialog.refresh_grid_tile_size()

    def _update_time_estimates(self) -> None:
        """Live, quiet preview of Stack Acq. Time / Timepoint Interval /
        Total time from the current Scan Setup values.

        These fields used to just sit at their construction-time
        placeholder ("00:00:00") forever -- nothing ever wrote to them
        (user, 2026-09-23, after a real 42-point run: "all the time
        variables remained 0s"). All three use the SAME hours:minutes:
        seconds format (user, same day: mixing that with a minutes:
        seconds.hundredths format on Stack Acq. Time made Total time look
        smaller than Stack Acq. Time even when it numerically never can be
        -- Total is always n_timepoints * n_positions * Stack Acq. Time
        plus any timepoint delays). Deliberately does NOT reuse
        _compute_scan_waveform() for this: that method logs several lines
        every call (meant for once-per-actual-arm), which would spam the
        log on every spin-box keystroke. Uses the same pure camera_cycle_s
        formula it falls back to when no camera is connected, or the
        camera's own cycle_time_s() when one is, or (best) the real
        self._trigger_period_s left over from the last arm.

        Guarded on self.camera_tab existing: this is wired to fire from
        z/exposure/timepoint/position controls built before the Camera
        tab is, so the very first calls (during __init__) are no-ops.
        """
        if not hasattr(self, "camera_tab"):
            return
        if self.mode_combo.currentText() != MODE_ZSTACK:
            self.stack_acq_time_field.setText("00:00:00")
            self.tp_interval_field.setText("00:00:00")
            self.total_time_field.setText("00:00:00")
            self._refresh_grid_tile_size()
            return

        interval = self.z_interval_spin.value()
        if interval > 0:
            n_slices = max(1, int(round(abs(self.z_end_spin.value() - self.z_start_spin.value()) / interval)) + 1)
        else:
            n_slices = 1

        exposure_ms = self.exposure_spin.value()
        cam = self.camera if (self.camera is not None and self.camera.is_connected) else None
        sync = (cam.trigger_active == cam.TRIGGER_SYNCREADOUT) if cam is not None \
            else self.sync_readout_chk.isChecked()
        if self._trigger_period_s > 0:
            period_s = self._trigger_period_s
        elif cam is not None:
            period_s = cam.cycle_time_s(exposure_ms)
        else:
            period_s = camera_cycle_s(exposure_ms / 1000.0, self.camera_tab.roi.height, sync)
        stack_s = n_slices * period_s

        n_positions = 1
        if self.multilocation_chk.isChecked() and self.sample_stage_dialog is not None:
            n_positions = max(1, len(self.sample_stage_dialog.sequence.positions_um()))
        round_s = n_positions * stack_s

        multi_tp = self.tp_combo.currentText() == "Multi"
        n_timepoints = self.tp_spin.value() if multi_tp else 1
        delay_s = self.tp_delay_spin.value() if multi_tp else 0.0
        total_s = n_timepoints * round_s + max(0, n_timepoints - 1) * delay_s

        self.stack_acq_time_field.setText(self._fmt_hms(stack_s))
        self.tp_interval_field.setText(self._fmt_hms(round_s))
        self.total_time_field.setText(self._fmt_hms(total_s))
        self._refresh_grid_tile_size()

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

        # FIXED 2026-09-23 (user: "exposure still isn't 100ms by default"):
        # this used to read the camera's OWN remembered exposure back into
        # the spin box, so a real camera left at some other exposure by a
        # previous session (or DCAM's own device default) silently
        # overrode the GUI's 100 ms default the moment you connected. The
        # spin box is the setpoint -- Actual exposure/frame time (the
        # read-back) comes from refresh_from_camera below -- so push it TO
        # the camera, the same direction on_exposure_changed already uses.
        try:
            self.camera.set_exposure_ms(self.exposure_spin.value())
        except Exception as e:
            self._log(f"Set exposure on connect FAILED: {type(e).__name__}: {e}")
        self.camera_tab.refresh_from_camera()
        self._push_engine_times()          # LouisXIV's connect-time "Set Camera"

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

    def _push_engine_times(self) -> tuple[float, float]:
        """LouisXIV's engine "Set Camera" -> Camera Recalc Wvfrm? -> Camera
        times to waveform times: rewrite Cam exp (s) and Cycle time (s) on
        the Low-Level Waveform Config page from the camera
        (docs/louisxiv_cycle_time_semantics.md). Call wherever LouisXIV
        sends "Set Camera": camera connect, every Camera-tab change (here:
        exposure and ROI -- see __init__'s roi_changed connection), and the
        Acquire set-up. Returns the (Cam exp (s), Cycle time (s)) it wrote.
        """
        panel = self.utilities_tab.waveform_panel
        cfg = panel.config()
        exposure_ms = float(self.exposure_spin.value())
        cam = self.camera
        # No camera open (planning, or LouisXIV holds the rig): the same
        # DCAM formula on the panel's own ROI height and trigger mode.
        cam_cycle = (cam.cycle_time_s(exposure_ms) if cam is not None and cam.is_connected
                     else camera_cycle_s(exposure_ms / 1000.0, self.camera_tab.roi.height,
                                         self.sync_readout_chk.isChecked()))
        cam_exp_s, cycle_s = engine_times(exposure_ms / 1000.0, cam_cycle,
                                          cfg.cycle_time_s, cfg.custom_cycle_time)
        panel.set_engine_times(cam_exp_s, cycle_s)
        return cam_exp_s, cycle_s

    def on_exposure_changed(self, value: float):
        # Cam exp / Cycle time on the Low-Level Waveform Config page follow
        # the Scan Setup exposure regardless of camera or run state, as
        # LouisXIV's own "Set Camera" does.
        self._push_engine_times()
        self._update_time_estimates()
        if self._blocking_op is not None:
            return  # a pumped spin-box event during a blocking driver call
        if self.acquiring:
            # The trigger period is fixed for the run; pushing a longer
            # exposure under it would make the camera ignore edges. The
            # value is kept and applied at the next Acquire.
            self._log(f"Exposure {value:.3f} ms noted; it applies at the next Acquire.")
            return
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
        # Whatever rows are already ticked (a restored session, usually) go
        # out now rather than waiting for an Acquire.
        self._push_aotf_levels()

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

    def _on_set_detection(self, magnification: float, camera_pixel_um: float) -> None:
        """Camera tab's Detection Optics > Set Optics (UNMScope addition,
        see camera_tab.py): apply the new magnification/camera pixel size
        to this run immediately, persist them to the UNMScope ini copy the
        same way um/V Cal does, and refresh FOV. User, 2026-09-23: "the FOV
        should be calculated from the magnification of the objective in use
        and the pixel size camera" -- the formula (Detection.pixel_size_um)
        was already correct; the two numbers just weren't editable."""
        detection = replace(self.calibration.detection,
                            magnification=magnification, camera_pixel_um=camera_pixel_um)
        self.calibration = replace(self.calibration, detection=detection)
        try:
            path = detection.save()
        except Exception as e:
            self._log(f"Detection optics: save failed: {type(e).__name__}: {e}")
        else:
            self._log(f"Detection optics set: {magnification:g}x magnification, "
                      f"{camera_pixel_um:g} um camera pixel -> "
                      f"{detection.xy_pixel_um:.4f} um/px sample-space (saved to {path}).")
        self.camera_tab.refresh_fov()
        self._refresh_grid_tile_size()

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
        SPIM MAIN event case [4]): one non-modal panel.

        X/Y motion: ASI MS-2000 on COM5 (``real_asi_factory``).
        Z motion:   Arduino/Nikon focus on COM8 (``real_focus_factory``).
        Both factories are passed so the Settings page's 'Save Settings' can
        reconnect each controller independently.
        """
        if self.sample_stage_dialog is None:
            self.sample_stage_dialog = SampleStageDialog(
                real_stage_factory=lambda settings: real_asi_factory(
                    settings.com_port, settings.velocity_um_s, settings.settling_ms),
                real_z_factory=real_focus_factory,
                z_com_port="COM8",
                rel_offset_provider=lambda: self.rel_offset_spin.value(),
                acq_settings_provider=self._tile_size_um,
                log=self._log, parent=self)
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
        self._update_time_estimates()

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

    def _compute_scan_waveform(self) -> tuple[LouisXivWaveform, float, float, bool] | None:
        """Build the TRUE AO waveform for the current Scan Setup / Low-Level
        Waveform Config values -- exactly what Acquire arms the FPGA with.

        The single source for both what gets ARMED and what the Waveforms
        tab DISPLAYS on a Simulate-on-FPGA run, so the two can never drift
        apart: the simulated trace is provably the same computation a real
        run would use, not a second, separately-maintained formula.

        Returns ``(lx, period_s, exposure_s, sync)``, or ``None`` (having
        already logged/warned) if no camera is connected or the waveform
        math itself fails. Reads ``self.camera`` (queries only --
        ``readout_ms()``, ``cycle_time_s()``, ``trigger_active`` -- never
        writes to it) and never references ``self.fpga``: nothing in this
        method can arm or send anything.

        Trigger period = the CYCLE TIME, which the X galvo needs to fly
        back to its resting position before the next cycle can start. It
        is NOT the exposure, and (Custom Cycle Time off) it is LouisXIV's
        OWN camera-cycle formula -- docs/louisxiv_cycle_time_semantics.md,
        reconciled against the bench 2026-09-06: the user confirmed Custom
        Cycle Time IS ticked on this rig, with 0.127 s typed (27 ms of
        flyback over a 100 ms exposure; MEASURED, not derived from any
        formula). engine_times()/_push_engine_times() apply exactly
        LouisXIV's rule in both Custom modes; see config/waveform_config.py.

        We had previously used only the camera's own minimum as the
        period. In SYNCREADOUT that is max(exposure, readout + margin) =
        the exposure itself for any realistic exposure, so the galvo got
        ZERO flyback time and triggers landing during readout were
        silently ignored -- fewer images than Slices. (The same class of
        bug once came from a hard-coded 9.7 ms readout, which dropped
        every second trigger.)

        PORT-ONLY DEVIATION from LouisXIV, kept deliberately: our own
        bench-measured safety margin (Camera.trigger_period_ms(),
        spikes/19, docs/known_issues.md) is still enforced as a separate,
        final floor below. LouisXIV's own formula has no such margin --
        it protects against a different failure (retriggering the camera
        during ITS OWN readout through this driver path), not the galvo
        flyback question, and dropping it reintroduced dropped frames on
        this rig's adapter even though it does not in LouisXIV's own
        native DCAM path.
        """
        # Deliberately works with NOTHING connected -- no camera, no FPGA.
        # The Simulated view exists to plan and check waveforms BEFORE they
        # are deployed, which includes while LouisXIV holds the (exclusive)
        # FPGA session for a side-by-side comparison. With a camera open we
        # use its own readback; without one, the same formulas run on the
        # panel's values, and the port-only hardware margin is skipped --
        # there is no hardware to protect, and LouisXIV has no such margin
        # anyway, so this is also the number to compare against it.
        cam = self.camera if (self.camera is not None and self.camera.is_connected) else None
        exposure_ms = self.exposure_spin.value()
        typed = self.utilities_tab.waveform_panel.config()
        cam_exp_s, cycle_s = self._push_engine_times()
        if typed.custom_cycle_time and cycle_s > typed.cycle_time_s + 1e-9:
            self._log(f"Custom Cycle time {typed.cycle_time_s * 1e3:.3f} ms is below the camera's "
                      f"own cycle; LouisXIV raises it to {cycle_s * 1e3:.3f} ms.")
        sync = (cam.trigger_active == cam.TRIGGER_SYNCREADOUT) if cam is not None \
            else self.sync_readout_chk.isChecked()
        # HHMI - Generate trigger settings for FPGA: Cycle (ticks) =
        # max(Waveform.Cycle time (s), camera Cycle(s)) -- established, Q6.
        if cam is not None:
            cam_cycle_s_val = cam.cycle_time_s(exposure_ms)
        else:
            cam_cycle_s_val = camera_cycle_s(exposure_ms / 1000.0,
                                             self.camera_tab.roi.height, sync)
        period_s = max(cycle_s, cam_cycle_s_val)
        if cam is not None:
            floor_s = cam.trigger_period_ms(exposure_ms) / 1000.0
            if floor_s > period_s + 1e-9:
                self._log(f"Trigger period raised from {period_s * 1e3:.3f} to {floor_s * 1e3:.3f} ms "
                          "(port safety margin over the camera's own readout -- LouisXIV has no such "
                          "margin; docs/known_issues.md).")
                period_s = floor_s
        period_ms = period_s * 1000.0
        # free_run_timing() only needs exposure <= period; in SYNCREADOUT the
        # interval itself is the exposure.
        exposure_s = min(exposure_ms, period_ms) / 1000.0
        mode_name = "SYNCREADOUT" if sync else "EDGE"
        if cam is None:
            self._log(f"{mode_name}: trigger period {period_ms:.2f} ms ({1000.0 / period_ms:.2f} Hz), "
                      f"camera cycle {cam_cycle_s_val * 1e3:.3f} ms from the panel "
                      f"({self.camera_tab.roi.height} rows) -- no camera open, so no readback and no "
                      "port safety margin.")
        elif sync:
            self._log(f"SYNCREADOUT: trigger period {period_ms:.2f} ms ({1000.0 / period_ms:.2f} Hz) "
                      f"= actual exposure (requested {exposure_ms:.2f} ms; camera floor "
                      f"{cam.readout_ms():.1f} ms readout + "
                      f"{cam.syncreadout_margin_ms():.2f} ms)"
                      + ("  <-- requested exposure is below the floor and was lengthened"
                         if period_ms > exposure_ms + 1e-6 else ""))
        else:
            self._log(f"EDGE: trigger period {period_ms:.2f} ms ({1000.0 / period_ms:.2f} Hz) = exposure "
                      f"{exposure_ms:.2f} ms + readout {cam.readout_ms():.1f} ms + margin "
                      f"{cam.edge_margin_ms():.2f} ms")

        # ---- AO waveform for this scan (docs/wvfrm2_packing.md) -------------
        # X galvo: one sweep of the Scan Setup Range around Offset per
        # trigger, over the exposure, + flyback. Z piezo / Z galvo: constant
        # per slice, stepping by their Interval per slice in a Z stack.
        # Microns -> volts with LouisXIV's own calibrations; volts clamped
        # to the ini limits. Points at 100 us (4000 ticks).
        cal = self.calibration
        # From the SAME field Acquire uses to set z_target_frames -- not
        # z_target_frames itself, so this is correct before a real run too
        # (a Preview click should not require having pressed Acquire first).
        n_slices = int(self.slice_count_field.text()) if self.slice_count_field.text().isdigit() else 1
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
                x_galvo_delay_us=wcfg.x_galvo_delay_us, z_galvo_delay_us=wcfg.z_galvo_delay_us,
                z_piezo_delay_us=wcfg.z_piezo_delay_us, aotf_cycle=wcfg.aotf_cycle,
                cycle_margin_s=0.0)        # LouisXIV's rule: the block fills the cycle (measured OK on the card)
        except ValueError as e:
            self._log(f"Waveform calculation failed: {e}")
            QMessageBox.warning(self, "Waveform", str(e))
            return None
        wf = lx.scan
        self.last_waveform = wf              # of the current/last acquisition OR preview
        self._log(f"LouisXIV ramp: {lx.line.total_points} pts/line = accel {lx.line.tau_elem} + linear "
                  f"{lx.line.linear_points} + decel {lx.line.tau_elem} + return {lx.line.return_points}; "
                  f"AO rate {lx.rate.ao_rate_hz / 1e3:.3f} kHz (exposure rule {lx.rate.rate_for_exposure_hz:.0f}, "
                  f"flyback rule {lx.rate.rate_for_flyback_hz:.0f} pts/s, option {lx.rate.option}); "
                  f"Pix/ms {lx.rate.pixel_per_ms:.3f}; S-curve {lx.s_curve_points} pts."
                  + ("".join(" NOTE: " + n for n in lx.notes)))
        # Indicators on the cluster panel: Pixel/ms (Compute AO rate from
        # Cycle Time.vi) and the axes as Scan Setup has them. Cam exp / Cycle
        # time were already set from the engine by _push_engine_times() above.
        self.utilities_tab.waveform_panel.set_indicators(
            pixel_per_ms=lx.rate.pixel_per_ms,
            axes={"x": AxisSettings(0, self.xg_offset.value(), self.xg_interval.value(), self.xg_pixels.value()),
                  "xwvfrm": AxisSettings(0, self.xg_offset.value(), self.xg_interval.value(), self.xg_pixels.value()),
                  "z": AxisSettings(0, self.zg_start.value(), self.zg_interval.value(), n_slices),
                  "zpiezo": AxisSettings(0, self.z_start_spin.value(), self.z_interval_spin.value(), n_slices),
                  "dither": AxisSettings(0, 0.0, self.dg_range.value(), 1)})
        block_ticks = wf.points_per_trigger * wf.ticks_between_points
        self._log(f"Waveform: {wf.points_per_trigger} points/trigger ({block_ticks / TICKS_PER_S * 1e3:.1f} ms) x "
                  f"{wf.n_slices} slice(s) = {len(wf.words)} words; X sweep {x_range_v * 1e3:+.1f} mV around "
                  f"{x_offset_v * 1e3:+.1f} mV ({self.xg_range.value():g} um @ {cal.x_galvo.um_per_volt:g} um/V); "
                  f"Z piezo {zp_start_v * 1e3:+.1f} mV step {zp_step_v * 1e3:+.2f} mV; "
                  f"Z galvo {zg_start_v * 1e3:+.1f} mV step {zg_step_v * 1e3:+.2f} mV; "
                  f"dither {dither_range_v * 1e3:.1f} mV pk-pk x {self.dg_sweeps.value():g} sweeps "
                  f"(flyback {self.dg_flyback.value():.2f}) (calibration: {cal.source}).")
        return lx, period_s, exposure_s, sync

    def _aotf_levels_for_run(self) -> tuple[dict[int, int], str]:
        """({AOTF channel: DAC counts}, a one-line description) for the
        current Excitation rows -- exactly what _start_acquisition hands to
        start_free_run(aotf_levels=...). Every ticked row with Power > 0
        (LouisXIV's one-laser rule is logged at Acquire, not enforced)
        sets its AOTF channel to Power% -> volts -> counts; the rest 0.
        The FPGA drives 'AOTF ch (V)' as a DC level while armed, so the
        laser is on for the whole run (docs/aotf.md). Shared with the Simulated view so it
        shows the levels that would really be written, not a second guess
        at them."""
        cal = self.calibration
        levels: dict[int, int] = {}
        desc = "off"
        for i, (chk, wl, spin) in enumerate(self.excitation_rows):
            if chk.isChecked() and spin.value() > 0:
                ch = cal.aotf.channel_for_row(i)
                v = cal.aotf.power_pct_to_v(spin.value())
                counts = int(round(v * COUNTS_PER_VOLT))
                levels[ch] = counts
                desc = (f"{wl} nm at {spin.value():g} % -> AOTF ch {ch} = {v:.3f} V ({counts} counts); "
                        f"other channels 0")
        return levels, desc

    def _push_aotf_levels(self) -> None:
        """Write the current Excitation rows to 'AOTF ch (V)' now.

        Called whenever a row is ticked or its Power % changes, and once at
        FPGA connect. The FPGA holds the level as a DC value the moment it is
        written -- verified on the card 2026-09-08 with nothing armed -- so
        this is what puts the voltage on AO5/AO6/AO7/AO3.

        Safe to call at any time: it no-ops without an FPGA, and writing the
        register mid-run is exactly what LouisXIV's "Set AOTF" mode does.
        Unticking a row zeroes that channel because the operator asked for
        it -- nothing here zeroes a channel on its own (docs/aotf.md).
        """
        if getattr(self, "fpga", None) is None:
            return
        levels, desc = self._aotf_levels_for_run()
        try:
            self.fpga.set_aotf_levels(levels)
        except Exception as e:                                  # noqa: BLE001
            self._log(f"AOTF level write failed: {type(e).__name__}: {e}")
            return
        self._log(f"AOTF: {desc}")

    @staticmethod
    def _trigger_up_ticks_for(wf) -> int:
        """The Int-Sync high time this port arms the FPGA with for one
        ``wf`` (a ScanWaveform): must cover the whole AO block or the FPGA
        aborts it after the last trigger of a bounded run (spikes/29b,
        29c) -- the same formula _start_acquisition sends to the real
        FPGA, factored out so the Simulated view's digital trace matches
        it exactly rather than a second, separately-maintained copy."""
        block_ticks = wf.points_per_trigger * wf.ticks_between_points
        return block_ticks + 40_000

    #: Where each slot of the Wvfrm2 AO stream lands on the FPGA Scope's
    #: columns (docs/fpga_scope.md: "columns 8-13 carry the galvo/tiling/
    #: filter values"). The two prefix slots are LabVIEW bookkeeping, not
    #: signals, so they are not shown.
    AO_STREAM_COLUMNS = {"X Galvo": 8, "Z Galvo": 9, "Z Piezo": 10,
                         "Dither Galvo": 11, "Tiling": 12, "Filter": 13}
    #: 'AOTF ch (V)' channel -> scope column (scope_view.LEGEND_LABELS:
    #: 16/17 are AOTF 0/1, 18 is Perfusion, 19+ are AOTF 2..6).
    AOTF_LEVEL_COLUMNS = {0: 16, 1: 17, 2: 19, 3: 20}

    def _scope_snapshot_from_arm(self, wf, aotf_levels: dict[int, int] | None = None) -> ScopeSnapshot:
        """Decode what would be SENT TO THE CARD back into scope traces --
        the Waveforms tab's Source: Simulated view.

        Deliberately built from the arm payload itself, not from a
        second model of each signal: ``wf.words`` IS the Wvfrm2 AO stream
        start_free_run() streams, so unpack_words() (waveform.py, written
        "for showing what was sent") replays every slot in it -- X/Z
        Galvo, Z Piezo, Dither Galvo, Tiling, Filter -- rather than a
        hand-picked few. ``aotf_levels`` is the same dict handed to
        start_free_run(aotf_levels=...), drawn as the DC level it is
        (docs/aotf.md: written just before the arm, held for the whole run,
        zeroed on stop). The trigger is the one thing with no payload to
        replay -- the FPGA's own timing logic generates it -- so it is
        reconstructed from the timing this port actually arms:
        MIN_TRIGGER_UP_TICKS wide, once per AO block.

        Anything added to the stream later shows up here for free. No
        camera, no FPGA, nothing hardware-facing; safe with nothing
        connected at all.

        Int Sync (column 14) is deliberately left empty: this port arms
        'Trigger up (ticks)' at _trigger_up_ticks_for(wf), longer than the
        whole AO block by design (it has to cover it, spikes/29b), so a
        faithful trace would be a permanently-high line carrying nothing.
        """
        n = max(1, wf.points_per_trigger * wf.n_slices)
        frames = np.zeros((n, len(AI_CHANNEL_NAMES)), dtype=np.int16)
        # The AO stream, replayed slot by slot straight out of the words
        # that would be DMA'd to the card.
        streamed = unpack_words(wf.words) if wf.words else {}
        for name, col in self.AO_STREAM_COLUMNS.items():
            arr = streamed.get(name)
            if arr is not None and len(arr):
                arr = arr[:n]
                frames[:len(arr), col] = np.clip(arr, -32767, 32767).astype(np.int16)
        # The laser: its level (from the Excitation rows) modulated by the
        # per-line gate LouisXIV defines -- on through the forward sweep,
        # off for the whole return move (louisxiv_waveform.aotf_gate).
        gate = wf.channels.get("AOTF gate")
        for ch, counts in (aotf_levels or {}).items():
            col = self.AOTF_LEVEL_COLUMNS.get(int(ch))
            if col is None:
                continue
            level = int(np.clip(counts, -32767, 32767))
            if gate is not None and len(gate):
                g = gate[:n]
                frames[:len(g), col] = np.clip(g * level, -32767, 32767).astype(np.int16)
            else:
                frames[:, col] = level
        # The camera trigger: no payload to replay (the FPGA generates it),
        # so rebuilt from the same ticks this port arms it with.
        tpp = max(1, wf.points_per_trigger)
        dio4_n = int(np.clip(round(MIN_TRIGGER_UP_TICKS / wf.ticks_between_points), 1, tpp))
        for k in range(wf.n_slices):
            start = k * tpp
            frames[start:start + dio4_n, IDX_DIO4] = DIGITAL_TRUE
        return ScopeSnapshot(frames=frames, fs_hz=1.0 / wf.point_period_s,
                             names=AI_CHANNEL_NAMES, end_frame_index=n)

    def _start_acquisition(self):
        """The Acquire-button entry point -- one-time session setup, then
        the first stack's arm. See _arm_one_stack for the repeatable part
        (FIXED 2026-09-22: this used to also BE the repeatable part, which
        would have meant showing the modal Save Image dialog once per
        stack in a multi-timepoint/multi-position sequence, dozens of
        times over a real run, and risked scattering stacks across
        different experiment folders)."""
        self._begin_acquisition_session()

    def _begin_acquisition_session(self):
        if self.camera is None or self.fpga is None:
            return
        mode = self.mode_combo.currentText()
        self._log(f"Starting acquisition: {mode}")

        # Ask where to save BEFORE anything is armed or started (user,
        # 2026-09-07: "The saving prompt needs to happen first before the
        # actual acquisition starts."). It used to be asked by _save_stack,
        # i.e. after the run had finished -- so a file dialog appeared over
        # a completed stack, and cancelling it threw the data away. Asked
        # here, cancelling costs nothing because nothing has started yet.
        # Still once per session: _ensure_data_dir only prompts while
        # _data_dir is None.
        #
        # FIXED 2026-09-18 (user): "The save file checkbox should only be
        # related to z stack, it should have nothing to do with the
        # continuous mode." Gated on mode because that already matches what
        # can actually be saved: _on_stack_finished only calls _save_stack
        # when z_target_frames is set, which Continuous leaves at 0
        # (unbounded -- see _start_acquisition below, "Continuous: arming
        # camera"), so nothing a Continuous run does was ever saved. This
        # prompt was firing anyway, able to cancel a live-view run over a
        # save location that would never be used.
        if (mode == MODE_ZSTACK and self.save_files_chk.isChecked()
                and self._prompt_save_image() is None):
            self._log("Acquisition cancelled at the Save Image dialog.")
            return

        # LouisXIV's rule (HHMI - Check that only 1 laser is selected.vi) is
        # that exactly 1 Excitation line may be checked with Power > 0%, and
        # this REFUSED to start otherwise. Relaxed to a log line on the
        # user's instruction (2026-09-07): "let me make the decision on if
        # the voltage on AOTFs should be forced to zero ... if any of the
        # AOTF is checked please throw the voltage at the appropriate
        # channel." Whatever is ticked is what gets driven -- including
        # nothing, which is a legitimate dark run for checking waveforms.
        selected = [wl for chk, wl, spin in self.excitation_rows if chk.isChecked() and spin.value() > 0]
        if len(selected) != 1:
            self._log(f"Excitation: {len(selected)} row(s) selected ({', '.join(str(w) for w in selected) or 'none'}) "
                      "-- LouisXIV allows exactly one; driving what is ticked, as asked.")

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
                    # A re-initialised device has no exposure open, so the next
                    # SYNCREADOUT run must not expect (and discard) a warm-up
                    # frame that will never come -- that cost a real slice.
                    self._sync_exposure_open = False
                got = self.camera.get_exposure_ms()
                if abs(got - self.exposure_spin.value()) > 0.01 * self.exposure_spin.value():
                    self._log(f"WARNING: camera exposure reads {got:.3f} ms after setting "
                              f"{self.exposure_spin.value():.3f} ms.")
        except Exception as e:
            self._log(f"Failed to set external trigger: {type(e).__name__}: {e}")
            QMessageBox.warning(self, "Error", f"Failed to set external trigger:\n{e}")
            return

        # ADDED 2026-09-22: build the multi-timepoint/multi-position walk
        # for this session, if either is actually in use. None (the
        # existing, unchanged behaviour) whenever Timepoints=Single and
        # Multi-location is unchecked.
        try:
            self._sequence = self._build_acquisition_sequence(mode)
        except StageError as e:
            self._log(f"Cannot start a Multi-location sequence: {e}")
            QMessageBox.warning(self, "Sample Stage", str(e))
            return
        if self._sequence is not None:
            self._log(f"Sequence: {self._sequence.n_timepoints} timepoint(s) x "
                      f"{self._sequence.n_positions} position(s) = "
                      f"{self._sequence.total_steps()} stack(s).")
            self._sequence_abort.clear()

        self._arm_one_stack()

    def _arm_one_stack(self):
        """The repeatable per-stack arm -- everything that must happen
        again for every stack in a sequence, reused unchanged from a plain
        single-stack run so SYNCREADOUT's warm-up-discard logic sees
        exactly the same "arm again" shape it already handles correctly
        between independent Acquire clicks (test_zstack_continuous_zstack)."""
        mode = self.mode_combo.currentText()   # mode_combo is disabled while acquiring/sequencing
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
        self._stale_discarded = 0
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
        self._update_scan_setup_enable_state()

        self.camera_poll_timer.start(30)

        # The true AO waveform -- shared with the Waveforms tab's Simulated
        # preview via _compute_scan_waveform(); see its docstring for the
        # cycle-time reasoning (LouisXIV's own formula plus our port-only
        # hardware safety margin, both explained there in full).
        result = self._compute_scan_waveform()
        if result is None:
            return
        lx, period_s, exposure_s, sync = result
        wf = lx.scan
        cal = self.calibration
        self._trigger_period_s = period_s
        self._triggers_fired = 0
        self._finishing = False
        self._run_closing = self.camera.closing_triggers()
        self._last_status_log_t = 0.0

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
        # Simulate-on-FPGA clamps every AO output to 0 V by design (the
        # whole point: nothing may move) -- the real scope reading flat is
        # correct, but not useful to look at, so replay the arm payload
        # instead automatically. Deliberately the INTENDED levels, not the
        # clamped ones start_free_run() will actually write: this view is
        # for checking the plan, and a clamped copy of it would be as
        # blank as the real trace. A real-camera run switches back to the
        # live view, in case an earlier Simulate-on-FPGA run left the panel
        # showing a computed one. Either way this is only what the Waveforms
        # tab DISPLAYS; the FPGA is armed identically in both cases.
        if sim_on_fpga:
            aotf_levels, aotf_desc = self._aotf_levels_for_run()
            snap = self._scope_snapshot_from_arm(wf, aotf_levels)
            self.scope_panel.show_computed_waveform(snap)
            self._log(f"Waveforms tab showing the SIMULATED trace: {len(snap.frames)} computed "
                      f"points ({wf.n_slices} slice(s) x {wf.points_per_trigger} pts/trigger), "
                      f"AOTF {aotf_desc} -- this is the arm payload itself, replayed, because "
                      "the AO outputs are clamped and no voltage is sent to the FPGA. Press "
                      "Reset on that tab for the live trace.")
        else:
            self.scope_panel.show_live()
        trigger_up_ticks = self._trigger_up_ticks_for(wf)
        clamp_counts = self.scope_panel.test_clamp_counts() if sim_on_fpga else 0
        if sim_on_fpga:
            self._log("SIMULATE ON FPGA: AO clamp " + (f"+-{clamp_counts} counts (scope test clamp)" if clamp_counts
                                                        else "0 -- every AO output frozen at 0 V."))

        # ---- AOTF excitation level (docs/aotf.md) --------------------------
        # Whatever is ticked is what gets driven, in every mode. The AO
        # clamp stops the galvos and the piezo moving; it has nothing to do
        # with the AOTF, and conflating the two hid the one signal a
        # simulate-on-FPGA run is usually being watched for.
        aotf_levels, aotf_desc = self._aotf_levels_for_run()
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

        # ---- AOTF digital gate (host-timed; not a LouisXIV feature) --------
        # Opt-in (main_window.py's own addition, docs/aotf.md): adaptive to
        # THIS run's actual waveform, phased off the same
        # self.fpga._free_run_t0 the FPGA's own trigger counter uses.
        #
        # Gates only channels with a ticked, driven Excitation row --
        # aotf_levels' keys, the same set _push_aotf_levels() lights up.
        # FIXED 2026-09-16 (user): Enable was previously hard-coded to
        # channels=(0,1,2,3), so checking it drove all four AOTF terminals
        # regardless of which laser row was ticked. An unticked row must
        # stay at 0 V.
        #
        # on-duration = line.on_points / rate.ao_rate_hz -- the forward-sweep
        # length aotf_gate() blanks to for this exact armed waveform, not the
        # camera's raw exposure_s. docs/aotf.md's measured reference (user's
        # FPGA-Scope export, 27 cycles averaged): the laser unblanks within
        # one 5 us sample of the camera trigger and blanks 173.987 ms later
        # at 87.0 % duty -- i.e. on_points/total_points of the cycle, which
        # is what this now reproduces instead of approximating with
        # exposure_s.
        if self.aotf_gate_chk.isChecked():
            gated_channels = tuple(sorted(aotf_levels))
            if not gated_channels:
                self.aotf_gate_status.setText("idle (no channel ticked)")
                self._log("AOTF digital gate: Enable is checked but no Excitation row is ticked "
                          "with Power > 0 -- nothing to gate.")
            else:
                line = lx.line
                on_points = line.total_points if line.bidirectional else line.on_points
                gate_on_s = on_points / lx.rate.ao_rate_hz
                lead_used = self.fpga.start_aotf_digital_gate(
                    period_s, gate_on_s, channels=gated_channels,
                    on_volts=self.aotf_gate_volts.value())
                gap_ms = self.fpga.aotf_gate_gap_s * 1e3
                self.aotf_gate_status.setText(f"running: lead {lead_used * 1e3:.2f} ms, gap {gap_ms:.2f} ms")
                self._log(f"AOTF digital gate: ON ({self.aotf_gate_volts.value():.2f} V) on channel(s) "
                          f"{gated_channels}, {gate_on_s * 1e3:.3f} ms/cycle ({on_points}/{line.total_points} pts), "
                          f"lead {lead_used * 1e3:.2f} ms of gap {gap_ms:.2f} ms available"
                          + ("" if gap_ms > 2.0 else " -- gap is tight; expect jittery, not clean, "
                             "off-edges (spikes/39_aotf_digital_gate.py case B)."))
        else:
            self.aotf_gate_status.setText("idle")

        if sim_on_fpga:
            self.acq_status_label.setText("ACQUIRING (SIM on FPGA)")
            self._log("SIMULATE ON FPGA: frames come from the simulated camera, one per FPGA trigger "
                      f"(fed from '# of triggers read'); AO clamp verified = {self.fpga.ao_clamped}.")

    def _build_acquisition_sequence(self, mode: str) -> _AcqSequence | None:
        """Reads Timepoints/Multi-location and returns the ordered walk of
        (timepoint, position, xyz) steps for this run, or None for the
        existing plain single-stack behaviour (Timepoints=Single and
        Multi-location unchecked -- or Continuous mode, which has no
        defined stop and so no "repeat N times" equivalent).

        Raises StageError (caught by the caller, shown as a warning) rather
        than silently constructing/connecting real stage hardware for the
        first time in the middle of launching an unattended run.
        """
        if mode != MODE_ZSTACK:
            return None
        multi_tp = self.tp_combo.currentText() == "Multi"
        n_timepoints = self.tp_spin.value() if multi_tp else 1

        positions: list[Vec3 | None]
        if self.multilocation_chk.isChecked():
            if self.sample_stage_dialog is None:
                raise StageError(
                    "Multi-location is checked, but Sample Stage Control has never been "
                    "opened -- open it (Configure) and set up a position sequence first.")
            xyz_list = list(self.sample_stage_dialog.sequence.positions_um())
            if not xyz_list:
                raise StageError(
                    "Multi-location is checked, but the position sequence is empty -- "
                    "add positions in Sample Stage Control first.")
            positions = xyz_list
        else:
            positions = [None]

        n_positions = len(positions)
        if n_timepoints * n_positions <= 1:
            return None   # plain single-stack case -- unchanged behaviour

        if n_positions > 1:
            if not self.separate_position_folders:
                self._log("Multi-location: forcing 'separate position folders' on for "
                          "this run (otherwise every position at a timepoint would "
                          "overwrite the same file).")
            self.separate_position_folders = True
            # Honesty check (2026-09-22): a still-simulated axis would
            # otherwise silently produce identically-positioned stacks.
            dlg = self.sample_stage_dialog
            self._log(f"Multi-location stage backends this run: X/Y={type(dlg.stage).__name__}, "
                      f"Z={type(dlg.z_stage).__name__}.")
            # Hand exclusive stage control to the sequence for the whole
            # run -- see begin_sequence_control's docstring for why.
            dlg.begin_sequence_control()

        steps = [(t, p, positions[p]) for t in range(n_timepoints) for p in range(n_positions)]
        delay_s = self.tp_delay_spin.value() if multi_tp else 0.0
        # ADDED 2026-09-23 (user, after a real 42-point run kept acquiring
        # past the 42nd position): with Timepoints=Multi, a run legitimately
        # repeats every position once per timepoint -- e.g. 42 positions x 2
        # timepoints = 84 stacks, correctly revisiting position 1 after
        # position 42. That was silent before (Total time was dummy, see
        # _update_time_estimates); state the real total plainly so a
        # misconfigured timepoint count is obvious before/while it runs,
        # not after the fact.
        self._log(f"Sequence: {n_timepoints} timepoint(s) x {n_positions} position(s) = "
                  f"{len(steps)} stack(s) total, timepoint delay {delay_s:.3f} s.")
        return _AcqSequence(steps=steps, n_timepoints=n_timepoints, n_positions=n_positions,
                            timepoint_delay_s=delay_s)

    def _start_sequence_transition(self) -> None:
        """After a stack finishes naturally, mid-sequence: advance the step
        index and, off the GUI thread, move the stage (if the position
        changed) and/or wait out the timepoint delay (if this crosses into
        a new round), then signal the GUI thread to arm the next stack.

        Runs on a short-lived thread per transition rather than one
        persistent thread for the whole sequence -- simpler lifecycle, and
        each transition is naturally bounded (a move plus at most one
        delay), unlike the sequence as a whole which can run for hours.
        Never touches self.sample_stage_dialog's widgets directly (headless
        move_to_position_blocking only) since this is not the GUI thread.
        """
        seq = self._sequence
        prev_position = seq.current()[1]
        wait_s = seq.timepoint_delay_s if seq.starts_new_timepoint_round() else 0.0
        seq.index += 1
        _, position, xyz = seq.current()

        def run():
            try:
                if xyz is not None and position != prev_position:
                    self.sample_stage_dialog.move_to_position_blocking(xyz)
                if wait_s > 0 and self._sequence_abort.wait(timeout=wait_s):
                    return   # aborted during the timepoint delay
                if self._sequence_abort.is_set():
                    return   # aborted right after the move, before the signal
                self.sequence_signals.advance.emit()
            except Exception as e:                                     # noqa: BLE001
                self.sequence_signals.error.emit(f"{type(e).__name__}: {e}")

        threading.Thread(target=run, name="unmscope-sequence-transition", daemon=True).start()

    def _on_sequence_advance(self) -> None:
        if self._sequence is None or not self.acquiring:
            return   # a stray signal after the sequence was already aborted
        t, p, _ = self._sequence.current()
        self._log(f"Sequence: arming timepoint {t + 1}/{self._sequence.n_timepoints}, "
                  f"position {p + 1}/{self._sequence.n_positions}.")
        self.acq_status_label.setText(f"ACQUIRING ({self._sequence.index + 1}/{self._sequence.total_steps()})")
        self._arm_one_stack()

    def _on_sequence_error(self, message: str) -> None:
        self._log(f"Sequence transition FAILED: {message} -- aborting the rest of the run.")
        QMessageBox.warning(self, "Sample Stage", f"Sequence aborted:\n{message}")
        self._sequence = None
        self._stop_acquisition()

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
            # Ahead of stop_free_run()'s own safe_state() call, purely so the
            # gate thread stops before triggers are disarmed rather than
            # racing it -- safe_state() would catch this anyway.
            self.fpga.stop_aotf_digital_gate()
            self.aotf_gate_status.setText("idle")
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

        # ADDED 2026-09-22: decide whether this stop is a mid-sequence
        # advance (per-stack teardown only, stay ACQUIRING) or a real,
        # terminal stop (sequence finished/aborted/errored, or there never
        # was one -- the plain single-stack case, unchanged). See
        # self._stack_finished_naturally's definition in __init__ for why
        # every path except the one true natural-completion site defaults
        # to terminal/abort.
        naturally = self._stack_finished_naturally
        self._stack_finished_naturally = False
        advancing = naturally and self._sequence is not None and not self._sequence.is_last()
        if advancing:
            self._on_stack_finished()
            self._start_sequence_transition()
            return

        self._sequence_abort.set()
        if self._sequence is not None and self.sample_stage_dialog is not None:
            self.sample_stage_dialog.end_sequence_control()
        self.acquiring = False
        self.acquire_btn.setText("Acquire")
        self.acq_status_label.setText("IDLE")
        self.acq_status_label.setStyleSheet(
            f"background-color: {IDLE_GREEN}; color: white; font-weight: bold; "
            "padding: 6px; border-radius: 3px;"
        )
        self.acq_progress.setValue(0)
        self.overall_progress.setValue(0)
        self.mode_combo.setEnabled(True)
        self._update_scan_setup_enable_state()
        self._update_connection_buttons()
        self._clear_image_to_black()
        self._log("Acquisition stopped, back to IDLE.")
        # FIXED 2026-09-22: _on_stack_finished() reads self._sequence.current()
        # for the timepoint/position to save under -- for the sequence's own
        # LAST step, this is the terminal branch (is_last() is what routes it
        # here), so self._sequence must still be valid when this runs. Clearing
        # it before the save silently fell back to (0, None, None), landing
        # the sequence's final stack outside any position folder.
        self._on_stack_finished()
        self._sequence = None

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
            # ADDED 2026-09-22: overall_progress used to just duplicate
            # acq_progress (there was only ever one stack); now it reflects
            # the whole sequence, acq_progress just the current stack.
            if self._sequence is not None:
                steps = self._sequence.total_steps()
                overall = min(100, int(round(100 * (self._sequence.index + count / total) / steps)))
                self.overall_progress.setValue(overall)
            else:
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
                  + (f" ({self._stale_discarded} stale pre-trigger frame discarded)" if self._stale_discarded else "")
                  + ".")

    def _finish_if_still_waiting(self):
        if self.acquiring and self._finishing:
            self._log(f"Z-stack: timed out waiting for frames "
                      f"({self.frame_count} of {self.z_target_frames} received). Stopping.")
            self._stop_acquisition()

    def _is_stale_pre_trigger_frame(self) -> bool:
        """True while it is physically too early for a triggered frame to
        exist. Only a real camera can have stale frames; the simulated one
        gets its edges in software and delivers instantly.

        The window used to be half the trigger period. That is wrong once
        the cycle time is much longer than a frame: with a 20 ms exposure
        and a 0.6 s Custom Cycle Time the first REAL slice lands ~55 ms
        after the arm, inside a 300 ms window, and was thrown away (found
        2026-09-06). A real frame cannot exist before an exposure and a
        readout have both happened after the first edge, so the window is
        capped at half of that, whatever the cycle time.
        """
        if self.camera is None or not self.camera.reacts_to_dio4:
            return False
        if self._arm_time <= 0 or self._trigger_period_s <= 0:
            return False
        physical_s = (self.exposure_spin.value() + self.camera.readout_ms()) / 1000.0
        window_s = min(0.5 * self._trigger_period_s, 0.5 * physical_s)
        return time.perf_counter() - self._arm_time < window_s

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
                    #
                    # In SYNCREADOUT that leftover IS the warm-up frame the
                    # backend told us to expect (the previous run's open
                    # exposure, read out by the first edge). Discarding it
                    # here WITHOUT charging the warm-up count meant the next
                    # frame -- slice 1 -- was then discarded as warm-up:
                    # N-1 frames, a timeout, and no save (2026-09-06, found
                    # by the frame-loss hunt, reproduced on the fake
                    # backends). One physical frame, one discard.
                    if self._warmup_remaining > 0:
                        self._warmup_remaining -= 1
                        self._warmup_discarded += 1
                        self._log("Discarded the expected warm-up frame (it arrived inside "
                                  "the pre-trigger window).")
                    else:
                        self._stale_discarded += 1
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
            # The ONE true "this stack really finished" site -- see
            # self._stack_finished_naturally's definition in __init__.
            self._stack_finished_naturally = True
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
        # Saved BEFORE the teardown: disconnecting can drive controls (the
        # camera tab is written back from the device on connect/disconnect),
        # and what should be remembered is what was on screen when the user
        # decided to close, not what the shutdown left behind.
        self._save_ui_state()
        if self.acquiring:
            self._stop_acquisition()
        if self.camera is not None:
            self.on_disconnect_clicked()
        if self.fpga is not None:
            self.on_fpga_disconnect_clicked()
        event.accept()
