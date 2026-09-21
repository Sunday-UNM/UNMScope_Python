"""The Utilities tab: LouisXIV's tool grid (11 of its 18 tools -- the user
dropped Align Laser, Image Reviewer, Calculate PSF, Auto Background, View
TIF stack, Resave OME-XML TIFs and Shift Vslit calibration on 2026-09-05),
plus the Low-Level Waveform Config page the user asked to have here
(LouisXIV keeps it under Adv Setup).

Grid layout MEASURED on the COM-rendered Utilities page of SPIM MAIN.vi
(2026-09-05): two columns of 152 x 56 buttons at x = 21 and 201 of the
render (tab body border at x = 8), rows from y = 113 with a 72 px pitch
(tab page top at y = 76). The remaining tools keep LouisXIV's reading order
and close up the gaps left by the removed ones.

Wired: um per V calibration (its own window), Sample Stage Control, Camera
Debug Panel, FPGA Scope (the Waveforms tab), Reset HW (stop, re-open the FPGA
and the camera, re-apply settings -- LouisXIV's "Reset HW" engine state), HW
Config. The rest are greyed until ported.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import QFrame, QPushButton, QScrollArea, QTabWidget, QWidget

from unmscope.gui.waveform_config_panel import WaveformConfigPanel
from unmscope.config.waveform_config import WaveformConfig

TOOLS = [  # (label, wired-callback name or None), LouisXIV's reading order
    ("um per V\ncalibration", "um_per_volt"), ("View Z Lookup\nTable", None),
    ("Sample Stage\nControl", "sample_stage"), ("X&Z Galvo offsets\nper AOTF ch", None),
    ("Camera Debug\nPanel", "camera_debug"), ("FPGA\nScope", "fpga_scope"),
    ("FPGA Monitor", None), ("Reset HW", "reset_hw"),
    ("X Galvo Z\nCorrections", None), ("HW Config", "hw_config"),
    ("Imagine Optics", None),
]
# Note: "Nikon Z Focus" and "ASI X/Y Stage" were removed (2026-09-14):
# both are now integrated into the unified "Sample Stage Control" window,
# which has two COM port connections (COM5 for ASI X/Y, COM8 for Arduino Z).
COL_X = (21 - 8, 201 - 8)
ROW_Y0, ROW_PITCH = 113 - 76, 72
BTN_W, BTN_H = 152, 56


class UtilitiesTab(QTabWidget):
    def __init__(self, *, fpga_scope: Callable[[], None],
                 camera_debug: Callable[[], None] | None = None,
                 hw_config: Callable[[], None] | None = None,
                 sample_stage: Callable[[], None] | None = None,
                 um_per_volt: Callable[[], None] | None = None,
                 reset_hw: Callable[[], None] | None = None,
                 waveform_config: WaveformConfig | None = None, parent=None):
        super().__init__(parent)
        noop = lambda: None
        self._actions = {"fpga_scope": fpga_scope,
                         "camera_debug": camera_debug or noop, "hw_config": hw_config or noop,
                         "sample_stage": sample_stage or noop, "um_per_volt": um_per_volt or noop,
                         "reset_hw": reset_hw or noop}
        self.buttons: dict[str, QPushButton] = {}
        tools = QWidget()
        tools.setMinimumSize(370, ROW_Y0 + ((len(TOOLS) + 1) // 2) * ROW_PITCH)
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

