"""The Low-Level Waveform Config page (LouisXIV: Adv Setup > Low-Level
Waveform Config -- the "Waveform" cluster), placed under Utilities here as
the user asked.

Layout: every rectangle is MEASURED on the COM-rendered page of SPIM MAIN.vi
(2026-09-05, flood-fill of the white fields and the (170,170,170)
sub-cluster frames), expressed relative to the cluster frame's top-left
(full-render (13,153)); the frame itself is 360 x 588. Colours measured:
cluster (204,204,204), sub-clusters (170,170,170), greyed block
(221,221,221), fields white, pressed toggles (204,204,255).

Live (editable, honoured by unmscope.hardware.louisxiv_waveform): Updates/Pix,
Fractional Flyback, Fract. Smoothing, X Single Direction, Dither Triangle
Pulses, Dither Fract. Flyback. Indicators
(read-only, refreshed by the window): Pixel/ms, Cam exp, Cycle time, the axis
sub-clusters. Everything else is shown with its LouisXIV default and greyed
until the corresponding part of "Calculate Waveforms" is ported (see
docs/utilities_and_waveform_config.md).
"""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QLabel, QLineEdit, QPushButton, QSpinBox, QWidget,
)

from unmscope.config.waveform_config import (
    AOTF_CYCLE, AOTF_SWEEP_MODE, DUAL_VIEW, ONE_EXP_PER, WAIT_FOR_Z_SETTLE, WAVEFORM_TYPES, X_WAVE,
    Z_MOTION, Z_WAVE, AxisSettings, WaveformConfig,
)
from unmscope.gui.widgets import set_bold

CLUSTER_BG = "#cccccc"
SUB_BG = "#aaaaaa"
GREYED_BG = "#dddddd"
TOGGLE_ON = "#ccccff"
FRAME_X, FRAME_Y = 5, 20          # cluster frame origin on the page; label above it
FRAME_W, FRAME_H = 360, 588


def _r(x, y, w, h):
    return (FRAME_X + x, FRAME_Y + y, w, h)


class WaveformConfigPanel(QWidget):
    changed = Signal(object)          # WaveformConfig, after any live edit

    LIVE = ("fractional_flyback", "fract_smoothing", "updates_per_pix", "x_single_direction",
            "dither_triangle_pulses", "dither_fract_flyback")

    def __init__(self, cfg: WaveformConfig | None = None, parent=None):
        super().__init__(parent)
        self._cfg = cfg or WaveformConfig()
        self._updating = False
        self.setMinimumSize(FRAME_X + FRAME_W + 10, FRAME_Y + FRAME_H + 10)
        self.setStyleSheet(f"WaveformConfigPanel {{ background: {CLUSTER_BG}; }}")
        self._build()
        self.set_config(self._cfg)

    # -- widget helpers ------------------------------------------------------------
    def _label(self, text, rect, *, bold=False, right=False):
        lab = QLabel(text, self)
        lab.setGeometry(*rect)
        lab.setAlignment((Qt.AlignRight if right else Qt.AlignLeft) | Qt.AlignVCenter)
        if bold:
            set_bold(lab)
        return lab

    def _rlabel(self, text, field_rect, width=100):
        """A label right-aligned to end 4 px left of its field."""
        x, y, w, h = field_rect
        return self._label(text, (x - 4 - width, y, width, h), right=True)

    def _frame(self, rect, bg, name):
        f = QFrame(self)
        f.setObjectName(name)
        x, y, w, h = rect
        if name != "cluster":
            # the measured rects are the FILL; LouisXIV draws the bevel outside it
            x, y, w, h = x - 1, y - 1, w + 2, h + 2
        f.setGeometry(x, y, w, h)
        f.setStyleSheet(f"QFrame#{name} {{ background: {bg}; border: 1px solid #666666; }}")
        if name == "cluster":
            f.lower()          # only the cluster goes to the bottom; sub-frames sit on it
        return f

    def _dspin(self, rect, lo, hi, dec=2, live=False):
        s = QDoubleSpinBox(self)
        s.setGeometry(*rect); s.setRange(lo, hi); s.setDecimals(dec)
        s.setButtonSymbols(QDoubleSpinBox.NoButtons); s.setAlignment(Qt.AlignRight)
        s.setKeyboardTracking(False)
        s.setEnabled(live)
        if live:
            s.valueChanged.connect(self._on_edit)
        return s

    def _ispin(self, rect, lo, hi, live=False):
        s = QSpinBox(self)
        s.setGeometry(*rect); s.setRange(lo, hi); s.setAlignment(Qt.AlignRight)
        s.setKeyboardTracking(False); s.setEnabled(live)
        if live:
            s.valueChanged.connect(self._on_edit)
        return s

    def _combo(self, rect, items, live=False):
        c = QComboBox(self)
        c.setGeometry(*rect); c.addItems(list(items)); c.setEnabled(live)
        if live:
            c.currentTextChanged.connect(self._on_edit)
        return c

    def _ro(self, rect, text=""):
        f = QLineEdit(text, self)
        f.setGeometry(*rect); f.setReadOnly(True); f.setAlignment(Qt.AlignRight)
        f.setFocusPolicy(Qt.NoFocus)
        return f

    def _toggle(self, rect, text="", on=False):
        b = QPushButton(text, self)
        b.setGeometry(*rect); b.setCheckable(True); b.setChecked(on); b.setEnabled(False)
        b.setStyleSheet(f"QPushButton:checked {{ background: {TOGGLE_ON}; }}")
        return b

    def _axis(self, name, rect, *, greyed=False):
        """A Size / Pixels sub-cluster: (index, value, size, pixels) fields."""
        x, y, w, h = rect
        self._frame(rect, GREYED_BG if greyed else SUB_BG, "ax_" + name.lower())
        self._label(name, (x + 3, y + 1, 40, 14), bold=True)
        idx = self._ispin((x + 6, y + 5, 18, 14), 0, 99)
        val = self._ro((x + 43, y + 3, 52, 16))
        size = self._ro((x + 43, y + 21, 52, 18))
        pix = self._ro((x + 43, y + 41, 52, 16))
        self._label("Size", (x + 3, y + 21, 38, 18), right=True)
        self._label("Pixels", (x + 3, y + 41, 38, 16), right=True)
        return {"index": idx, "value": val, "size": size, "pixels": pix}

    # -- construction --------------------------------------------------------------
    def _build(self):
        self._label("Waveform", (7, 2, 80, 16))
        self._frame((FRAME_X, FRAME_Y, FRAME_W, FRAME_H), CLUSTER_BG, "cluster")
        self.waveform = self._combo(_r(106, 4, 83, 19), WAVEFORM_TYPES); self._rlabel("Waveform", _r(106, 4, 83, 19))
        self.x_single_direction = self._toggle(_r(194, 6, 30, 14), "→", True)
        self.x_single_direction.setEnabled(True)
        self.x_single_direction.toggled.connect(self._on_edit)
        self._label("X Single Direction", _r(228, 4, 120, 18))
        self.pixel_per_ms = self._ro(_r(106, 26, 38, 18)); self._rlabel("Pixel / ms", _r(106, 26, 38, 18))
        self.updates_per_pix = self._ispin(_r(93, 49, 52, 18), 1, 100, live=True); self._rlabel("Updates/Pix", _r(93, 49, 52, 18))
        # Excitations + X (greyed block, as on the panel)
        self._frame(_r(147, 51, 203, 97), GREYED_BG, "excitations")
        self._label("Excitations", _r(175, 50, 80, 14))
        self.exc_index = self._ispin(_r(154, 65, 19, 15), 0, 9)
        self.exc_on = QCheckBox(self); self.exc_on.setGeometry(*_r(179, 61, 13, 13)); self.exc_on.setEnabled(False)
        self.exc_v = self._ro(_r(227, 57, 46, 17), "0.1"); self._label("V", _r(275, 57, 12, 17))
        self.exc_pct = self._ro(_r(287, 57, 46, 17), "0.1"); self._label("%", _r(335, 57, 12, 17))
        self.ax = {
            "x": self._axis("X", _r(148, 85, 98, 60), greyed=True),
            "xwvfrm": self._axis("Xwvfrm", _r(253, 85, 94, 60)),
            "z": self._axis("Z", _r(149, 152, 98, 60)),
            "spiezo": self._axis("Spiezo", _r(251, 152, 98, 60)),
            "zpiezo": self._axis("Zpiezo", _r(149, 216, 98, 60)),
            "dither": self._axis("Dither", _r(251, 216, 98, 60)),
        }
        self.fractional_flyback = self._dspin(_r(107, 91, 38, 18), 0, 1, 3, live=True)
        self._rlabel("Fractional Flyback", _r(107, 91, 38, 18))
        self.fract_smoothing = self._dspin(_r(107, 110, 38, 18), 0, 100, 2, live=True)
        self._rlabel("Fract. Smoothing", _r(107, 110, 38, 18))
        rows = [("aotf_delay_us", "AOTF delay (us)", 185), ("x_galvo_delay_us", "X galvo delay (us)", 204),
                ("z_galvo_delay_us", "Z galvo delay (us)", 223), ("z_piezo_delay_us", "Z piezo delay (us)", 242),
                ("sweep_period_um", "Sweep period (um)", 261), ("duty_pct", "Duty (%)", 280)]
        for attr, text, y in rows:
            setattr(self, attr, self._dspin(_r(107, y, 38, 18), -1e6, 1e6, 3))
            self._rlabel(text, _r(107, y, 38, 18))
        self.n_integrations = self._ispin(_r(107, 299, 38, 18), 1, 1000); self._rlabel("# of Integrations", _r(107, 299, 38, 18))
        self.cam_exp_s = self._ro(_r(82, 318, 63, 18)); self._rlabel("Cam exp (s)", _r(82, 318, 63, 18), 76)
        self.cycle_time_s = self._ro(_r(82, 337, 63, 18)); self._rlabel("Cycle time (s)", _r(82, 337, 63, 18), 76)
        self.linked = self._toggle(_r(150, 282, 56, 16), "Linked", True)
        self.xz_correct = self._toggle(_r(150, 299, 56, 17), "XZcrrct", True)
        self.aotf_cycle = self._combo(_r(275, 280, 73, 19), AOTF_CYCLE); self._rlabel("AOTF cycle", _r(275, 280, 73, 19), 66)
        self.one_exp_per = self._combo(_r(275, 300, 73, 17), ONE_EXP_PER); self._rlabel("1 exp per", _r(275, 300, 73, 17), 66)
        self.wait_for_z_settle = self._combo(_r(275, 318, 73, 19), WAIT_FOR_Z_SETTLE); self._rlabel("Wait for Zsettle?", _r(275, 318, 73, 19), 100)
        self.z_wave = self._combo(_r(275, 338, 73, 17), Z_WAVE); self._rlabel("Z wave", _r(275, 338, 73, 17), 66)
        self.dual_view = self._combo(_r(275, 356, 73, 19), DUAL_VIEW); self._rlabel("Dual View", _r(275, 356, 73, 19), 66)
        self.z_motion = self._combo(_r(68, 359, 98, 19), Z_MOTION); self._rlabel("Z motion", _r(68, 359, 98, 19), 60)
        self.n_doe_beams = self._ispin(_r(128, 380, 38, 18), 1, 100); self._rlabel("# of DOE beams", _r(128, 380, 38, 18))
        self.x_wave = self._combo(_r(88, 402, 78, 19), X_WAVE); self._rlabel("X wave", _r(88, 402, 78, 19), 60)
        self.doe_period_um = self._dspin(_r(311, 378, 37, 18), 0, 1e4, 1); self._rlabel("DOE period (um)", _r(311, 378, 37, 18), 110)
        self.x_triangle_pulses = self._dspin(_r(311, 400, 37, 18), 0, 1e3, 1); self._rlabel("X Triangle Pulses", _r(311, 400, 37, 18), 110)
        self.aotf_sweep_mode = self._combo(_r(288, 420, 60, 19), AOTF_SWEEP_MODE); self._rlabel("AOTF sweep mode", _r(288, 420, 60, 19), 110)
        self.dither_triangle_pulses = self._dspin(_r(311, 442, 37, 18), 0, 1e3, 1, live=True)
        self._rlabel("Dither Triangle Pulses", _r(311, 442, 37, 18), 130)
        self.dither_fract_flyback = self._dspin(_r(311, 464, 38, 18), 0, 1, 3, live=True)
        self._rlabel("Dither Fract. Flyback", _r(311, 464, 38, 18), 130)
        self.aotf_pulse_width_um = self._dspin(_r(311, 484, 38, 20), 0, 1e4, 1); self._rlabel("AOTF Pulse Width (um)", _r(311, 484, 38, 20), 130)
        self.aotf_pulse_duty_pct = self._dspin(_r(311, 506, 38, 20), 0, 100, 1); self._rlabel("AOTF Pulse Duty %", _r(311, 506, 38, 20), 130)
        self.z_bidirectional = self._toggle(_r(109, 465, 33, 14), "→")
        self._rlabel("Z Bidirectional", _r(109, 465, 33, 14)); self._label("OFF", _r(146, 465, 30, 14))
        self._label("Virtual Confocal", _r(40, 498, 100, 16), bold=True)
        self.virtual_confocal = QLabel(self); self.virtual_confocal.setGeometry(*_r(145, 500, 14, 14))
        self.virtual_confocal.setStyleSheet("background: #204020; border-radius: 7px; border: 1px solid #333;")
        self.custom_cycle_time = QCheckBox(self); self.custom_cycle_time.setGeometry(*_r(169, 536, 13, 13))
        self.custom_cycle_time.setEnabled(False); self._rlabel("Custom Cycle Time", _r(169, 536, 13, 13), 110)
        self._label("Z Piezo Selector", _r(10, 552, 92, 21))
        self.z_piezo_1 = self._toggle(_r(105, 552, 44, 21), "1", True)
        self.z_piezo_2 = self._toggle(_r(153, 552, 44, 21), "2")

    # -- state ------------------------------------------------------------------------
    def set_config(self, cfg: WaveformConfig) -> None:
        self._cfg = cfg
        self._updating = True
        try:
            self.waveform.setCurrentText(cfg.waveform)
            self.x_single_direction.setChecked(cfg.x_single_direction)
            self.pixel_per_ms.setText(f"{cfg.pixel_per_ms:g}")
            self.updates_per_pix.setValue(cfg.updates_per_pix)
            self.fractional_flyback.setValue(cfg.fractional_flyback)
            self.fract_smoothing.setValue(cfg.fract_smoothing)
            for attr in ("aotf_delay_us", "x_galvo_delay_us", "z_galvo_delay_us", "z_piezo_delay_us",
                         "sweep_period_um", "duty_pct", "doe_period_um", "x_triangle_pulses",
                         "dither_triangle_pulses", "dither_fract_flyback", "aotf_pulse_width_um",
                         "aotf_pulse_duty_pct"):
                getattr(self, attr).setValue(getattr(cfg, attr))
            self.n_integrations.setValue(cfg.n_integrations)
            self.n_doe_beams.setValue(cfg.n_doe_beams)
            self.cam_exp_s.setText(f"{cfg.cam_exp_s:g}")
            self.cycle_time_s.setText(f"{cfg.cycle_time_s:g}")
            for attr in ("aotf_cycle", "one_exp_per", "wait_for_z_settle", "z_wave", "dual_view",
                         "z_motion", "x_wave", "aotf_sweep_mode"):
                getattr(self, attr).setCurrentText(getattr(cfg, attr))
            self.linked.setChecked(cfg.linked)
            self.xz_correct.setChecked(cfg.xz_correct)
            self.z_bidirectional.setChecked(cfg.z_bidirectional)
            self.custom_cycle_time.setChecked(cfg.custom_cycle_time)
            self.z_piezo_1.setChecked(cfg.z_piezo_selector == 1)
            self.z_piezo_2.setChecked(cfg.z_piezo_selector == 2)
            for name, w in self.ax.items():
                self._show_axis(w, getattr(cfg, name))
        finally:
            self._updating = False

    def _show_axis(self, w, a: AxisSettings) -> None:
        w["index"].setValue(a.index)
        w["value"].setText(f"{a.value:g}")
        w["size"].setText(f"{a.size:g}")
        w["pixels"].setText(f"{a.pixels:g}")

    def config(self) -> WaveformConfig:
        """The configuration with the live fields read from the widgets."""
        return replace(self._cfg,
                       fractional_flyback=self.fractional_flyback.value(),
                       fract_smoothing=self.fract_smoothing.value(),
                       updates_per_pix=self.updates_per_pix.value(),
                       x_single_direction=self.x_single_direction.isChecked(),
                       dither_triangle_pulses=self.dither_triangle_pulses.value(),
                       dither_fract_flyback=self.dither_fract_flyback.value())

    def set_indicators(self, *, cam_exp_s: float | None = None, cycle_time_s: float | None = None,
                       pixel_per_ms: float | None = None, axes: dict[str, AxisSettings] | None = None) -> None:
        """Refresh the read-only fields from the window (exposure, trigger
        period, AO rate / Updates per Pixel, and the Scan Setup axes)."""
        if cam_exp_s is not None:
            self._cfg = replace(self._cfg, cam_exp_s=cam_exp_s); self.cam_exp_s.setText(f"{cam_exp_s:g}")
        if cycle_time_s is not None:
            self._cfg = replace(self._cfg, cycle_time_s=cycle_time_s); self.cycle_time_s.setText(f"{cycle_time_s:g}")
        if pixel_per_ms is not None:
            self._cfg = replace(self._cfg, pixel_per_ms=pixel_per_ms); self.pixel_per_ms.setText(f"{pixel_per_ms:g}")
        for name, a in (axes or {}).items():
            if name in self.ax:
                self._cfg = replace(self._cfg, **{name: a}); self._show_axis(self.ax[name], a)

    def _on_edit(self, *_):
        if self._updating:
            return
        self._cfg = self.config()
        self.changed.emit(self._cfg)
