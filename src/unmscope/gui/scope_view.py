"""Waveforms tab: the FPGA Scope trace view, driven like a real digital
oscilloscope rather than an auto-scaled plot.

The screen is a fixed **10 x 8 division** graticule. The timebase is a
**time/div** from a 1-2-5 sequence and the vertical gain a **volts/div**
from the same, so every gridline lands on a round value by construction
and the axis numbers stop churning -- the earlier design labelled ticks at
``span * i/10`` off a free-running clock, which printed things like 2.106,
2.166, 2.226 and changed ten times a second.

Mouse: wheel zooms the timebase about the pointer (Shift+wheel = volts/div),
double-click returns to live and re-fits. There is deliberately no drag-pan:
where the trace sits is decided by the trigger and by Fit, the way a scope
works, not by shoving the picture around and having to find it again. **Hold**
freezes the buffer so a captured window can be zoomed into to check
synchronisation between the trigger, the galvo sweep and the dither.

Data comes from :class:`unmscope.hardware.fpga_scope.FpgaScope`. Every
column is drawn in volts = counts x 10/32768, so the digital flags (0 /
4096 counts) show as 1.25 V steps exactly as they do in LabVIEW. Windows
longer than the screen is wide are decimated per pixel column with a
min/max envelope, so a 100 us trigger pulse stays visible in a 5 s window.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QPolygonF
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
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
                 16: "AOTF 0", 17: "AOTF 1", 18: "Perfusion", 19: "AOTF 2", 20: "AOTF 3", 21: "AOTF 4",
                 22: "AOTF 5", 23: "AOTF 6"}
PALETTE = ["#ffffff", "#ff4040", "#40ff40", "#4080ff", "#ffff40", "#ff40ff", "#40ffff", "#ff9020",
           "#a0a0a0", "#c060ff", "#60c0ff", "#80ff80", "#ffb0b0", "#b0ffb0", "#b0b0ff", "#ffe080",
           "#e0e0e0", "#ff8080", "#80ff80", "#8080ff", "#ffff80", "#ff80ff", "#80ffff", "#ffc080",
           "#c0c0c0", "#d080ff", "#80d0ff", "#a0ffa0", "#ffd0d0"]

#: The graticule. 10 x 8 is the digital-scope standard.
HDIV, VDIV = 10, 8

#: Timebase and vertical gain, 1-2-5 per decade. Every gridline is a round
#: number because the step is, which is the whole point of the rework.
TIME_PER_DIV = [10e-6, 20e-6, 50e-6, 100e-6, 200e-6, 500e-6,
                1e-3, 2e-3, 5e-3, 10e-3, 20e-3, 50e-3,
                0.1, 0.2, 0.5]
VOLTS_PER_DIV = [0.001, 0.002, 0.005, 0.01, 0.02, 0.05,
                 0.1, 0.2, 0.5, 1.0, 2.0, 5.0]

SCREEN_BG = QColor(0, 0, 0)
GRID_DOT = QColor(0, 110, 110)
GRID_AXIS = QColor(0, 165, 165)
READOUT = QColor(220, 220, 120)


def eng_time(s: float) -> str:
    """A timebase the way a scope prints it: 50 us, 20 ms, 0.2 s."""
    if s < 1e-3:
        return f"{s * 1e6:g} µs"
    if s < 1.0:
        return f"{s * 1e3:g} ms"
    return f"{s:g} s"


def eng_volts(v: float) -> str:
    return f"{v * 1e3:g} mV" if abs(v) < 1.0 else f"{v:g} V"


def time_axis_label(t_s: float, per_div: float) -> str:
    """A tick label in the unit the timebase implies, no wasted decimals.

    Ticks are measured from the CENTRE of the screen, so they only ever span
    +-5 divisions and stay short at every zoom. Referencing them to "now"
    instead produced things like -165900 us once the view was panned back.
    """
    val = t_s * 1e6 if per_div < 1e-3 else (t_s * 1e3 if per_div < 0.1 else t_s)
    if abs(val) < 1e-9:
        return "0"
    return f"{val:+.{0 if abs(val) >= 1 else 1}f}"


def time_axis_unit(per_div: float) -> str:
    """Must track time_axis_label's own branches exactly."""
    if per_div < 1e-3:
        return "µs"
    return "ms" if per_div < 0.1 else "s"


def tick_decimals(span: float) -> int:
    """Decimal places that keep neighbouring axis labels distinct. A 0.3 V
    span at "%.1f" printed 0.2, 0.2, 0.1, 0.1, 0.0, -0.0 -- useless."""
    if not np.isfinite(span) or span <= 0:
        return 2
    return int(max(0, min(5, 2 - np.floor(np.log10(span)))))


def envelope(y: np.ndarray, columns: int) -> tuple[np.ndarray, np.ndarray]:
    """Per-pixel-column min/max of a 1-D signal: (mins, maxs), each of
    length ``columns`` (or len(y) when y is shorter than that).

    Bin edges come from ``linspace`` so every column covers an equal slice of
    TIME. The previous version used ``per = n // columns`` and folded the
    leftover samples into the last column, which squeezed up to ``columns-1``
    extra samples into one pixel and stretched everything before it: with
    2000 samples in 788 columns a feature at the middle of the window drew at
    63% across, a 13% time error. Harmless while the axis was decorative,
    wrong once the graticule is meant to be measured against.
    """
    y = np.asarray(y)
    n = len(y)
    if n == 0:
        return np.zeros(0), np.zeros(0)
    if n <= columns:
        return y.copy(), y.copy()
    starts = np.linspace(0, n, columns + 1)[:-1].astype(np.int64)
    return np.minimum.reduceat(y, starts), np.maximum.reduceat(y, starts)


class ScopeTraceWidget(QWidget):
    """The scope screen: 10 x 8 graticule, time/div, volts/div, zoom."""

    #: Emitted when the wheel or a double-click changes time/div or volts/div,
    #: so the panel's combos can follow.
    view_changed = Signal()

    LEFT, RIGHT, TOP, BOTTOM = 58, 14, 12, 34

    #: How near the pointer must be to a trace, in pixels, to pick it.
    HOVER_SLOP = 6

    #: In trigger mode, how far back to look for an edge (the panel caps it
    #: at the buffer length). Big on purpose: see seconds_needed.
    TRIGGER_LOOKBACK_S = 60.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 260)
        self._snap: ScopeSnapshot | None = None
        self._enabled: set[int] = set(DEFAULT_ACTIVE)
        self._ti = TIME_PER_DIV.index(20e-3)      # 20 ms/div -> 200 ms screen
        self._gain: dict[int, float] = {}         # volts per division, per channel
        self._offset: dict[int, float] = {}       # divisions from centre, per channel
        self._tags: list[dict] = []               # pinned tags, see place_tag
        self._tag_hits: list[tuple] = []          # button rects, laid out during paint
        self._tag_boxes: list[tuple] = []         # box rects, for dragging a tag aside
        self._tag_drag: tuple | None = None
        #: True = each channel keeps its own volts/div (the default: it is
        #: what per-channel gain was added for). False ganges them together so
        #: one adjustment moves the lot and the axis can stay in Volts.
        #: Either way the axis only leaves Volts once gains actually differ.
        self._per_channel_scale = True
        self._delay_s = 0.0        # how far the right edge sits behind "now"
        self._trigger_col: int | None = None   # None = free run; else align on this column
        self._trig_info = ""
        self._held_trig: ScopeSnapshot | None = None   # last snapshot that had a usable edge
        self._source: ScopeSnapshot | None = None      # what _visible_segment slices this paint
        self._drawn: dict[int, tuple] = {}     # per-channel geometry, for hit-testing
        self._hover: int | None = None
        self._hover_pos = None
        self._placed_by_press: int | None = None
        self._held = False
        self.setMouseTracking(True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.WheelFocus)

    # -- view model ---------------------------------------------------------
    @property
    def time_per_div(self) -> float:
        return TIME_PER_DIV[self._ti]

    @property
    def view_seconds(self) -> float:
        return HDIV * self.time_per_div

    @property
    def delay_seconds(self) -> float:
        return self._delay_s

    def seconds_needed(self) -> float:
        """What the panel must pull from the ring to fill this view.

        Trigger alignment needs history beyond the screen to find an edge at
        all -- at 200 us/div the screen holds 2 ms and the triggers are 100 ms
        apart, so asking only for the screen would never find one.
        """
        want = self.view_seconds + self._delay_s
        if self._trigger_col is None:
            return want
        # Trigger mode: ask for every second the panel will give (it caps this
        # at "# of seconds to buff"). A Z stack is a burst of a few hundred ms
        # every few seconds; looking back only 3x the screen missed the burst
        # between passes and dropped the view to a flat live line.
        return max(want * 3, want + 0.25, self.TRIGGER_LOOKBACK_S)

    def set_time_per_div(self, value: float) -> None:
        self._ti = min(range(len(TIME_PER_DIV)), key=lambda i: abs(TIME_PER_DIV[i] - value))
        self.update()

    def set_trigger_column(self, col: int | None) -> None:
        """Align the screen centre on this column's rising edges (None = free).

        Free-running, the trace slides every refresh and nothing can be
        compared between frames; aligned, the sweep and the dither sit still
        against the trigger, which is the whole point of looking at them.
        """
        if col is None:
            # Anything banked while the trigger owned the position would now
            # take effect all at once, landing the user on a blank screen.
            self._delay_s = 0.0
        self._held_trig = None
        self._trigger_col = col
        self.update()

    def set_enabled(self, enabled) -> None:
        # Sampled BEFORE the new set is applied: afterwards the newly ticked
        # channel is already in it on the default gain, so uniform_scale()
        # would see the mix it is supposed to prevent and do nothing.
        uniform = None if self._per_channel_scale else self.uniform_scale()
        self._enabled = set(int(i) for i in enabled)
        if uniform is not None:
            # In shared mode a newly ticked channel arriving on the DEFAULT
            # gain would break the invariant and flip the axis to Divisions.
            gain, offset = uniform
            for c in self._enabled:
                self._gain[c] = gain
                self._offset[c] = offset
        self.update()

    def set_held(self, on: bool) -> None:
        self._held = bool(on)
        self.update()

    def set_data(self, snap: ScopeSnapshot | None) -> None:
        self._snap = snap
        if snap is None:
            self._held_trig = None
        self.update()

    # -- per-channel vertical scale ------------------------------------------
    #
    # Each channel maps volts to the graticule with its OWN gain and offset:
    #
    #     divisions_from_centre = volts / gain(c) + offset(c)
    #
    # A single shared scale cannot serve this instrument -- the digital flags
    # swing 1.25 V while the X galvo is +-0.125 V -- so any gain that shows one
    # squashes the other, and two waveforms of unequal amplitude cannot be
    # compared shape-for-shape. The cost is that a Volts axis would then be
    # lying about every channel it does not belong to, so when gains differ the
    # axis switches to DIVISIONS and each tag states its own volts/div.
    DEFAULT_GAIN = 0.2
    #: 0 V sits 2.5 divisions low by default, which puts -0.3 .. +1.3 V on
    #: screen at 200 mV/div: the galvos and the 1.25 V flags together.
    DEFAULT_OFFSET = -2.5

    def gain(self, c: int) -> float:
        return self._gain.get(c, self.DEFAULT_GAIN)

    def offset(self, c: int) -> float:
        return self._offset.get(c, self.DEFAULT_OFFSET)

    def set_gain(self, c: int, value: float) -> None:
        self._gain[c] = min(VOLTS_PER_DIV, key=lambda v: abs(v - value))
        self.update()

    def set_offset(self, c: int, divisions: float) -> None:
        self._offset[c] = float(divisions)
        self.update()

    @property
    def per_channel_scale(self) -> bool:
        return self._per_channel_scale

    def set_per_channel_scale(self, on: bool) -> None:
        """Switch between one shared volts/div and per-channel gains.

        Leaving custom mode collapses every visible channel onto the COARSEST
        gain in play, so nothing that was on screen falls off it, and restores
        the default vertical position. The axis can then go back to volts.
        """
        on = bool(on)
        if on == self._per_channel_scale:
            return
        self._per_channel_scale = on
        if not on:
            cols = self._visible_columns()
            if cols:
                shared = max(self.gain(c) for c in cols)
                for c in cols:
                    self._gain[c] = shared
                    self._offset[c] = self.DEFAULT_OFFSET
        self.update()
        self.view_changed.emit()

    def _scale_targets(self, c: int) -> list[int]:
        """Which channels a gain change applies to, honouring the mode."""
        return [c] if self._per_channel_scale else (self._visible_columns() or [c])

    def step_gain(self, c: int, steps: int, from_tag: bool = False) -> None:
        """Move volts/div by whole 1-2-5 steps (the tag buttons).

        A tag's buttons are a per-waveform control, so pressing one while the
        scales are ganged switches to independent scales rather than silently
        moving every other trace as well.
        """
        if from_tag and not self._per_channel_scale:
            self._per_channel_scale = True
        for t in self._scale_targets(c):
            i = VOLTS_PER_DIV.index(self.gain(t))
            self._gain[t] = VOLTS_PER_DIV[int(np.clip(i + steps, 0, len(VOLTS_PER_DIV) - 1))]
        self.update()
        self.view_changed.emit()

    def uniform_scale(self) -> tuple[float, float] | None:
        """(gain, offset) when every visible channel shares them, else None.

        This is what decides whether the vertical axis can be labelled in
        volts at all.
        """
        cols = self._visible_columns()
        if not cols:
            return (self.DEFAULT_GAIN, self.DEFAULT_OFFSET)
        gains = {self.gain(c) for c in cols}
        offsets = {self.offset(c) for c in cols}
        if len(gains) == 1 and len(offsets) == 1:
            return (gains.pop(), offsets.pop())
        return None

    @property
    def volts_per_div(self) -> float:
        """The common gain if there is one, else the default -- what the
        toolbar combo displays."""
        uniform = self.uniform_scale()
        return uniform[0] if uniform else self.DEFAULT_GAIN

    def set_volts_per_div(self, value: float) -> None:
        """Set every visible channel's gain: the toolbar combo is a 'set all'.
        Per-channel adjustment lives on the tags."""
        for c in self._visible_columns() or [None]:
            if c is not None:
                self._gain[c] = min(VOLTS_PER_DIV, key=lambda v: abs(v - value))
        self.update()

    def fit_each_channel(self) -> None:
        """Give every visible channel its own gain so they all fill the screen.

        This is the answer to comparing waveforms of unequal amplitude: after
        it, shape is comparable directly and each tag says what scale it is on.
        """
        seg = self._visible_segment()
        cols = self._visible_columns()
        if seg is None or len(seg) == 0 or not cols:
            return
        if not self._per_channel_scale:
            # Shared mode: one gain that fits every visible channel at once.
            sub = seg[:, cols].astype(np.float64) * AI_VOLTS_PER_COUNT
            lo, hi = float(sub.min()), float(sub.max())
            if not (np.isfinite(lo) and np.isfinite(hi)):
                return
            need = max((hi - lo) / (VDIV - 2), 1e-4)
            shared = next((v for v in VOLTS_PER_DIV if v >= need), VOLTS_PER_DIV[-1])
            for c in cols:
                self._gain[c] = shared
                self._offset[c] = -((hi + lo) / 2) / shared
            self.update()
            self.view_changed.emit()
            return
        for c in cols:
            col = seg[:, c].astype(np.float64) * AI_VOLTS_PER_COUNT
            lo, hi = float(col.min()), float(col.max())
            if not (np.isfinite(lo) and np.isfinite(hi)):
                continue
            need = max((hi - lo) / (VDIV - 2), 1e-4)
            self._gain[c] = next((v for v in VOLTS_PER_DIV if v >= need), VOLTS_PER_DIV[-1])
            self._offset[c] = -((hi + lo) / 2) / self._gain[c]     # centre it
        self.update()
        self.view_changed.emit()

    def _px_per_div(self, plot: QRectF) -> float:
        return plot.height() / VDIV

    def _volts_to_y(self, c: int, v, plot: QRectF):
        div = np.asarray(v) / self.gain(c) + self.offset(c)
        return plot.center().y() - div * self._px_per_div(plot)

    def _y_to_div(self, y: float, plot: QRectF) -> float:
        return (plot.center().y() - y) / self._px_per_div(plot)

    def _y_to_volts(self, c: int, y: float, plot: QRectF) -> float:
        return (self._y_to_div(y, plot) - self.offset(c)) * self.gain(c)

    # -- pinned tags ---------------------------------------------------------
    #
    # Click a trace to pin a tag to it. The tag stays put, names the channel,
    # states that channel's OWN volts/div, and carries the buttons that change
    # it -- so two waveforms of very different amplitude can be scaled to
    # compare shape-for-shape, with the scale of each one written next to it.
    #
    # The anchor is stored as a TIME against the screen reference (the trigger
    # when aligned), not as a pixel, so a tag survives zooming and rolling and
    # stays on the feature it was put on.

    TAG_GAP = 16.0

    def place_tag(self, c: int, t_from_centre: float) -> None:
        self._tags.append({"c": int(c), "t": float(t_from_centre)})
        self.update()

    def remove_tag(self, index: int) -> None:
        if 0 <= index < len(self._tags):
            del self._tags[index]
            self.update()

    def clear_tags(self) -> None:
        if self._tags:
            self._tags.clear()
            self.update()

    @property
    def tags(self) -> list[dict]:
        return self._tags

    def _tag_anchor(self, tag: dict, plot: QRectF):
        """(x, y) of the tagged point, or None when its channel is not drawn."""
        entry = self._drawn.get(tag["c"])
        if entry is None:
            return None
        xs, ytop, ybot, _vmin, _vmax = entry
        x = plot.center().x() + tag["t"] / self.view_seconds * plot.width()
        if not (plot.left() <= x <= plot.right()) or len(xs) < 2 or xs[-1] <= xs[0]:
            return None
        k = int(np.clip(round((x - xs[0]) / (xs[-1] - xs[0]) * (len(xs) - 1)), 0, len(xs) - 1))
        return (float(x), float((ytop[k] + ybot[k]) / 2))

    def _draw_tags(self, p: QPainter, plot: QRectF):
        self._tag_hits = []
        self._tag_boxes = []
        if not self._tags:
            return
        name_font = QFont()
        name_font.setPointSize(7)
        name_font.setBold(True)
        p.setFont(name_font)
        fm = p.fontMetrics()
        row = fm.height()
        p.setClipRect(plot)
        for i, tag in enumerate(self._tags):
            anchor = self._tag_anchor(tag, plot)
            if anchor is None:
                continue
            ax, ay = anchor
            c = tag["c"]
            colour = QColor(PALETTE[c % len(PALETTE)])
            name = LEGEND_LABELS.get(c, AI_CHANNEL_NAMES[c] if c < len(AI_CHANNEL_NAMES) else str(c))
            scale = f"{eng_volts(self.gain(c))}/div"
            btn = row - 2
            w = max(fm.horizontalAdvance(name) + btn + 14,
                    fm.horizontalAdvance(scale) + 2 * btn + 18)
            h = 2 * row + 6
            x = ax + self.TAG_GAP
            if x + w > plot.right() - 2:
                x = ax - self.TAG_GAP - w
            y = ay - h - self.TAG_GAP * 0.5
            if y < plot.top() + 2:
                y = ay + self.TAG_GAP * 0.5
            # Whatever the user has dragged this tag aside by. The leader line
            # keeps it attached, so a box moved out of the way still says which
            # waveform it belongs to.
            x += tag.get("dx", 0.0)
            y += tag.get("dy", 0.0)
            x = float(np.clip(x, plot.left() + 2, max(plot.left() + 2, plot.right() - w - 2)))
            y = float(np.clip(y, plot.top() + 2, max(plot.top() + 2, plot.bottom() - h - 2)))
            box = QRectF(x, y, w, h)
            self._tag_boxes.append((i, box))

            p.setPen(QPen(colour, 1, Qt.DotLine))
            p.drawLine(QPointF(ax, ay), QPointF(box.center().x(), box.center().y()))
            p.setPen(QPen(colour))
            p.setBrush(colour)
            p.drawEllipse(QPointF(ax, ay), 3.0, 3.0)
            p.setBrush(QColor(0, 0, 0, 225))
            p.drawRect(box)
            p.setBrush(Qt.NoBrush)

            close = QRectF(box.right() - btn - 4, box.top() + 3, btn, btn)
            minus = QRectF(box.right() - 2 * btn - 8, box.top() + row + 3, btn, btn)
            plus = QRectF(box.right() - btn - 4, box.top() + row + 3, btn, btn)
            self._tag_hits.append((i, "close", close))
            self._tag_hits.append((i, "minus", minus))
            self._tag_hits.append((i, "plus", plus))

            p.setPen(colour)
            p.drawText(QRectF(box.left() + 5, box.top() + 3, w - btn - 12, row),
                       Qt.AlignLeft | Qt.AlignVCenter, name)
            p.setPen(READOUT)
            p.drawText(QRectF(box.left() + 5, box.top() + row + 3, w - 2 * btn - 16, row),
                       Qt.AlignLeft | Qt.AlignVCenter, scale)
            for rect, glyph in ((close, "x"), (minus, "-"), (plus, "+")):
                p.setPen(QPen(QColor(150, 150, 150)))
                p.drawRect(rect)
                p.setPen(QPen(QColor(235, 235, 235)))
                p.drawText(rect, Qt.AlignCenter, glyph)
        p.setClipping(False)

    def tag_box_at(self, pos):
        """Index of the tag whose box is under this point, or None."""
        for i, box in self._tag_boxes:
            if box.contains(pos):
                return i
        return None

    def tag_button_at(self, pos):
        """(tag index, button) under this point, or None. Buttons are laid out
        during paint, so this reflects exactly what is on screen."""
        for i, kind, rect in self._tag_hits:
            if rect.adjusted(-2, -2, 2, 2).contains(pos):
                return (i, kind)
        return None

    def mousePressEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            return
        hit = self.tag_button_at(ev.position())
        if hit is not None:
            i, kind = hit
            if kind == "close":
                self.remove_tag(i)
            elif i < len(self._tags):
                # '+' means a BIGGER waveform, i.e. fewer volts per division.
                self.step_gain(self._tags[i]["c"], -1 if kind == "plus" else +1, from_tag=True)
            ev.accept()
            return
        i = self.tag_box_at(ev.position())
        if i is not None:
            tag = self._tags[i]
            self._tag_drag = (i, ev.position().x(), ev.position().y(),
                              tag.get("dx", 0.0), tag.get("dy", 0.0))
            ev.accept()
            return
        c = self.hover_hit(ev.position())
        if c is not None:
            plot = self._plot_rect()
            t = (ev.position().x() - plot.center().x()) / plot.width() * self.view_seconds
            self.place_tag(c, t)
            self._placed_by_press = len(self._tags) - 1
            ev.accept()

    def mouseReleaseEvent(self, ev):
        self._tag_drag = None

    # -- geometry -----------------------------------------------------------
    def _plot_rect(self) -> QRectF:
        return QRectF(self.LEFT, self.TOP,
                      max(1, self.width() - self.LEFT - self.RIGHT),
                      max(1, self.height() - self.TOP - self.BOTTOM))

    def _visible_columns(self) -> list[int]:
        frames = self._snap.frames if self._snap is not None else None
        if frames is None:
            return []
        return [c for c in sorted(self._enabled) if c < frames.shape[1]]

    def _edge_delay(self, snap: ScopeSnapshot):
        """(delay, reason) for aligning the centre on this snapshot's newest
        usable rising edge; delay is None when there is none."""
        if self._trigger_col >= snap.frames.shape[1]:
            return None, "trigger column not streamed"
        col = snap.frames[:, self._trigger_col].astype(np.float64) * AI_VOLTS_PER_COUNT
        hot = col > 0.6                                   # the 1.25 V digital flags
        rises = np.flatnonzero(hot[1:] & ~hot[:-1]) + 1
        if len(rises) == 0:
            return None, "no trigger"
        n = len(col)
        half = int(round(self.view_seconds * snap.fs_hz / 2))
        usable = rises[rises + half <= n]
        if len(usable) == 0:
            return None, "trigger too recent"
        idx = int(usable[-1])
        return max(0.0, (n - (idx + half)) / snap.fs_hz), "trig'd"

    def _effective_delay(self) -> float:
        """Where the right edge sits behind 'now'. In trigger mode this is
        derived from the newest usable edge instead of the pan position.

        Also decides which snapshot this paint slices (_source). When the
        fresh snapshot has no usable edge, the last one that did is held on
        screen -- a scope's Normal trigger. Without that, a Z stack (a burst
        every few seconds) vanished between passes into a flat live line.
        """
        snap = self._snap
        self._source = snap
        self._trig_info = ""
        if self._trigger_col is None or snap is None or len(snap.frames) == 0:
            return self._delay_s
        delay, reason = self._edge_delay(snap)
        if delay is not None:
            self._held_trig = snap
            self._trig_info = reason
            return delay
        held = self._held_trig
        if held is not None:
            held_delay, held_reason = self._edge_delay(held)
            if held_delay is not None:
                self._source = held
                self._trig_info = "trig'd (held)"
                return held_delay
        self._trig_info = reason
        return self._delay_s

    def _visible_segment(self, delay: float | None = None) -> np.ndarray | None:
        """The slice of the snapshot the current window covers. The snapshot's
        last sample is 'now'; the window ends ``delay`` before that."""
        if delay is None:
            delay = self._effective_delay()
        snap = self._source if self._source is not None else self._snap
        if snap is None or len(snap.frames) == 0:
            return None
        n = len(snap.frames)
        fs = snap.fs_hz
        end = n - int(round(delay * fs))
        start = end - int(round(self.view_seconds * fs))
        start, end = max(0, start), min(n, end)
        return snap.frames[start:end] if end > start else None

    def _t_to_x(self, t_before_right: float, plot: QRectF) -> float:
        """t measured backwards from the right edge -> pixel x."""
        return plot.right() - (t_before_right / self.view_seconds) * plot.width()

    def _x_to_t(self, x: float, plot: QRectF) -> float:
        return (plot.right() - x) / plot.width() * self.view_seconds

    # -- interaction --------------------------------------------------------
    def wheelEvent(self, ev):
        steps = ev.angleDelta().y() / 120.0
        if steps == 0:
            return
        if ev.modifiers() & Qt.ShiftModifier:
            # Scale the channel under the pointer if there is one, else every
            # visible channel. Anchor on the voltage under the pointer so the
            # feature being examined stays put: zooming about a fixed centre
            # walked away from signals that all sit near 0 V and went blank.
            plot = self._plot_rect()
            hovered = self.hover_hit(ev.position(), plot)
            targets = (self._scale_targets(hovered) if hovered is not None
                       else (self._visible_columns() or []))
            d = self._y_to_div(ev.position().y(), plot)
            for c in targets:
                anchor_v = (d - self.offset(c)) * self.gain(c)
                i = VOLTS_PER_DIV.index(self.gain(c))
                self._gain[c] = VOLTS_PER_DIV[int(np.clip(i - np.sign(steps), 0, len(VOLTS_PER_DIV) - 1))]
                self._offset[c] = d - anchor_v / self._gain[c]
        else:
            plot = self._plot_rect()
            # Zoom about the pointer so the feature under it stays put --
            # that is what makes hunting for a sync edge workable.
            anchor = self._x_to_t(ev.position().x(), plot) + self._delay_s
            self._ti = int(np.clip(self._ti - np.sign(steps), 0, len(TIME_PER_DIV) - 1))
            if self._trigger_col is None:
                frac = (plot.right() - ev.position().x()) / max(1.0, plot.width())
                self._delay_s = max(0.0, anchor - frac * self.view_seconds)
        self.update()
        self.view_changed.emit()
        ev.accept()

    def mouseMoveEvent(self, ev):
        if self._tag_drag is not None:
            i, x0, y0, dx0, dy0 = self._tag_drag
            if i < len(self._tags):
                self._tags[i]["dx"] = dx0 + (ev.position().x() - x0)
                self._tags[i]["dy"] = dy0 + (ev.position().y() - y0)
                self.update()
            return
        previous = self._hover
        self._hover = self.hover_hit(ev.position())
        self._hover_pos = ev.position()
        if self._hover is not None or previous is not None:
            self.update()

    def leaveEvent(self, ev):
        if self._hover is not None:
            self._hover = None
            self.update()

    def mouseDoubleClickEvent(self, ev):
        # The press half of this double-click already pinned a tag; drop it,
        # or every double-click-to-fit litters the screen.
        if self._placed_by_press is not None and self._placed_by_press == len(self._tags) - 1:
            self.remove_tag(self._placed_by_press)
        self._placed_by_press = None
        self._delay_s = 0.0
        self.fit_each_channel()
        self.view_changed.emit()
        ev.accept()

    # -- painting -----------------------------------------------------------
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor("#e8e8f8"))
        plot = self._plot_rect()
        p.fillRect(plot, SCREEN_BG)

        # Once per paint, before anything reads _trig_info: the axis title
        # names the reference and would otherwise show the previous frame's.
        delay = self._effective_delay()
        self._draw_graticule(p, plot)
        self._draw_axis_labels(p, plot)
        self._draw_traces(p, plot, delay)
        self._draw_tags(p, plot)
        self._draw_hover_callout(p, plot)
        self._draw_readout(p, plot, delay)

    def _draw_graticule(self, p: QPainter, plot: QRectF):
        dot = QPen(GRID_DOT)
        dot.setStyle(Qt.DotLine)
        for i in range(1, HDIV):
            p.setPen(dot)
            x = plot.left() + plot.width() * i / HDIV
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
        for j in range(1, VDIV):
            p.setPen(dot)
            y = plot.top() + plot.height() * j / VDIV
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        # Centre cross-hairs with 1/5-division ticks, as on a scope screen:
        # they are the fine ruler you actually count against when measuring.
        axis = QPen(GRID_AXIS)
        p.setPen(axis)
        cx = plot.left() + plot.width() / 2
        cy = plot.top() + plot.height() / 2
        p.drawLine(QPointF(cx, plot.top()), QPointF(cx, plot.bottom()))
        p.drawLine(QPointF(plot.left(), cy), QPointF(plot.right(), cy))
        for i in range(HDIV * 5 + 1):
            x = plot.left() + plot.width() * i / (HDIV * 5)
            p.drawLine(QPointF(x, cy - 3), QPointF(x, cy + 3))
        for j in range(VDIV * 5 + 1):
            y = plot.top() + plot.height() * j / (VDIV * 5)
            p.drawLine(QPointF(cx - 3, y), QPointF(cx + 3, y))
        p.setPen(QPen(QColor(0, 140, 140)))
        p.drawRect(plot)

    def _draw_axis_labels(self, p: QPainter, plot: QRectF):
        p.setPen(QColor(20, 20, 20))
        font = QFont()
        font.setPointSize(8)
        p.setFont(font)
        per_div = self.time_per_div
        # Volts numbers ONLY while every visible channel shares one scale.
        # Once gains differ there is no single voltage per pixel row, so the
        # axis switches to divisions and each tag states its own volts/div --
        # labelling it in volts anyway would be a lie about every other trace.
        uniform = self.uniform_scale()
        vdec = tick_decimals(uniform[0] * VDIV) if uniform else 0
        for j in range(0, VDIV + 1, 2):
            div = VDIV / 2 - j
            y = plot.top() + plot.height() * j / VDIV
            text = f"{(div - uniform[1]) * uniform[0]:.{vdec}f}" if uniform else f"{div:+.0f}"
            p.drawText(QRectF(0, y - 8, self.LEFT - 6, 16),
                       Qt.AlignRight | Qt.AlignVCenter, text)
        # Time is measured backwards from the right edge, which is 'now' (or
        # 'now - delay'). Because the step is a round time/div, every label is
        # a round number and none of them move while the trace rolls.
        # Odd divisions so the centre (0) is always one of the five labels.
        for i in range(1, HDIV, 2):
            x = plot.left() + plot.width() * i / HDIV
            p.drawText(QRectF(x - 44, plot.bottom() + 3, 88, 14), Qt.AlignHCenter | Qt.AlignTop,
                       time_axis_label((i - HDIV / 2) * per_div, per_div))
        ref = ("trigger" if self._trigger_col is not None and self._trig_info.startswith("trig'd")
               else "screen centre")
        p.drawText(QRectF(plot.left(), plot.bottom() + 18, plot.width(), 14),
                   Qt.AlignHCenter, f"Time from {ref} ({time_axis_unit(per_div)})")
        p.save()
        p.translate(12, plot.center().y())
        p.rotate(-90)
        p.drawText(QRectF(-60, -8, 120, 16), Qt.AlignCenter,
                   "Volts" if self.uniform_scale() else "Divisions")
        p.restore()

    def _draw_traces(self, p: QPainter, plot: QRectF, delay: float):
        # Cleared BEFORE the early return: hover, the value callout and the
        # tags all read this cache, and over a blank screen they were still
        # reporting voltages from the last frame that had data.
        self._drawn = {}
        seg = self._visible_segment(delay)
        cols = self._visible_columns()
        if seg is None or len(seg) == 0 or not cols:
            return
        p.setClipRect(plot)
        columns = max(2, int(plot.width()))
        # The segment may be shorter than the window (the ring does not reach
        # back far enough yet). Anchor it at the right edge and leave the rest
        # of the screen blank rather than stretching it to fit.
        have_s = len(seg) / (self._source or self._snap).fs_hz
        x_left = self._t_to_x(have_s, plot)

        for c in cols:
            col = seg[:, c].astype(np.float64) * AI_VOLTS_PER_COUNT
            mins, maxs = envelope(col, columns)
            m = len(mins)
            if m <= 2:
                continue
            width_px = (plot.right() - x_left)
            # m samples span m intervals: x_left came from len(seg)/fs, i.e.
            # the right edge is the instant AFTER the newest sample. Dividing
            # by m-1 instead pinned the last sample ON the right edge and
            # stretched the whole trace by m/(m-1) -- 0.56 of a division at
            # 10 us/div, which threw the trigger edge off the centre it is
            # pinned to. Same class of defect envelope() was rewritten to kill.
            xs = x_left + np.arange(m) * (width_px / max(1, m))
            # Keep exactly what was drawn: hover hit-tests this, so the pick
            # region always matches the pixels being pointed at.
            self._drawn[c] = (xs, self._volts_to_y(c, maxs, plot),
                              self._volts_to_y(c, mins, plot), mins, maxs)
            pen = QPen(QColor(PALETTE[c % len(PALETTE)]))
            pen.setWidth(2 if c == self._hover else 1)
            p.setPen(pen)
            ytop, ybot = self._drawn[c][1], self._drawn[c][2]
            p.drawPolyline(QPolygonF([QPointF(float(x), float(y)) for x, y in zip(xs, ytop)]))
            p.drawPolyline(QPolygonF([QPointF(float(x), float(y)) for x, y in zip(xs, ybot)]))
            for k in np.flatnonzero(maxs - mins > 0):
                p.drawLine(QPointF(float(xs[k]), float(ytop[k])), QPointF(float(xs[k]), float(ybot[k])))
        p.setClipping(False)

    def hover_hit(self, pos, plot: QRectF | None = None) -> int | None:
        """Which channel's trace is under this point, or None.

        Hit-tests the ENVELOPE that was actually drawn (the min..max band per
        pixel column), not a resampled signal, so a 100 us pulse rendered as a
        one-pixel vertical bar is still pickable.
        """
        if not self._drawn:
            return None
        plot = self._plot_rect() if plot is None else plot
        x, y = float(pos.x()), float(pos.y())
        if not (plot.left() <= x <= plot.right() and plot.top() <= y <= plot.bottom()):
            return None
        best, best_d = None, float(self.HOVER_SLOP)
        for c, (xs, ytop, ybot, _vmin, _vmax) in self._drawn.items():
            if len(xs) < 2 or xs[-1] <= xs[0]:
                continue
            k = int(np.clip(round((x - xs[0]) / (xs[-1] - xs[0]) * (len(xs) - 1)), 0, len(xs) - 1))
            if abs(xs[k] - x) > self.HOVER_SLOP:
                continue
            d = 0.0 if ytop[k] <= y <= ybot[k] else min(abs(y - ytop[k]), abs(y - ybot[k]))
            if d < best_d:
                best, best_d = c, d
        return best

    def hover_text(self, c: int, pos, plot: QRectF | None = None) -> list[str]:
        """The callout lines for channel ``c`` at this point: name, its own
        volts/div, the value there, and the time against the reference."""
        plot = self._plot_rect() if plot is None else plot
        name = LEGEND_LABELS.get(c, AI_CHANNEL_NAMES[c] if c < len(AI_CHANNEL_NAMES) else str(c))
        lines = [name, f"{eng_volts(self.gain(c))}/div"]
        entry = self._drawn.get(c)
        if entry is not None:
            xs, _ytop, _ybot, vmin, vmax = entry
            if len(xs) >= 2 and xs[-1] > xs[0]:
                k = int(np.clip(round((float(pos.x()) - xs[0]) / (xs[-1] - xs[0]) * (len(xs) - 1)),
                                0, len(xs) - 1))
                lo, hi = float(vmin[k]), float(vmax[k])
                # A decimated column holds a RANGE, not one sample. Printing a
                # single number there would invent precision the pixel has not
                # got: a 100 us pulse inside a 50 ms column spans 0 to 1.25 V.
                lines.append(f"{lo:+.3f} V" if abs(hi - lo) < 1e-4 else f"{lo:+.3f} .. {hi:+.3f} V")
        per_div = self.time_per_div
        t = (float(pos.x()) - plot.center().x()) / plot.width() * self.view_seconds
        lines.append(f"t {time_axis_label(t, per_div)} {time_axis_unit(per_div)}")
        return lines

    def _draw_hover_callout(self, p: QPainter, plot: QRectF):
        c, pos = self._hover, self._hover_pos
        if c is None or pos is None:
            return
        colour = QColor(PALETTE[c % len(PALETTE)])
        font = QFont()
        font.setPointSize(8)
        p.setFont(font)
        fm = p.fontMetrics()
        lines = self.hover_text(c, pos, plot)
        w = max(fm.horizontalAdvance(t) for t in lines) + 12
        h = fm.height() * len(lines) + 8
        # Flip the box to whichever side keeps it on screen, so the callout
        # never runs off an edge the pointer is near, nor covers the point.
        x = pos.x() + 14
        if x + w > plot.right() - 2:
            x = pos.x() - 14 - w
        y = pos.y() + 14
        if y + h > plot.bottom() - 2:
            y = pos.y() - 14 - h
        x = float(np.clip(x, plot.left() + 2, max(plot.left() + 2, plot.right() - w - 2)))
        y = float(np.clip(y, plot.top() + 2, max(plot.top() + 2, plot.bottom() - h - 2)))
        box = QRectF(x, y, w, h)
        p.setBrush(QColor(0, 0, 0, 225))
        p.setPen(QPen(colour))
        p.drawRect(box)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(pos, 3.0, 3.0)
        for i, text in enumerate(lines):
            p.setPen(colour if i == 0 else READOUT)
            p.drawText(QRectF(box.left() + 6, box.top() + 4 + i * fm.height(), w - 12, fm.height()),
                       Qt.AlignLeft | Qt.AlignVCenter, text)

    def _draw_readout(self, p: QPainter, plot: QRectF, delay: float):
        font = QFont()
        font.setPointSize(8)
        font.setBold(True)
        p.setFont(font)
        # A dark strip behind it: the readout sat straight on the traces and
        # went unreadable wherever a bright one ran under it.
        p.fillRect(QRectF(plot.left() + 1, plot.top() + 1, plot.width() - 2, 17), QColor(0, 0, 0, 200))
        p.setPen(READOUT)
        p.drawText(QRectF(plot.left() + 6, plot.top() + 3, plot.width() / 2, 14),
                   Qt.AlignLeft | Qt.AlignTop,
                   f"{eng_time(self.time_per_div)}/div    " +
                   (f"{eng_volts(self.volts_per_div)}/div" if self.uniform_scale()
                    else "per-channel V/div (see tags)"))
        bits = []
        if self._trigger_col is not None:
            bits.append(self._trig_info or "trig")
        if self._held:
            bits.append("HOLD")          # a frozen screen must never read "live"
        else:
            bits.append("live" if delay <= 0
                        else f"centre -{eng_time(delay + self.view_seconds / 2)}")
        right = "   ".join(bits)
        p.drawText(QRectF(plot.center().x(), plot.top() + 3, plot.width() / 2 - 6, 14),
                   Qt.AlignRight | Qt.AlignTop, right)


class FpgaScopePanel(QWidget):
    """The whole Waveforms tab: controls, graph, legend, status line."""

    REFRESH_MS = 100

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scope: FpgaScope | None = None
        self._held = False
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        top = QHBoxLayout()
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setMinimumWidth(84)
        self.clear_btn.clicked.connect(self._on_clear)
        top.addWidget(self.clear_btn)
        self.hold_btn = QPushButton("Hold")
        self.hold_btn.setCheckable(True)
        self.hold_btn.setMinimumWidth(70)
        self.hold_btn.setToolTip("Freeze the screen so the captured window can be zoomed into.")
        self.hold_btn.toggled.connect(self._on_hold_toggled)
        top.addWidget(self.hold_btn)
        top.addSpacing(12)

        top.addWidget(QLabel("T/div"))
        self.tdiv_combo = QComboBox()
        for v in TIME_PER_DIV:
            self.tdiv_combo.addItem(eng_time(v), v)
        self.tdiv_combo.currentIndexChanged.connect(self._on_tdiv_changed)
        top.addWidget(self.tdiv_combo)

        top.addWidget(QLabel("V/div"))
        self.vdiv_combo = QComboBox()
        for v in VOLTS_PER_DIV:
            self.vdiv_combo.addItem(eng_volts(v), v)
        self.vdiv_combo.currentIndexChanged.connect(self._on_vdiv_changed)
        top.addWidget(self.vdiv_combo)

        top.addWidget(QLabel("Trig"))
        self.trig_combo = QComboBox()
        self.trig_combo.addItem("Free run", None)
        self.trig_combo.addItem("D4 cam trig", IDX_DIO4)
        self.trig_combo.addItem("Int cycle", 14)
        self.trig_combo.setToolTip("Align the screen centre on this signal's rising edge so the "
                                   "waveform stands still and times read against the trigger.")
        self.trig_combo.currentIndexChanged.connect(self._on_trig_changed)
        top.addWidget(self.trig_combo)

        self.perch_chk = QCheckBox("Per-ch V/div")
        self.perch_chk.setToolTip("On (default): each channel keeps its own volts/div, so one "
                                  "waveform can be magnified while another stays put; each tag "
                                  "states its own scale. Off: the channels are ganged, one "
                                  "adjustment moves them all and the axis stays in Volts.")
        self.perch_chk.toggled.connect(self._on_perch_toggled)
        top.addWidget(self.perch_chk)

        self.fit_btn = QPushButton("Fit")
        self.fit_btn.setMinimumWidth(52)
        self.fit_btn.setToolTip("Fit the traces to the screen. In Per-ch mode each channel gets "
                                "its own volts/div, so waveforms of very different amplitude "
                                "become comparable shape-for-shape.")
        self.fit_btn.clicked.connect(self._on_fit)
        top.addWidget(self.fit_btn)
        self.untag_btn = QPushButton("Untag")
        self.untag_btn.setMinimumWidth(58)
        self.untag_btn.setToolTip("Remove every pinned tag. Click a trace to pin one; the tag's "
                                  "+/- buttons set that channel's volts/div.")
        self.untag_btn.clicked.connect(self._on_untag)
        top.addWidget(self.untag_btn)
        top.addStretch(1)

        # Second row for the acquisition-side controls. All of it on one row
        # put the panel's minimum width at 1345, which would have forced the
        # whole 1381px window wider (CLAUDE.md: a control row can do that).
        top2 = QHBoxLayout()

        top2.addWidget(QLabel("# of seconds to buff"))
        self.window_spin = QDoubleSpinBox()
        self.window_spin.setRange(0.05, 5.0)
        self.window_spin.setDecimals(2)
        self.window_spin.setSingleStep(0.5)
        self.window_spin.setValue(2.0)
        self.window_spin.setToolTip("How much history the scope keeps. The screen shows "
                                    "10 x Time/div of it, positioned by the trigger.")
        top2.addWidget(self.window_spin)
        top2.addSpacing(12)
        self.points_label = QLabel("# points acq: 0")
        top2.addWidget(self.points_label)
        top2.addSpacing(12)
        self.stream_chk = QCheckBox("Scope streaming")
        self.stream_chk.setChecked(True)
        self.stream_chk.toggled.connect(self._on_stream_toggled)
        top2.addWidget(self.stream_chk)
        top2.addSpacing(12)
        # "Simulate on FPGA" normally clamps every AO output to 0, which also
        # zeroes the AO columns the scope samples (they are read after the
        # range check). A small test clamp lets waveform SHAPE be seen on
        # the scope with nothing connected to the AO BNCs. 0 = frozen.
        top2.addWidget(QLabel("Scope test clamp"))
        self.test_clamp_spin = QDoubleSpinBox()
        self.test_clamp_spin.setRange(0.0, 500.0)
        self.test_clamp_spin.setDecimals(0)
        self.test_clamp_spin.setSingleStep(50.0)
        self.test_clamp_spin.setSuffix(" mV")
        self.test_clamp_spin.setValue(0.0)
        self.test_clamp_spin.setToolTip("Simulate-on-FPGA only: allow AO outputs up to +-this much so the waveform "
                                        "shows on the scope. Keep 0 unless nothing is connected to the AO BNCs.")
        top2.addWidget(self.test_clamp_spin)
        top2.addStretch(1)

        # Both control rows live in a horizontally scrollable strip. Their
        # labels are LouisXIV's own wording and a QLabel reports its full
        # unwrapped width as a MINIMUM (CLAUDE.md), so leaving them in the
        # layout made this panel demand 1072 px and dragged the whole window
        # past its 1381 px target. Scrolling the strip is the remedy CLAUDE.md
        # prescribes; shaving the labels would cost fidelity instead.
        controls = QWidget()
        controls_lay = QVBoxLayout(controls)
        controls_lay.setContentsMargins(0, 0, 0, 0)
        controls_lay.setSpacing(4)
        controls_lay.addLayout(top)
        controls_lay.addLayout(top2)
        controls_scroll = QScrollArea()
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setFrameShape(QFrame.NoFrame)
        controls_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        controls_scroll.setWidget(controls)
        # Room for the horizontal scrollbar too: it appears inside this fixed
        # height and would otherwise eat the second row of controls.
        controls_scroll.setFixedHeight(controls.sizeHint().height()
                                       + controls_scroll.horizontalScrollBar().sizeHint().height() + 4)
        root.addWidget(controls_scroll)

        body = QHBoxLayout()
        self.trace = ScopeTraceWidget()
        self.trace.setToolTip("Click a trace to pin a tag; its +/- buttons set that "
                              "channel's volts/div.\n"
                              "Wheel: zoom the timebase about the pointer.\n"
                              "Shift+wheel: volts/div about the pointer.\n"
                              "Double-click: back to live and fit.")
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
        self.tdiv_combo.setCurrentIndex(TIME_PER_DIV.index(self.trace.time_per_div))
        self.vdiv_combo.setCurrentIndex(VOLTS_PER_DIV.index(self.trace.volts_per_div))
        self.trace.view_changed.connect(self._sync_view_combos)
        self.trig_combo.setCurrentIndex(1)          # trigger-aligned by default
        self.perch_chk.setChecked(self.trace.per_channel_scale)
        self._on_channel_toggled()

    # -- wiring -----------------------------------------------------------------
    def set_scope(self, scope: FpgaScope | None) -> None:
        self._scope = scope
        if scope is None:
            self._timer.stop()
            self.trace.set_data(None)
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
        self.trace.set_data(None)

    def _on_tdiv_changed(self, _i):
        self.trace.set_time_per_div(self.tdiv_combo.currentData())

    def _on_vdiv_changed(self, _i):
        self.trace.set_volts_per_div(self.vdiv_combo.currentData())

    def _sync_view_combos(self):
        if self.perch_chk.isChecked() != self.trace.per_channel_scale:
            self.perch_chk.blockSignals(True)
            self.perch_chk.setChecked(self.trace.per_channel_scale)
            self.perch_chk.blockSignals(False)
        """Follow a wheel zoom without re-driving the widget from the combo."""
        pairs = [(self.tdiv_combo, TIME_PER_DIV, self.trace.time_per_div)]
        if self.trace.uniform_scale():
            pairs.append((self.vdiv_combo, VOLTS_PER_DIV, self.trace.volts_per_div))
        for combo, seq, value in pairs:
            i = seq.index(value)
            if combo.currentIndex() != i:
                combo.blockSignals(True)
                combo.setCurrentIndex(i)
                combo.blockSignals(False)

    def _on_trig_changed(self, _i):
        self.trace.set_trigger_column(self.trig_combo.currentData())

    def _on_fit(self):
        self.trace.fit_each_channel()
        self._sync_view_combos()

    def _on_untag(self):
        self.trace.clear_tags()

    def _on_perch_toggled(self, on: bool):
        # The V/div combo stays live in both modes: in Per-ch it is the
        # 'set every channel back to this' reset.
        self.trace.set_per_channel_scale(on)
        self._sync_view_combos()

    def _on_hold_toggled(self, on: bool):
        self._held = on
        self.trace.set_held(on)
        self.hold_btn.setText("Held" if on else "Hold")

    def set_interactive(self, on: bool) -> None:
        """Freeze the controls that reach the FPGA scope while the GUI thread
        is inside a blocking driver call. A native driver's message pump keeps
        delivering clicks, so a live checkbox here could start or stop the
        scope thread from inside camera.disconnect() (MainWindow._begin_blocking).
        """
        self.stream_chk.setEnabled(on)
        self.clear_btn.setEnabled(on)

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
        # Hold freezes the screen: keep the last snapshot so it can be zoomed
        # and panned, but go on reporting the live status line underneath.
        if not self._held:
            need = min(max(self.trace.seconds_needed(), 0.05), self.window_spin.value())
            snap = scope.snapshot(seconds=need)
            self.trace.set_data(snap)
        self.points_label.setText(f"# points acq: {scope.ring.total:,}")
        parts = [f"{scope.fs_hz:,.0f} S/s x {scope.channels} ch", f"buffered {scope.seconds_buffered():.1f} s",
                 f"backlog max {scope.backlog_max}"]
        err = [k for k, v in scope.ai_error.items() if v]
        parts.append("AI err " + (",".join(err) if err else "-"))
        stat_snap = scope.snapshot(seconds=self.window_spin.value())
        if scope.channels > IDX_DIO4 and len(stat_snap.frames):
            st = measure_period(stat_snap.frames[:, IDX_DIO4], scope.fs_hz)
            if st is not None:
                parts.append(f"DIO4: {st.edges} edges, period {st.period_ms:.4f} ms "
                             f"(sd {st.period_sd_ms:.4f}), high {st.high_ms:.3f} ms")
            else:
                parts.append("DIO4: no edges in window")
        if self._held:
            parts.append("HELD")
        if not scope.running:
            parts.append("STOPPED")
        if scope.last_error:
            parts.append(scope.last_error)
        self.status_label.setText("Scope: " + " | ".join(parts))
