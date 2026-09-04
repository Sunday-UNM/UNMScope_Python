"""Waveforms tab: the FPGA Scope trace view, modelled on LouisXIV's FPGA
Scope (the front panel of ``HHMI - AI buffer.vi``): a black graph with a
dotted grid, Volts against Time (s), a legend of per-channel checkboxes
with colour swatches, "# of seconds to buff", "Clear" and "# points acq".

Data comes from :class:`unmscope.hardware.fpga_scope.FpgaScope`. Every
column is drawn in volts = counts x 10/32768, so the digital flags (0 /
4096 counts) show as 1.25 V steps exactly as they do in LabVIEW. Long
windows are decimated per pixel column with a min/max envelope so a 100 us
trigger pulse stays visible inside a 5 s window.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QPolygonF
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from unmscope.hardware.fpga_scope import (
    AI_CHANNEL_NAMES, AI_VOLTS_PER_COUNT, IDX_DIO4, FpgaScope, ScopeSnapshot, measure_period,
)

#: LouisXIV's default 'Active Channels' (8,14,15,16,17,9,10,11):
#: X Galvo, Int Sync, DIO4, AOTF0, AOTF1, Z Galvo, Z Piezo, Dither Galvo.
DEFAULT_ACTIVE = (8, 14, 15, 16, 17, 9, 10, 11)
#: Legend labels as LouisXIV shows them, by column index (others use the map names).
LEGEND_LABELS = {14: "Int Cycle Trigger", 15: "D4 Cam Ext Trig Out", 8: "X Galvo", 9: "Z Galvo",
                 10: "Z Piezo", 11: "Dither Galvo", 12: "AOTF0 (AO)", 13: "Filter",
                 16: "AOTF 0", 17: "AOTF 1", 18: "AOTF 2", 19: "AOTF 3", 20: "AOTF 4", 21: "AOTF 5", 22: "AOTF 6"}
PALETTE = ["#ffffff", "#ff4040", "#40ff40", "#4080ff", "#ffff40", "#ff40ff", "#40ffff", "#ff9020",
           "#a0a0a0", "#c060ff", "#60c0ff", "#80ff80", "#ffb0b0", "#b0ffb0", "#b0b0ff", "#ffe080",
           "#e0e0e0", "#ff8080", "#80ff80", "#8080ff", "#ffff80", "#ff80ff", "#80ffff", "#ffc080",
           "#c0c0c0", "#d080ff", "#80d0ff", "#a0ffa0", "#ffd0d0"]


def tick_decimals(span: float) -> int:
    """Decimal places that keep neighbouring axis labels distinct. A 0.3 V
    span at "%.1f" printed 0.2, 0.2, 0.1, 0.1, 0.0, -0.0 -- useless."""
    if not np.isfinite(span) or span <= 0:
        return 2
    return int(max(0, min(5, 2 - np.floor(np.log10(span)))))


def envelope(y: np.ndarray, columns: int) -> tuple[np.ndarray, np.ndarray]:
    """Per-pixel-column min/max of a 1-D signal: (mins, maxs), each of
    length ``columns`` (or len(y) when y is shorter than that)."""
    y = np.asarray(y)
    n = len(y)
    if n == 0:
        return np.zeros(0), np.zeros(0)
    if n <= columns:
        return y.copy(), y.copy()
    per = n // columns
    used = per * columns
    block = y[:used].reshape(columns, per)
    mins = block.min(axis=1)
    maxs = block.max(axis=1)
    if used < n:                     # fold the remainder into the last column
        tail = y[used:]
        mins[-1] = min(mins[-1], tail.min())
        maxs[-1] = max(maxs[-1], tail.max())
    return mins, maxs


class ScopeTraceWidget(QWidget):
    """The graph: black, dotted teal grid, Volts vs Time (s)."""

    LEFT, RIGHT, TOP, BOTTOM = 64, 12, 10, 34

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 260)
        self._snap: ScopeSnapshot | None = None
        self._t_end = 0.0
        self._enabled: set[int] = set(DEFAULT_ACTIVE)
        self._y_range = (-0.1, 1.3)
        self.setAutoFillBackground(False)

    def set_enabled(self, enabled) -> None:
        self._enabled = set(int(i) for i in enabled)
        self.update()

    def set_data(self, snap: ScopeSnapshot | None, t_end_s: float) -> None:
        self._snap = snap
        self._t_end = t_end_s
        self.update()

    def _compute_y_range(self, frames, cols) -> tuple[float, float]:
        if frames is None or len(frames) == 0 or not cols:
            return (-0.1, 1.3)
        sub = frames[:, cols].astype(np.float64) * AI_VOLTS_PER_COUNT
        lo, hi = float(sub.min()), float(sub.max())
        if hi - lo < 0.2:
            mid = (hi + lo) / 2
            lo, hi = mid - 0.1, mid + 0.1
        pad = 0.05 * (hi - lo)
        return (min(lo - pad, -0.1), max(hi + pad, 1.3) if hi > 1.0 else hi + pad)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor("#e8e8f8"))
        plot = QRectF(self.LEFT, self.TOP, max(1, w - self.LEFT - self.RIGHT), max(1, h - self.TOP - self.BOTTOM))
        p.fillRect(plot, QColor(0, 0, 0))

        snap = self._snap
        frames = snap.frames if snap is not None else None
        cols = [c for c in sorted(self._enabled) if frames is not None and c < frames.shape[1]]
        y0, y1 = self._compute_y_range(frames, cols)
        self._y_range = (y0, y1)
        n = 0 if frames is None else len(frames)
        span_s = n / snap.fs_hz if snap is not None and n else 1.0
        t_start = self._t_end - span_s

        # grid: 10 vertical x 14 horizontal divisions, dotted teal like LabVIEW
        grid_pen = QPen(QColor(0, 110, 110))
        grid_pen.setStyle(Qt.DotLine)
        p.setPen(grid_pen)
        for i in range(1, 10):
            x = plot.left() + plot.width() * i / 10
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
        for j in range(1, 14):
            y = plot.top() + plot.height() * j / 14
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

        # axes text
        p.setPen(QColor(20, 20, 20))
        font = QFont(); font.setPointSize(8); p.setFont(font)
        vdec = tick_decimals(y1 - y0)
        tdec = tick_decimals(span_s)
        for j in range(0, 15, 2):
            frac = j / 14
            v = y1 - (y1 - y0) * frac
            y = plot.top() + plot.height() * frac
            p.drawText(QRectF(0, y - 8, self.LEFT - 6, 16), Qt.AlignRight | Qt.AlignVCenter, f"{v:.{vdec}f}")
        for i in range(0, 11, 2):
            frac = i / 10
            x = plot.left() + plot.width() * frac
            p.drawText(QRectF(x - 40, plot.bottom() + 2, 80, 14), Qt.AlignHCenter | Qt.AlignTop,
                       f"{t_start + span_s * frac:.{tdec}f}")
        p.drawText(QRectF(plot.left(), h - 16, plot.width(), 14), Qt.AlignHCenter, "Time (s)")
        p.save()
        p.translate(12, plot.center().y())
        p.rotate(-90)
        p.drawText(QRectF(-40, -8, 80, 16), Qt.AlignCenter, "Volts")
        p.restore()

        if frames is None or n == 0 or not cols:
            return
        p.setClipRect(plot)
        columns = max(2, int(plot.width()))
        yscale = plot.height() / (y1 - y0) if y1 > y0 else 1.0

        def ypix(v):
            return plot.bottom() - (v - y0) * yscale

        for c in cols:
            col = frames[:, c].astype(np.float64) * AI_VOLTS_PER_COUNT
            mins, maxs = envelope(col, columns)
            m = len(mins)
            xs = plot.left() + np.arange(m) * (plot.width() / max(1, m - 1) if m > 1 else 0)
            pen = QPen(QColor(PALETTE[c % len(PALETTE)]))
            pen.setWidth(1)
            p.setPen(pen)
            if m <= 2:
                continue
            # envelope: draw a vertical span per column plus the connecting polyline of the maxima
            poly_max = QPolygonF([QPointF(float(x), float(ypix(v))) for x, v in zip(xs, maxs)])
            poly_min = QPolygonF([QPointF(float(x), float(ypix(v))) for x, v in zip(xs, mins)])
            p.drawPolyline(poly_max)
            p.drawPolyline(poly_min)
            # fill the gaps where min != max so pulses read as solid vertical bars
            diff = maxs - mins
            for k in np.flatnonzero(diff > 0):
                p.drawLine(QPointF(float(xs[k]), float(ypix(mins[k]))), QPointF(float(xs[k]), float(ypix(maxs[k]))))
        p.setClipping(False)


class FpgaScopePanel(QWidget):
    """The whole Waveforms tab: controls, graph, legend, status line."""

    REFRESH_MS = 100

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scope: FpgaScope | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        top = QHBoxLayout()
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setMinimumWidth(110)
        self.clear_btn.clicked.connect(self._on_clear)
        top.addWidget(self.clear_btn)
        top.addSpacing(16)
        top.addWidget(QLabel("# of seconds to buff"))
        self.window_spin = QDoubleSpinBox()
        self.window_spin.setRange(0.05, 5.0)
        self.window_spin.setDecimals(2)
        self.window_spin.setSingleStep(0.5)
        self.window_spin.setValue(2.0)
        top.addWidget(self.window_spin)
        top.addSpacing(16)
        self.points_label = QLabel("# points acq: 0")
        top.addWidget(self.points_label)
        top.addSpacing(16)
        self.stream_chk = QCheckBox("Scope streaming")
        self.stream_chk.setChecked(True)
        self.stream_chk.toggled.connect(self._on_stream_toggled)
        top.addWidget(self.stream_chk)
        top.addSpacing(16)
        # "Simulate on FPGA" normally clamps every AO output to 0, which also
        # zeroes the AO columns the scope samples (they are read after the
        # range check). A small test clamp lets waveform SHAPE be seen on
        # the scope with nothing connected to the AO BNCs. 0 = frozen.
        top.addWidget(QLabel("Scope test clamp"))
        self.test_clamp_spin = QDoubleSpinBox()
        self.test_clamp_spin.setRange(0.0, 500.0)
        self.test_clamp_spin.setDecimals(0)
        self.test_clamp_spin.setSingleStep(50.0)
        self.test_clamp_spin.setSuffix(" mV")
        self.test_clamp_spin.setValue(0.0)
        self.test_clamp_spin.setToolTip("Simulate-on-FPGA only: allow AO outputs up to +-this much so the waveform "
                                        "shows on the scope. Keep 0 unless nothing is connected to the AO BNCs.")
        top.addWidget(self.test_clamp_spin)
        top.addStretch(1)
        root.addLayout(top)

        body = QHBoxLayout()
        self.trace = ScopeTraceWidget()
        body.addWidget(self.trace, stretch=1)

        legend_box = QFrame()
        legend_box.setObjectName("scopeLegend")
        legend_box.setStyleSheet("QFrame#scopeLegend { border: 1px solid #a0a0b0; }")
        legend_lay = QVBoxLayout(legend_box)
        legend_lay.setContentsMargins(4, 4, 4, 4)
        legend_lay.setSpacing(1)
        self.channel_checks: dict[int, QCheckBox] = {}
        for c, name in enumerate(AI_CHANNEL_NAMES):
            row = QHBoxLayout()
            chk = QCheckBox(LEGEND_LABELS.get(c, name))
            chk.setChecked(c in DEFAULT_ACTIVE)
            chk.toggled.connect(self._on_channel_toggled)
            swatch = QLabel()
            swatch.setFixedSize(22, 12)
            swatch.setStyleSheet(f"background-color: {PALETTE[c % len(PALETTE)]}; border: 1px solid #404040;")
            row.addWidget(chk, stretch=1)
            row.addWidget(swatch)
            legend_lay.addLayout(row)
            self.channel_checks[c] = chk
        legend_lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(legend_box)
        scroll.setFixedWidth(215)
        body.addWidget(scroll)
        root.addLayout(body, stretch=1)

        self.status_label = QLabel("Scope: FPGA not connected")
        self.status_label.setStyleSheet("font-family: Consolas, monospace; font-size: 8pt;")
        root.addWidget(self.status_label)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._on_channel_toggled()

    # -- wiring -----------------------------------------------------------------
    def set_scope(self, scope: FpgaScope | None) -> None:
        self._scope = scope
        if scope is None:
            self._timer.stop()
            self.trace.set_data(None, 0.0)
            self.status_label.setText("Scope: FPGA not connected")
        else:
            self._timer.start(self.REFRESH_MS)
            self._refresh()

    def enabled_channels(self) -> list[int]:
        return [c for c, chk in self.channel_checks.items() if chk.isChecked()]

    def test_clamp_counts(self) -> int:
        """The simulate-on-FPGA AO clamp in DAC counts (0 = frozen)."""
        return int(round(self.test_clamp_spin.value() * 32767 / 10000.0))

    def _on_channel_toggled(self, *_):
        self.trace.set_enabled(self.enabled_channels())

    def _on_clear(self):
        if self._scope is not None:
            self._scope.clear()
        self.trace.set_data(None, 0.0)

    def _on_stream_toggled(self, on: bool):
        if self._scope is None:
            return
        try:
            if on and not self._scope.running:
                self._scope.start()
            elif not on and self._scope.running:
                self._scope.stop()
        except Exception as e:  # surface, never crash the GUI
            self.status_label.setText(f"Scope: {type(e).__name__}: {e}")

    # -- refresh ----------------------------------------------------------------
    def _refresh(self):
        scope = self._scope
        if scope is None:
            return
        snap = scope.snapshot(seconds=self.window_spin.value())
        t_end = scope.ring.total / scope.fs_hz
        self.trace.set_data(snap, t_end)
        self.points_label.setText(f"# points acq: {scope.ring.total:,}")
        parts = [f"{scope.fs_hz:,.0f} S/s x {scope.channels} ch", f"buffered {scope.seconds_buffered():.1f} s",
                 f"backlog max {scope.backlog_max}"]
        err = [k for k, v in scope.ai_error.items() if v]
        parts.append("AI err " + (",".join(err) if err else "-"))
        if scope.channels > IDX_DIO4 and len(snap.frames):
            st = measure_period(snap.frames[:, IDX_DIO4], scope.fs_hz)
            if st is not None:
                parts.append(f"DIO4: {st.edges} edges, period {st.period_ms:.4f} ms "
                             f"(sd {st.period_sd_ms:.4f}), high {st.high_ms:.3f} ms")
            else:
                parts.append("DIO4: no edges in window")
        if not scope.running:
            parts.append("STOPPED")
        if scope.last_error:
            parts.append(scope.last_error)
        self.status_label.setText("Scope: " + " | ".join(parts))
