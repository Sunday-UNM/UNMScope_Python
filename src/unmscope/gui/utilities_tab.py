"""The Utilities tab: LouisXIV's 18-button tool grid, plus the Low-Level
Waveform Config page the user asked to have here (LouisXIV keeps it under
Adv Setup).

Grid layout MEASURED on the COM-rendered Utilities page of SPIM MAIN.vi
(2026-09-05): two columns of 152 x 56 buttons at x = 21 and 201 of the
render (tab body border at x = 8), rows from y = 113 with a 72 px pitch
(tab page top at y = 76), in LouisXIV's order (down the left column, then
the right).

Wired here: "View TIF stack" (load a stack into the window, as if it had
just been acquired, so Calc / save work on it), "FPGA Scope" (shows the
Waveforms tab) and "Camera Debug Panel" (LouisXIV's read-only status window,
gui/camera_debug_panel.py). Every other tool is greyed until it is ported; "Auto
Background" is greyed in LouisXIV too. Which of them to port is the user's
call (cleanup directive).
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import QFileDialog, QFrame, QPushButton, QScrollArea, QTabWidget, QWidget

from unmscope.gui.waveform_config_panel import WaveformConfigPanel
from unmscope.config.waveform_config import WaveformConfig

TOOLS = [  # (label, wired-callback name or None), LouisXIV order
    ("Align Laser", None), ("View TIF stack", "view_tif"),
    ("Image Reviewer", None), ("um per V\ncalibration", None),
    ("Calculate PSF", None), ("View Z Lookup\nTable", None),
    ("Sample Stage\nControl", None), ("Shift Vslit\ncalibration", None),
    ("Resave\nOME-XML TIFs", None), ("X&Z Galvo offsets\nper AOTF ch", None),
    ("Camera Debug\nPanel", "camera_debug"), ("FPGA\nScope", "fpga_scope"),
    ("Auto\nBackground", None), ("FPGA Monitor", None),
    ("Reset HW", None), ("X Galvo Z\nCorrections", None),
    ("HW Config", None), ("Imagine Optics", None),
]
COL_X = (21 - 8, 201 - 8)
ROW_Y0, ROW_PITCH = 113 - 76, 72
BTN_W, BTN_H = 152, 56


class UtilitiesTab(QTabWidget):
    def __init__(self, *, view_tif: Callable[[str], None], fpga_scope: Callable[[], None],
                 camera_debug: Callable[[], None] | None = None,
                 waveform_config: WaveformConfig | None = None, parent=None):
        super().__init__(parent)
        self._actions = {"view_tif": self._on_view_tif, "fpga_scope": fpga_scope,
                         "camera_debug": camera_debug or (lambda: None)}
        self._view_tif = view_tif
        self.buttons: dict[str, QPushButton] = {}
        tools = QWidget()
        tools.setMinimumSize(370, ROW_Y0 + 9 * ROW_PITCH)
        for i, (label, action) in enumerate(TOOLS):
            b = QPushButton(label, tools)
            b.setGeometry(COL_X[i % 2], ROW_Y0 + (i // 2) * ROW_PITCH, BTN_W, BTN_H)
            f = b.font(); f.setPointSize(11); b.setFont(f)
            if action is None:
                b.setEnabled(False)
                b.setToolTip("Not ported from LouisXIV yet")
            else:
                b.clicked.connect(self._actions[action])
            self.buttons[label.replace("\n", " ")] = b
        scroll = QScrollArea(); scroll.setWidget(tools); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self.addTab(scroll, "Tools")
        self.waveform_panel = WaveformConfigPanel(waveform_config)
        scroll2 = QScrollArea(); scroll2.setWidget(self.waveform_panel); scroll2.setWidgetResizable(True)
        scroll2.setFrameShape(QFrame.NoFrame)
        self.addTab(scroll2, "Low-Level Waveform Config")

    def _on_view_tif(self):
        path, _ = QFileDialog.getOpenFileName(self, "View TIF stack", "", "TIFF stacks (*.tif *.tiff)")
        if path:
            self._view_tif(path)
