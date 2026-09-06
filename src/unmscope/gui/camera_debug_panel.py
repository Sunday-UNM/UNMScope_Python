"""LouisXIV's Camera Debug Panel (GUI/Camera Debug Panel.vi, window title
"Debug Panel"; SPIM MAIN event case [25] opens it with FP.Open + Run VI,
non-modal). A read-only status window: per camera, the acquisition /
download / save counters the engine keeps, the image-buffer size, the number
of open TIFF refnums, the DCAM API flag and the last error. It has no camera
settings at all.

Layout MEASURED on the COM-rendered panel (Camera Debug Panelp.png, 677 x
510, background (221,221,221)): seven array columns of five cells, outer
bevel 58 x 128 at x = 40, 144, 240, 324, 420, 504, 601 / y = 19; cells 52 x
22 at y = 22 + 25 k with a 46 x 17 interior at (+3, +3); column headers at
y ~ 9; row labels 'Cam 1'..'Cam 5' at x 4..40; the flat 'DCAM API' LED
34 x 10 at (456, 294); the error-out cluster 151 x 116 at (459, 391) with
the status LED at (587, 461), code field at (510, 414), source box at
(465, 459).

Dropped on the cleanup rule (documented, not faked): the remote-acquisition
block -- 'Include Remotes?', the 'Remote Camera Selector' ring and the
'Remote Status' cluster (SPIMProject.ini has Remote = FALSE for all five
cameras and UNMScope has no TCP remote nodes) -- and the 'All Cameras?' /
'Camera ID' filter (one camera). Rows Cam 2..Cam 5 stay, greyed and empty,
exactly as LouisXIV renders an empty array.

What each column shows here (LouisXIV source -> Python source):
  Exposures Dwnld   HHMI - Get new images '# Exp Downloaded'   -> frames popped from the camera
  Exposures Acqd    '# of Exps Acq'                            -> FPGA triggers fired
  Total #           '# of images all stacks (no skipped)'      -> frames expected for the run
  Dwnld backlog     '# of images to be downloaded'             -> camera.remaining_image_count()
  Save backlog      image-spooling queue length                -> 0 (saving is synchronous here)
  Img Buffer Sizes  DCam Module 'Get Img Buff Size'             -> camera.buffer_capacity()[1] (slots)
  Open File Refs    open TIFF refnums                           -> 0 (no spooler)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QLabel, QLineEdit, QWidget

from unmscope.gui.widgets import set_bold

BG = "#dddddd"            # measured (221,221,221)
BEVEL = "#aaaaaa"         # measured (170,170,170)
CELL_INTERIOR = "#dddddd"
GREY_TEXT = "#777777"     # measured (119,119,119): LouisXIV's empty-array digits
COLUMNS = ("Exposures Dwnld", "Exposures Acqd", "Total #", "Dwnld backlog", "Save backlog",
           "Img Buffer Sizes", "Open File Refs")   # header order as rendered
COLUMN_X = (40, 144, 240, 324, 420, 504, 601)
ROW_Y0, ROW_PITCH, N_ROWS = 19, 25, 5
REFRESH_MS = 500          # LouisXIV's period is not visible in the render (assumed)


@dataclass
class CameraDebugStatus:
    exposures_downloaded: int = 0
    total_expected: int = 0
    exposures_acquired: int = 0
    download_backlog: int = 0
    save_backlog: int = 0
    image_buffer_slots: int = 0
    open_file_refs: int = 0
    dcam_api: bool = False
    error_text: str = ""
    connected: bool = False

    def counters(self) -> tuple[int, ...]:
        return (self.exposures_downloaded, self.exposures_acquired, self.total_expected,
                self.download_backlog, self.save_backlog, self.image_buffer_slots, self.open_file_refs)


class CameraDebugPanel(QWidget):
    """Non-modal top-level window; ``status_provider()`` is called on the GUI
    thread every REFRESH_MS and must be cheap and safe (the window skips the
    poll while a blocking driver call is in progress)."""

    def __init__(self, status_provider: Callable[[], CameraDebugStatus | None], parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("Debug Panel")
        self.setFixedSize(677, 510)
        self.setStyleSheet(f"CameraDebugPanel {{ background: {BG}; }}")
        self._provider = status_provider
        self._cells: list[list[QLabel]] = []
        self._build()
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    # -- construction ------------------------------------------------------------
    def _label(self, text, rect, *, bold=False, color=None, align=Qt.AlignLeft | Qt.AlignVCenter):
        lab = QLabel(text, self)
        lab.setGeometry(*rect)
        lab.setAlignment(align)
        if bold:
            set_bold(lab)
        if color:
            lab.setStyleSheet(f"color: {color};")
        return lab

    def _bevel(self, rect):
        f = QFrame(self)
        f.setObjectName("bevel")
        f.setGeometry(*rect)
        f.setStyleSheet(f"QFrame#bevel {{ background: {BEVEL}; border: 1px solid #999999; }}")
        return f

    def _build(self):
        for c, (name, x) in enumerate(zip(COLUMNS, COLUMN_X)):
            # headers centred on the 58 px column; 96 px so this font does not clip them
            self._label(name, (x - 19, 2, 96, 15), align=Qt.AlignHCenter | Qt.AlignVCenter)
            self._bevel((x, ROW_Y0, 58, 128))
            cells = []
            for r in range(N_ROWS):
                y = ROW_Y0 + 3 + r * ROW_PITCH
                self._bevel((x + 3, y, 52, 22))
                lab = QLabel("0", self)
                lab.setGeometry(x + 6, y + 3, 46, 17)
                lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                lab.setStyleSheet(f"background: {CELL_INTERIOR}; color: {GREY_TEXT};")
                cells.append(lab)
            self._cells.append(cells)
        for r in range(N_ROWS):
            self._label(f"Cam {r + 1}", (4, ROW_Y0 + 6 + r * ROW_PITCH, 36, 17))
        # DCAM API LED (flat rectangular, dark green off / bright green on)
        self.dcam_led = QFrame(self)
        self.dcam_led.setGeometry(456, 294, 34, 10)
        self._set_led(self.dcam_led, False)
        self._label("DCAM API", (494, 290, 80, 18))
        # error out cluster
        self._label("error out", (459, 374, 80, 16))
        self._bevel((459, 391, 151, 116))
        self._label("status", (465, 395, 40, 14)); self._label("code", (510, 395, 40, 14))
        self.err_led = QFrame(self)
        self.err_led.setGeometry(587, 461, 16, 40)
        self._set_led(self.err_led, False, kind="status")
        self.err_code = QLineEdit("0", self)
        self.err_code.setGeometry(510, 414, 96, 20)
        self.err_code.setReadOnly(True); self.err_code.setAlignment(Qt.AlignRight)
        self._label("source", (465, 440, 60, 14))
        self.err_source = QLineEdit("", self)
        self.err_source.setGeometry(465, 459, 118, 45)
        self.err_source.setReadOnly(True)
        self.err_source.setAlignment(Qt.AlignLeft | Qt.AlignTop)

    @staticmethod
    def _set_led(frame: QFrame, on: bool, kind: str = "flat"):
        colour = "#40ff40" if on else "#204020"
        if kind == "status":
            colour = "#ff4040" if on else "#40a040"
        frame.setStyleSheet(f"background: {colour}; border: 1px solid #333333;")

    # -- refresh ---------------------------------------------------------------------
    def refresh(self) -> None:
        try:
            st = self._provider()
        except Exception as e:            # the panel must never take the GUI down
            st = CameraDebugStatus(error_text=f"{type(e).__name__}: {e}")
        if st is None:
            return
        self.last_status = st
        for c, value in enumerate(st.counters()):
            cell = self._cells[c][0]
            cell.setText(str(value))
            cell.setStyleSheet(f"background: {CELL_INTERIOR}; color: {'#000000' if st.connected else GREY_TEXT};")
        self._set_led(self.dcam_led, st.dcam_api)
        self._set_led(self.err_led, bool(st.error_text), kind="status")
        self.err_code.setText("1" if st.error_text else "0")
        self.err_source.setText(st.error_text)

    def closeEvent(self, event):
        self._timer.stop()
        super().closeEvent(event)

    def showEvent(self, event):
        if not self._timer.isActive():
            self._timer.start()
        super().showEvent(event)
