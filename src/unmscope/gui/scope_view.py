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
#: Top-to-bottom order for the legend AND for the slots on screen, so the
#: list you tick down the side is the order the traces come out in and the
#: numbers in the scale bar agree with it. The user's order (2026-09-07):
#: the channels actually watched during a run first, the eight raw analog
#: inputs and the unused columns after. Anything not named here falls in at
#: the end in column order, so a column added to AI_CHANNEL_NAMES later
#: still appears instead of silently vanishing from the list.
LEGEND_ORDER = (9, 10, 8, 15, 14, 11, 16, 17, 19, 20, 21, 22, 23)

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


def legend_order(n_columns: int) -> list[int]:
    """Column indices in display order: LEGEND_ORDER first, then whatever
    else exists, in column order."""
    named = [c for c in LEGEND_ORDER if c < n_columns]
    return named + [c for c in range(n_columns) if c not in set(named)]


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

    #: Left is now just the ground-marker gutter (there are no volts
    #: numbers to print); the bottom carries the time labels AND the
    #: per-channel volts/div bar, the way a DSO states its scales.
    LEFT, RIGHT, TOP, BOTTOM = 30, 14, 12, 52

    #: Fraction of its own slot a channel's peak-to-peak is scaled to fill.
    SLOT_FILL = 0.72

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
        #: False = the standard scope view, one slot per channel. True =
        #: every channel centred on the middle line and superimposed, for
        #: comparing edges between channels directly.
        self._overlay = False
        self._delay_s = 0.0        # how far the right edge sits behind "now"
        #: Centre a segment SHORTER than the window instead of anchoring it
        #: at the right edge. Only bites when there is blank screen to
        #: share out -- a live buffer that fills the window is unaffected.
        self._h_center = False
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

    def seconds_needed(self) -> float:
        """What the panel must pull from the ring to fill this view.

        Trigger alignment needs history beyond the screen to find an edge at
        all -- at 200 us/div the screen holds 2 ms and the triggers are 100 ms
        apart, so asking only for the screen would never find one.
        """
        want = self.view_seconds + self._delay_s
        if self._trigger_col is None:
            return want
        # Trigger mode: ask for every second there is -- the ring caps it at
        # what it holds. A Z stack is a burst of a few hundred ms every few
        # seconds; looking back only 3x the screen missed the burst between
        # passes and dropped the view to a flat live line.
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
        before, was = set(self._enabled), self.slot_centres()
        self._enabled = set(int(i) for i in enabled)
        # The slot a channel occupies depends on how many are shown, so
        # ticking or unticking one re-deals the whole stack -- keeping every
        # channel's own gain, only re-seating it in its new slot.
        if self._enabled != before and not self._overlay:
            self._reseat_slots(was)
        self.update()

    def set_held(self, on: bool) -> None:
        self._held = bool(on)
        self.update()

    def set_data(self, snap: ScopeSnapshot | None) -> None:
        # A new frame re-anchors the sweep on "now": the right edge of a
        # rolling view IS the newest sample, and centring a buffer shorter
        # than the window would park it mid-screen and make a live trace
        # look frozen. Only a view that has STOPPED arriving can be centred
        # -- Hold and the Simulated source both stop calling this, so
        # Centre (and Fit) stick exactly where they are meant to.
        self._h_center = False
        self._snap = snap
        if snap is None:
            self._held_trig = None
        self.update()

    # -- vertical: one slot per channel --------------------------------------
    #
    # A DSO gives every channel its OWN volts/div and its own vertical
    # position, and draws a ground-reference marker at the left edge showing
    # where that channel's 0 V sits:
    #
    #     divisions_from_centre = volts / gain(c) + offset(c)
    #
    # There is deliberately no shared-scale mode any more. One gain cannot
    # serve this instrument -- the digital flags swing 1.25 V while the X
    # galvo is +-0.125 V -- so a Volts axis would be lying about every channel
    # it did not belong to, and the axis flipping between "Volts" and
    # "Divisions" depending on whether the gains happened to match was the
    # single most confusing thing on the screen. The graticule is divisions,
    # always; each channel's scale is stated in the bar under it.
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

    def slot_centres(self) -> dict[int, float]:
        """Divisions from the screen centre for each shown channel's slot,
        top of the screen first. n channels split the 8 divisions evenly."""
        cols = self._visible_columns()
        if not cols:
            return {}
        h = VDIV / len(cols)
        return {c: VDIV / 2 - (i + 0.5) * h for i, c in enumerate(cols)}

    def slot_index(self, c: int) -> int | None:
        """1-based position down the screen -- the number on the channel's
        ground marker and in the volts/div bar."""
        cols = self._visible_columns()
        return cols.index(c) + 1 if c in cols else None

    def _reseat_slots(self, was: dict[int, float]) -> None:
        """Re-deal the stack after the shown set changed: shift each channel
        that is still up by the DELTA between its old and new slot centre.

        A delta rather than an assignment, so a channel the user has nudged
        off its slot line keeps that position relative to the slot, and a
        channel that has never been placed is left on the default.
        """
        for c, centre in self.slot_centres().items():
            if c in self._offset and c in was:
                self._offset[c] += centre - was[c]

    def step_gain(self, c: int, steps: int, from_tag: bool = False) -> None:
        """Move this channel's volts/div by whole 1-2-5 steps (tag buttons,
        Shift+wheel). Only ever this channel: that is what per-channel means.

        The trace expands about its own ground marker, because offset() is in
        divisions and pins where 0 V sits -- the same thing a real scope's
        volts/div knob does.
        """
        i = VOLTS_PER_DIV.index(self.gain(c))
        self._gain[c] = VOLTS_PER_DIV[int(np.clip(i + steps, 0, len(VOLTS_PER_DIV) - 1))]
        self.update()
        self.view_changed.emit()

    @property
    def volts_per_div(self) -> float:
        """The gain shown in the toolbar combo: the shallowest in play, so
        the combo names a real scale that is actually on screen."""
        cols = self._visible_columns()
        if not cols:
            return self.DEFAULT_GAIN
        return min(self.gain(c) for c in cols)

    def set_volts_per_div(self, value: float) -> None:
        """Set every shown channel's gain: the toolbar combo is a 'set all'.
        Per-channel adjustment lives on the tags and on Shift+wheel."""
        # Positions are left alone: offset() pins where 0 V sits, so every
        # trace rescales about its own ground marker and stays in its slot.
        value = min(VOLTS_PER_DIV, key=lambda v: abs(v - value))
        for c in self._visible_columns():
            self._gain[c] = value
        self.update()

    @property
    def overlay(self) -> bool:
        return self._overlay

    def set_overlay(self, on: bool) -> None:
        """Switch between the two presentations and re-fit for the new one.

        Stacked (default) is the standard scope view: one slot per channel.
        Overlay centres every channel on the middle line and superimposes
        them, which is what you want when the question is "does this edge
        line up with that one" rather than "what is each channel doing".
        """
        on = bool(on)
        if on == self._overlay:
            return
        self._overlay = on
        self.autoset()

    def autoset(self) -> None:
        """The scope's Autoset: give every shown channel its own volts/div
        and put it where the current presentation says it goes.

        Stacked -- one slot per channel, top to bottom, each scaled so its
        peak-to-peak fills its own slot. Overlay -- every channel centred on
        the middle line and scaled to fill the screen, so the traces lie on
        top of one another and edges can be compared directly.
        """
        seg = self._visible_segment()
        cols = self._visible_columns()
        if seg is None or len(seg) == 0 or not cols:
            return
        # A stopped screen (Hold, or the Simulated source) shares its blank
        # out evenly; a live one re-anchors on "now" at the next frame.
        self._h_center = True
        if self._overlay:
            # Everything on the centre line, DC removed: the levels are what
            # you are deliberately ignoring when you overlay.
            usable, centres = VDIV - 2, {c: 0.0 for c in cols}
        else:
            usable, centres = (VDIV / len(cols)) * self.SLOT_FILL, self.slot_centres()
        for c, centre in centres.items():
            col = seg[:, c].astype(np.float64) * AI_VOLTS_PER_COUNT
            lo, hi = float(col.min()), float(col.max())
            if not (np.isfinite(lo) and np.isfinite(hi)):
                continue
            # Peak-to-peak sets the scale, but stacked so does the DC level:
            # a channel parked flat at 0.8 V has no p-p at all, and scaling
            # it on that alone picked 1 mV/div -- a meaningless readout that
            # put its ground marker 800 divisions off screen. Overlaid the
            # level is removed anyway, so only p-p can set the scale there.
            need = (hi - lo) / usable
            if not self._overlay:
                need = max(need, abs(hi + lo) / 2 / (VDIV / 2))
            if need <= 0:
                # Nothing to scale TO: a channel flat at 0 V (an AOTF under
                # the simulate-on-FPGA clamp, say), or any flat channel once
                # overlay has removed its level. Falling through would pick
                # the smallest step in the list and state "1 mV/div" about a
                # line that carries no information at all -- keep whatever
                # scale the channel already had instead.
                self._gain.setdefault(c, self.DEFAULT_GAIN)
            else:
                self._gain[c] = next((v for v in VOLTS_PER_DIV if v >= need), VOLTS_PER_DIV[-1])
            self._offset[c] = centre - ((hi + lo) / 2) / self._gain[c]
        self.update()
        self.view_changed.emit()

    def fit_gains(self) -> None:
        """Auto-adjust every shown channel's volts/div so its peak-to-peak
        fills the display -- gains, not layout.

        The difference from Autoset: Autoset decides WHERE each channel goes
        (a slot each, or all on the centre line) and scales it to fit there.
        Fit leaves the arrangement exactly as it is and just makes every
        trace as big as the screen allows, which is what you want once six
        channels in six slots are each too small to read.

        Each trace keeps its own midpoint, so only its SIZE changes -- except
        where growing would push it off an edge, in which case it slides back
        on by the least amount that fits. "Fit in the display" has to mean
        the whole trace is actually in the display.
        """
        seg = self._visible_segment()
        cols = self._visible_columns()
        if seg is None or len(seg) == 0 or not cols:
            return
        for c in cols:
            col = seg[:, c].astype(np.float64) * AI_VOLTS_PER_COUNT
            lo, hi = float(col.min()), float(col.max())
            if not (np.isfinite(lo) and np.isfinite(hi)) or hi <= lo:
                continue                       # a flat line has nothing to fit
            mid = (hi + lo) / 2
            where = mid / self.gain(c) + self.offset(c)      # divisions, as drawn now
            need = (hi - lo) / (VDIV - 2)
            self._gain[c] = next((v for v in VOLTS_PER_DIV if v >= need), VOLTS_PER_DIV[-1])
            half = (hi - lo) / 2 / self._gain[c]
            room = max(0.0, VDIV / 2 - half)
            self._offset[c] = float(np.clip(where, -room, room)) - mid / self._gain[c]
        self.update()
        self.view_changed.emit()

    def reset_view(self) -> None:
        """Back to what a standard oscilloscope shows: the stacked view,
        every channel re-fitted, nothing left over from a hand adjustment.

        Deliberately display-only. It does NOT touch the timebase, the
        trigger or which channels are ticked -- those are measurement
        choices the user made, and clobbering them on a "reset" is the
        same defaults-kicking-in complaint that Acquire had.
        """
        self._overlay = False
        self._gain.clear()
        self._offset.clear()
        self._delay_s = 0.0
        self._h_center = False
        self.autoset()

    def _px_per_div(self, plot: QRectF) -> float:
        return plot.height() / VDIV

    def _volts_to_y(self, c: int, v, plot: QRectF):
        div = np.asarray(v) / self.gain(c) + self.offset(c)
        return plot.center().y() - div * self._px_per_div(plot)

    def _y_to_div(self, y: float, plot: QRectF) -> float:
        return (plot.center().y() - y) / self._px_per_div(plot)

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
        """The shown channels, TOP TO BOTTOM -- in LEGEND_ORDER, not column
        order, so a trace's slot and its number match where it sits in the
        legend beside the screen."""
        frames = self._snap.frames if self._snap is not None else None
        if frames is None:
            return []
        return [c for c in legend_order(frames.shape[1])
                if c in self._enabled and c < frames.shape[1]]

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
            targets = [hovered] if hovered is not None else (self._visible_columns() or [])
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
                self._h_center = False        # the user is positioning it by hand now
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
        self.autoset()
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
        self._draw_ground_markers(p, plot)
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
        # No numbers up the side. Each channel has its own volts/div and its
        # own position, so no single voltage belongs to a pixel row -- a
        # real DSO prints none either. Where a channel's 0 V sits is shown by
        # its ground marker at the left edge; what its scale is, by the bar
        # under the screen.
        #
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
        p.drawText(QRectF(plot.left(), plot.bottom() + 17, 260, 13), Qt.AlignLeft,
                   f"t from {ref} ({time_axis_unit(per_div)})")
        self._draw_channel_bar(p, plot)

    def _draw_channel_bar(self, p: QPainter, plot: QRectF):
        """The scale bar under the screen: `1 X Galvo 200mV` per shown
        channel, in the channel's own colour -- how a DSO states per-channel
        volts/div now that the vertical axis carries no numbers."""
        cols = self._visible_columns()
        if not cols:
            return
        font = QFont()
        font.setPointSize(8)
        p.setFont(font)
        fm = p.fontMetrics()
        y = plot.bottom() + 32
        # Name + scale while they fit; scale alone once the row would overrun.
        def chips(with_names: bool):
            out = []
            for i, c in enumerate(cols, start=1):
                name = LEGEND_LABELS.get(c, AI_CHANNEL_NAMES[c] if c < len(AI_CHANNEL_NAMES) else str(c))
                out.append((c, f"{i} {name} {eng_volts(self.gain(c))}" if with_names
                            else f"{i}:{eng_volts(self.gain(c))}"))
            return out
        items = chips(True)
        if sum(fm.horizontalAdvance(t) + 14 for _c, t in items) > plot.width():
            items = chips(False)
        x = plot.left()
        for c, text in items:
            w = fm.horizontalAdvance(text)
            if x + w > plot.right():
                break
            p.setPen(QPen(QColor(PALETTE[c % len(PALETTE)]).darker(140)))
            p.drawText(QRectF(x, y, w + 2, 14), Qt.AlignLeft | Qt.AlignVCenter, text)
            x += w + 14
        p.setPen(QColor(20, 20, 20))

    def _draw_ground_markers(self, p: QPainter, plot: QRectF):
        """A DSO's ground-reference markers: a filled arrow in the left
        gutter at each shown channel's 0 V, numbered by its slot. This is
        what makes a stack of traces readable -- you can see at a glance
        which one is which and where its zero is."""
        cols = self._visible_columns()
        if not cols:
            return
        font = QFont()
        font.setPointSize(7)
        font.setBold(True)
        p.setFont(font)
        for i, c in enumerate(cols, start=1):
            y = float(self._volts_to_y(c, 0.0, plot))
            colour = QColor(PALETTE[c % len(PALETTE)])
            # 0 V off screen: park the marker on the edge it went past and
            # draw it hollow, the way a scope does, rather than dropping it
            # -- the channel still needs a number you can find it by.
            on_screen = plot.top() <= y <= plot.bottom()
            y = float(np.clip(y, plot.top() + 6, plot.bottom() - 6))
            p.setPen(QPen(colour))
            p.setBrush(colour if on_screen else Qt.NoBrush)
            p.drawPolygon(QPolygonF([QPointF(plot.left() - 12, y - 5),
                                     QPointF(plot.left() - 12, y + 5),
                                     QPointF(plot.left() - 2, y)]))
            p.setBrush(Qt.NoBrush)
            p.setPen(QColor(20, 20, 20))
            p.drawText(QRectF(plot.left() - 26, y - 7, 12, 14),
                       Qt.AlignRight | Qt.AlignVCenter, str(i))
            if on_screen:
                # ...and its zero line across the screen, faintly, so a trace
                # is read against its OWN baseline, not the graticule centre.
                pen = QPen(QColor(colour.red() // 3, colour.green() // 3, colour.blue() // 3))
                pen.setStyle(Qt.DotLine)
                p.setPen(pen)
                p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

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
        if self._h_center and have_s < self.view_seconds:
            # Share the blank equally instead: the data block's left edge sits
            # (view - have)/2 in from the left, i.e. (view + have)/2 before the
            # right edge. Centre puts it here; panning or zooming releases it.
            x_left = self._t_to_x((self.view_seconds + have_s) / 2.0, plot)
        else:
            x_left = self._t_to_x(have_s, plot)
        # The width the DATA occupies. Identical to (plot.right() - x_left)
        # whenever the block is anchored at the right edge, but that form
        # silently stretched it back out to the edge once the block was
        # centred -- so the pan only ever got it half of the way there.
        width_px = min(1.0, have_s / self.view_seconds) * plot.width()

        for c in cols:
            col = seg[:, c].astype(np.float64) * AI_VOLTS_PER_COUNT
            mins, maxs = envelope(col, columns)
            m = len(mins)
            if m <= 2:
                continue
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
                   f"{eng_time(self.time_per_div)}/div")
        p.drawText(QRectF(plot.center().x(), plot.top() + 3, plot.width() / 2 - 6, 14),
                   Qt.AlignRight | Qt.AlignTop, self.status_text(delay))

    def status_text(self, delay: float) -> str:
        """The top-right corner: what the screen is doing right now."""
        bits = []
        if self._overlay:
            # Traces piled on one another read as a fault unless the screen
            # says it is a mode you asked for.
            bits.append("OVERLAY")
        if self._trigger_col is not None:
            bits.append(self._trig_info or "trig")
        if self._held:
            bits.append("HOLD")          # a frozen screen must never read "live"
        else:
            bits.append("live" if delay <= 0
                        else f"centre -{eng_time(delay + self.view_seconds / 2)}")
        return "   ".join(bits)


class FpgaScopePanel(QWidget):
    """The whole Waveforms tab: controls, graph, legend, status line."""

    REFRESH_MS = 100

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scope: FpgaScope | None = None
        self._held = False
        #: "hardware" (default, unchanged behaviour) or "simulated". Gates
        #: _refresh() only -- see there. Never gates anything that reaches
        #: self._scope or the FPGA.
        self._mode = "hardware"
        self._preview_snap: ScopeSnapshot | None = None
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

        self.autoset_btn = QPushButton("Autoset")
        self.autoset_btn.setMinimumWidth(70)
        self.autoset_btn.setToolTip("Deal every shown channel into its own horizontal slot, top "
                                    "to bottom, each with its own volts/div so its peak-to-peak "
                                    "fills that slot. The numbered arrow in the left gutter marks "
                                    "where each channel's 0 V sits; the bar under the screen "
                                    "states its scale. Double-clicking the screen does the same.")
        self.autoset_btn.clicked.connect(self._on_autoset)
        top.addWidget(self.autoset_btn)
        self.fit_btn = QPushButton("Fit")
        self.fit_btn.setMinimumWidth(52)
        self.fit_btn.setToolTip("Auto-adjust every channel's volts/div so its waveform fills the "
                                "display, leaving the arrangement alone. Autoset decides WHERE "
                                "each channel goes and fits it to that room; Fit just makes what "
                                "is already there as big as the screen allows.")
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

        self.overlay_btn = QPushButton("Overlay")
        self.overlay_btn.setCheckable(True)
        self.overlay_btn.setMinimumWidth(70)
        self.overlay_btn.setToolTip("Centre every shown channel on the middle line and superimpose "
                                    "them, each scaled to fill the screen. For 'does this edge line "
                                    "up with that one' -- the DC levels are removed, so only shape "
                                    "and timing are being compared. Press again (or Reset) to go "
                                    "back to a slot per channel.")
        self.overlay_btn.toggled.connect(self._on_overlay_toggled)
        top2.addWidget(self.overlay_btn)
        self.reset_btn = QPushButton("Reset")
        self.reset_btn.setMinimumWidth(62)
        self.reset_btn.setToolTip("Back to what a standard scope shows: one slot per channel, every "
                                  "one re-fitted, nothing left over from a hand adjustment. Leaves "
                                  "the timebase, the trigger and your channel ticks alone.")
        self.reset_btn.clicked.connect(self._on_reset)
        top2.addWidget(self.reset_btn)
        top2.addSpacing(12)
        top2.addWidget(QLabel("# of seconds to buff"))
        self.window_spin = QDoubleSpinBox()
        self.window_spin.setRange(0.05, 5.0)
        self.window_spin.setDecimals(2)
        self.window_spin.setSingleStep(0.5)
        self.window_spin.setValue(2.0)
        self.window_spin.setToolTip("How much history the DIO4 period/jitter readout below is "
                                    "measured over. It does NOT limit the screen: the ring keeps "
                                    "5 s and the trace always fills 10 x Time/div of it.")
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
        for c in legend_order(len(AI_CHANNEL_NAMES)):
            name = AI_CHANNEL_NAMES[c]
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
        self._streamed = len(AI_CHANNEL_NAMES)
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
        self._on_channel_toggled()

    # -- wiring -----------------------------------------------------------------
    def persistent_widgets(self) -> dict:
        """The settings worth carrying into the next session (see
        unmscope.config.ui_state). Hold is deliberately absent: coming up on
        a frozen screen reads as broken, not as restored.
        """
        w = {"scope_tdiv": self.tdiv_combo, "scope_vdiv": self.vdiv_combo,
             "scope_trig": self.trig_combo, "scope_overlay": self.overlay_btn,
             "scope_buffer_s": self.window_spin, "scope_streaming": self.stream_chk,
             "scope_test_clamp": self.test_clamp_spin}
        w.update({f"scope_ch_{c}": chk for c, chk in self.channel_checks.items()})
        return w

    def set_streamed_channels(self, n: int) -> None:
        """Grey out the channels the current source does not carry.

        Nothing above "AI # of channels" is in the DMA stream, so ticking one
        drew nothing at all and gave no clue why -- which is exactly how a
        run went by with every AOTF box ticked and no AOTF trace on screen.
        A control that cannot work is disabled, per CLAUDE.md.
        """
        self._streamed = int(n)
        for c, chk in self.channel_checks.items():
            live = c < self._streamed
            chk.setEnabled(live)
            chk.setToolTip("" if live else
                           f"Not in the stream: this source carries {self._streamed} of "
                           f"{len(AI_CHANNEL_NAMES)} columns, and this one is column {c}.")
            if not live and chk.isChecked():
                chk.blockSignals(True)
                chk.setChecked(False)
                chk.blockSignals(False)
        self._on_channel_toggled()

    def set_scope(self, scope: FpgaScope | None) -> None:
        self._scope = scope
        if scope is not None:
            self.set_streamed_channels(scope.channels)
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
        """Follow a wheel zoom without re-driving the widget from the combo."""
        pairs = [(self.tdiv_combo, TIME_PER_DIV, self.trace.time_per_div),
                 (self.vdiv_combo, VOLTS_PER_DIV, self.trace.volts_per_div)]
        for combo, seq, value in pairs:
            i = seq.index(value)
            if combo.currentIndex() != i:
                combo.blockSignals(True)
                combo.setCurrentIndex(i)
                combo.blockSignals(False)

    def _on_trig_changed(self, _i):
        self.trace.set_trigger_column(self.trig_combo.currentData())

    def _on_autoset(self):
        self.trace.autoset()
        self._sync_view_combos()

    def _on_fit(self):
        self.trace.fit_gains()
        self._sync_view_combos()

    def _on_overlay_toggled(self, on: bool):
        self.trace.set_overlay(on)
        self._sync_view_combos()

    def _on_reset(self):
        # "shows the waveforms as shown by a standard oscilloscope" -- which
        # includes showing the live input. With the Source combo gone this is
        # also the only manual way out of the simulated view.
        self.show_live()
        self.trace.reset_view()
        if self.overlay_btn.isChecked():
            self.overlay_btn.blockSignals(True)      # reset_view already left overlay
            self.overlay_btn.setChecked(False)
            self.overlay_btn.blockSignals(False)
        self._sync_view_combos()

    def _on_untag(self):
        self.trace.clear_tags()

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

    def show_live(self) -> None:
        """Leave the simulated view and go back to the live FPGA scope.

        There is no Source control any more (the user removed it: the mode
        is decided by what you Acquire, not by a dropdown). Acquire puts the
        screen into the simulated view on a Simulate-on-FPGA run and takes it
        out again on a real-camera one; Reset is the manual way back, so a
        run left on screen can never strand the panel away from live.
        """
        if self._mode == "hardware":
            return
        self._mode = "hardware"
        # Resume immediately rather than waiting up to REFRESH_MS for the
        # timer -- self._scope itself was never touched by the simulated
        # view, so this just goes back to reading it.
        if self._scope is not None:
            self._refresh()
        else:
            self.trace.set_data(None)
            self.status_label.setText("Scope: FPGA not connected")

    def show_computed_waveform(self, snap: ScopeSnapshot) -> None:
        """MainWindow calls this with the TRUE waveform, whenever an Acquire
        starts a Simulate-on-FPGA run: the real scope is known-uninformative
        there, clamped to 0 V by design, so showing the computed one is the
        more useful default (docs/known_issues.md). Puts the panel into the
        simulated view, and only ever touches self.trace / the status and
        points labels -- never self._scope, never anything FPGA-facing.
        """
        self._mode = "simulated"
        self._preview_snap = snap
        self.set_streamed_channels(snap.frames.shape[1])
        # A Z stack's slow axes (Z Piezo included) are constant WITHIN a
        # slice and only step BETWEEN slices -- correctly flat over any one
        # trigger. Whatever T/div was last showing (left over from the live
        # scope, or a previous zoom) can be much narrower than one slice,
        # let alone the whole stack, in which case every step looks like
        # nothing is happening. autoset() only ever rescales
        # volts, never the timebase (see its own docstring) -- widen T/div
        # first, to whatever shows the ENTIRE computed buffer.
        total_s = len(snap.frames) / snap.fs_hz if snap.fs_hz else 0.0
        if total_s > 0:
            need_per_div = total_s / HDIV
            fit_tdiv = next((v for v in TIME_PER_DIV if v >= need_per_div), TIME_PER_DIV[-1])
            self.trace.set_time_per_div(fit_tdiv)
        # A computed snapshot has no real Int Sync / DIO4 edges to align on
        # (those columns are left at 0 -- this is the AO side only);
        # trigger-searching them would just report "no trigger" at best and
        # can hide data at worst, so free-run for this view. Through the
        # combo (not trace.set_trigger_column directly) so the dropdown
        # itself shows what the screen is actually doing.
        self.trig_combo.setCurrentIndex(0)
        # RESCUE ONLY -- never on a screen that is already showing something.
        # An earlier version ticked every column carrying data on every
        # single call, which meant each Acquire reimposed a channel set over
        # whatever the user had chosen. Their words: "everytime I hit acquire
        # the defaults displays are kicking in. I want only the waveforms I
        # checked be displayed." So: if any ticked channel already has data,
        # change nothing at all. Only when the screen would be blank -- none
        # of the ticked channels hold anything -- tick the ones that do,
        # which is also what puts the live laser's AOTF channel up the first
        # time (DEFAULT_ACTIVE hard-codes AOTF 0/1, but 488 is AOTF 2 here).
        has_data = {col for col in self.channel_checks
                    if col < snap.frames.shape[1] and np.any(snap.frames[:, col])}
        if not any(self.channel_checks[c].isChecked() for c in has_data):
            for col in has_data:
                self.channel_checks[col].setChecked(True)
        self.trace.set_data(snap)
        self.trace.autoset()
        self._sync_view_combos()
        self.points_label.setText(f"# points: {len(snap.frames):,} (computed)")
        self.status_label.setText("Scope: SIMULATED -- computed waveform, no voltage sent to the FPGA")

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
        if self._mode == "simulated":
            return    # the screen shows the last show_computed_waveform() result, untouched
        scope = self._scope
        if scope is None:
            return
        # The screen gets EVERY second it can show. This used to be clamped to
        # "# of seconds to buff" as well, which is not a property of the
        # screen at all: the ring is a fixed 5 s and the widest timebase
        # (0.5 s/div x 10) is a 5 s window, so the ring can always fill the
        # screen -- but with the spinbox at its 2.00 default, 500 ms/div asked
        # for 2 s of a 5 s window and the trace could only ever occupy the
        # right-hand 40%, with the rest blank. "the waveforms appear on the
        # right hand side of the scope". The ring itself caps the request at
        # what it actually holds, which is the only limit that is real.
        need = max(max(self.trace.seconds_needed(), 0.05), self.window_spin.value())
        snap = scope.snapshot(seconds=need)      # one copy, shared with the stats below
        # Hold freezes the screen: keep the last snapshot so it can be zoomed
        # and panned, but go on reporting the live status line underneath.
        if not self._held:
            self.trace.set_data(snap)
        self.points_label.setText(f"# points acq: {scope.ring.total:,}")
        parts = [f"{scope.fs_hz:,.0f} S/s x {scope.channels} ch", f"buffered {scope.seconds_buffered():.1f} s",
                 f"backlog max {scope.backlog_max}"]
        err = [k for k, v in scope.ai_error.items() if v]
        parts.append("AI err " + (",".join(err) if err else "-"))
        # The spinbox keeps its own meaning here -- how much history the DIO4
        # period/jitter is measured over -- but taken as a slice of the
        # snapshot already in hand rather than a second full copy of the ring.
        stat_n = max(1, int(self.window_spin.value() * scope.fs_hz))
        stat_frames = snap.frames[-stat_n:]
        if scope.channels > IDX_DIO4 and len(stat_frames):
            st = measure_period(stat_frames[:, IDX_DIO4], scope.fs_hz)
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
