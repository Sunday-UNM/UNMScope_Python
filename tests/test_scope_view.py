"""The Waveforms tab: decimation, axis labelling and trigger alignment."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from unmscope.gui.scope_view import (  # noqa: E402
    HDIV, VDIV, TIME_PER_DIV, VOLTS_PER_DIV, FpgaScopePanel, ScopeTraceWidget, eng_time, eng_volts,
    envelope, time_axis_label,
)
from unmscope.hardware.fpga_scope import (  # noqa: E402
    AI_CHANNEL_NAMES, AI_VOLTS_PER_COUNT, IDX_DIO4, ScopeSnapshot,
)


def test_envelope_keeps_a_short_pulse_visible():
    y = np.zeros(100_000)
    y[50_000:50_002] = 4096          # a 2-sample pulse in 100k samples
    mins, maxs = envelope(y, 900)
    assert len(mins) == len(maxs) == 900
    assert maxs.max() == 4096        # still visible after 111:1 decimation
    assert mins.min() == 0
    assert (maxs > 0).sum() == 1     # in exactly one pixel column


def test_envelope_passthrough_when_short():
    y = np.arange(10.0)
    mins, maxs = envelope(y, 900)
    np.testing.assert_array_equal(mins, y)
    np.testing.assert_array_equal(maxs, y)


def test_envelope_remainder_folded_into_last_column():
    y = np.zeros(1005)
    y[-1] = 7                        # lives in the remainder past 1000 = 100 x 10
    mins, maxs = envelope(y, 100)
    assert len(maxs) == 100 and maxs[-1] == 7


def test_envelope_empty():
    mins, maxs = envelope(np.zeros(0), 10)
    assert len(mins) == 0 and len(maxs) == 0


def test_envelope_puts_a_feature_where_it_belongs_in_time():
    """The graticule is meant to be measured against, so a column must cover
    an equal slice of TIME. The old `per = n // columns` + fold-the-remainder
    scheme drew a mid-window feature at 63% across -- a 13% time error that
    put the trigger three divisions off centre."""
    n, cols = 2000, 788
    y = np.zeros(n)
    y[n // 2] = 1.0
    _mins, maxs = envelope(y, cols)
    k = int(np.flatnonzero(maxs > 0)[0])
    assert abs(k / cols - 0.5) < 0.01, f"mid-window pulse drew at {k / cols:.3f}"


def test_envelope_is_monotonic_in_time():
    n, cols = 5000, 300
    y = np.arange(n, dtype=float)
    _mins, maxs = envelope(y, cols)
    assert np.all(np.diff(maxs) > 0)
    assert maxs[0] < n * 0.01 and maxs[-1] > n * 0.99


def test_time_axis_labels_are_short_round_and_centred():
    # 2 ms/div: the five labels are -4, -2, 0, +2, +4 ms. Referencing "now"
    # instead produced things like -165900 once the view was panned back.
    labels = [time_axis_label((i - HDIV / 2) * 2e-3, 2e-3) for i in range(1, HDIV, 2)]
    assert labels == ["-8", "-4", "0", "+4", "+8"]
    assert all(len(t) <= 3 for t in labels)


def test_time_axis_labels_switch_unit_with_the_timebase():
    assert time_axis_label(-400e-6, 200e-6) == "-400"      # microseconds
    assert time_axis_label(0.0, 200e-6) == "0"
    assert time_axis_label(0.2, 0.1) == "+0.2"             # seconds from 100 ms/div
    assert time_axis_label(-1.0, 0.5) == "-1"              # not "-1000" ms


def test_axis_label_and_axis_title_agree_on_the_unit():
    from unmscope.gui.scope_view import TIME_PER_DIV, time_axis_unit
    for per_div in TIME_PER_DIV:
        unit = time_axis_unit(per_div)
        val = float(time_axis_label(per_div, per_div))     # one division, in that unit
        scale = {"µs": 1e6, "ms": 1e3, "s": 1.0}[unit]
        assert val == pytest.approx(per_div * scale), f"{per_div}: label/title unit mismatch"


def test_engineering_formatting():
    assert eng_time(200e-6) == "200 µs"
    assert eng_time(20e-3) == "20 ms"
    assert eng_time(0.5) == "500 ms"
    assert eng_volts(0.2) == "200 mV"
    assert eng_volts(2.0) == "2 V"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _snapshot(seconds=2.0, fs=100_000.0, period=0.1, offset=0.0371):
    n = int(seconds * fs)
    t = np.arange(n) / fs
    v = np.zeros((n, len(AI_CHANNEL_NAMES)))
    v[:, IDX_DIO4] = np.where(((t - offset) % period) < 1e-4, 1.25, 0.0)
    frames = np.clip(v / AI_VOLTS_PER_COUNT, -32768, 32767).astype(np.int16)
    return ScopeSnapshot(frames=frames, fs_hz=fs, names=tuple(AI_CHANNEL_NAMES), end_frame_index=n)


def test_trigger_alignment_lands_the_edge_on_the_screen_centre(app):
    w = ScopeTraceWidget()
    w.set_time_per_div(2e-3)                 # 20 ms window, triggers 100 ms apart
    w.set_data(_snapshot())
    w.set_trigger_column(IDX_DIO4)
    seg = w._visible_segment()
    assert seg is not None
    col = seg[:, IDX_DIO4].astype(float) * AI_VOLTS_PER_COUNT
    centre = len(seg) // 2
    assert col[centre] > 0.6, "no trigger edge at the screen centre"
    assert col[centre - 1] <= 0.6, "the centre is not the RISING edge"


def test_free_run_does_not_move_the_window(app):
    w = ScopeTraceWidget()
    w.set_time_per_div(2e-3)
    w.set_data(_snapshot())
    w.set_trigger_column(None)
    assert w._effective_delay() == 0.0        # right edge stays at "now"


def test_trigger_mode_asks_for_more_history_than_the_screen(app):
    w = ScopeTraceWidget()
    w.set_time_per_div(200e-6)                # 2 ms screen, triggers 100 ms apart
    w.set_trigger_column(None)
    assert w.seconds_needed() == pytest.approx(2e-3)
    w.set_trigger_column(IDX_DIO4)
    # asking only for the screen would never find an edge to align on
    assert w.seconds_needed() >= 0.25


def test_screen_is_ten_by_eight_divisions_of_the_selected_scales(app):
    w = ScopeTraceWidget()
    w.set_time_per_div(50e-3)
    assert w.view_seconds == pytest.approx(0.5)
    w.set_time_per_div(37e-3)                 # snaps to the nearest 1-2-5 step
    assert w.time_per_div in (20e-3, 50e-3)


def _painted(w):
    """Render once so the widget caches the geometry hover hit-tests against."""
    from PySide6.QtGui import QImage
    img = QImage(w.width(), w.height(), QImage.Format_RGB32)
    w.render(img)
    return w


def _offset_snapshot():
    snap = _snapshot()
    snap.frames[:, 9] = int(0.8 / AI_VOLTS_PER_COUNT)     # Z Galvo parked at 0.8 V
    return snap


def test_hover_picks_the_trace_under_the_pointer(app):
    from PySide6.QtCore import QPointF

    w = ScopeTraceWidget()
    w.resize(860, 430)
    w.set_data(_offset_snapshot())
    _painted(w)
    plot = w._plot_rect()
    xs, ytop, _ybot, _vmin, _vmax = w._drawn[9]
    k = len(xs) // 2
    assert w.hover_hit(QPointF(float(xs[k]), float(ytop[k])), plot) == 9


def test_hover_returns_nothing_in_empty_space(app):
    from PySide6.QtCore import QPointF

    w = ScopeTraceWidget()
    w.resize(860, 430)
    w.set_data(_offset_snapshot())
    _painted(w)
    plot = w._plot_rect()
    xs, ytop, _ybot, _vmin, _vmax = w._drawn[9]
    k = len(xs) // 2
    empty = QPointF(float(xs[k]), float(ytop[k]) - 40)     # well clear of every trace
    assert w.hover_hit(empty, plot) is None


def test_hover_text_names_the_channel_its_scale_and_its_value(app):
    from PySide6.QtCore import QPointF

    w = ScopeTraceWidget()
    w.resize(860, 430)
    w.set_data(_offset_snapshot())
    _painted(w)
    plot = w._plot_rect()
    xs, ytop, _ybot, _vmin, _vmax = w._drawn[9]
    k = len(xs) // 2
    lines = w.hover_text(9, QPointF(float(xs[k]), float(ytop[k])), plot)
    assert lines[0] == "Z Galvo"
    assert "/div" in lines[1]
    assert "+0.800" in lines[2]
    assert lines[3].startswith("t ")


def test_hover_on_a_decimated_column_reports_a_range_not_a_point(app):
    """One pixel column of a 200 ms window holds 20 000 samples. A 100 us
    trigger pulse inside it spans 0 to 1.25 V; printing a single number there
    would invent precision the pixel does not have."""
    from PySide6.QtCore import QPointF

    w = ScopeTraceWidget()
    w.resize(860, 430)
    w.set_data(_snapshot())
    w.set_time_per_div(20e-3)
    _painted(w)
    plot = w._plot_rect()
    xs, ytop, ybot, vmin, vmax = w._drawn[IDX_DIO4]
    k = int(np.argmax(vmax - vmin))                  # a column straddling the pulse
    assert vmax[k] - vmin[k] > 1.0
    lines = w.hover_text(IDX_DIO4, QPointF(float(xs[k]), float(ytop[k])), plot)
    assert ".." in lines[2], f"expected a range, got {lines[2]!r}"


def _wheel(w, x, y, mod):
    from PySide6.QtCore import Qt, QPoint, QPointF
    from PySide6.QtGui import QWheelEvent
    w.wheelEvent(QWheelEvent(QPointF(x, y), QPointF(x, y), QPoint(0, 0), QPoint(0, 120),
                             Qt.NoButton, mod, Qt.NoScrollPhase, False))


def _click(w, x, y):
    from PySide6.QtCore import Qt, QPointF
    from PySide6.QtGui import QMouseEvent
    w.mousePressEvent(QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(x, y),
                                  Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))


def test_shift_wheel_keeps_the_voltage_under_the_pointer_on_screen(app):
    """Vertical zoom used to be anchored on a fixed 0.5 V centre while every
    signal of interest sits near 0 V, so below 200 mV/div the screen went
    blank. The voltage under the pointer must survive the zoom."""
    from PySide6.QtCore import Qt

    w = ScopeTraceWidget()
    w.resize(860, 430)
    w.set_data(_offset_snapshot())
    _painted(w)
    plot = w._plot_rect()
    c, target = 8, 0.0
    y = float(w._volts_to_y(c, target, plot))
    for _ in range(5):
        _wheel(w, plot.center().x(), y, Qt.ShiftModifier)
        _painted(w)
        assert plot.top() <= float(w._volts_to_y(c, target, plot)) <= plot.bottom()
    assert w.gain(c) <= 0.005


def test_dragging_does_not_move_the_waveform(app):
    """Where the trace sits is the trigger's and Fit's business. Dragging the
    picture around the screen made it easy to lose and hard to restore."""
    from PySide6.QtCore import Qt, QPointF
    from PySide6.QtGui import QMouseEvent

    w = ScopeTraceWidget()
    w.resize(860, 430)
    w.set_data(_offset_snapshot())
    _painted(w)
    before = (w._delay_s, dict(w._gain), dict(w._offset))
    for kind, pos in ((QMouseEvent.Type.MouseButtonPress, QPointF(400, 200)),
                      (QMouseEvent.Type.MouseMove, QPointF(700, 380)),
                      (QMouseEvent.Type.MouseButtonRelease, QPointF(700, 380))):
        w.event(QMouseEvent(kind, pos, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    assert (w._delay_s, dict(w._gain), dict(w._offset)) == before


def test_clicking_a_trace_pins_a_tag_that_stays_put(app):
    w = ScopeTraceWidget()
    w.resize(860, 430)
    w.set_data(_offset_snapshot())
    _painted(w)
    xs, ytop, _yb, _vm, _vx = w._drawn[9]
    k = len(xs) // 2
    _click(w, float(xs[k]), float(ytop[k]))
    assert [t["c"] for t in w.tags] == [9]
    # The anchor is a TIME, so it survives a change of timebase.
    t_before = w.tags[0]["t"]
    w.set_time_per_div(50e-3)
    _painted(w)
    assert w.tags[0]["t"] == t_before


def test_tag_buttons_change_only_their_own_channels_gain(app):
    w = ScopeTraceWidget()
    w.resize(860, 430)
    w.set_data(_offset_snapshot())
    _painted(w)
    xs, ytop, _yb, _vm, _vx = w._drawn[9]
    k = len(xs) // 2
    _click(w, float(xs[k]), float(ytop[k]))
    _painted(w)
    others = {c: w.gain(c) for c in w._visible_columns() if c != 9}
    before = w.gain(9)
    plus = next(r for i, kind, r in w._tag_hits if i == 0 and kind == "plus")
    _click(w, plus.center().x(), plus.center().y())
    assert w.gain(9) < before, "'+' should magnify, i.e. fewer volts per division"
    assert {c: w.gain(c) for c in w._visible_columns() if c != 9} == others


def test_tag_close_button_removes_it(app):
    w = ScopeTraceWidget()
    w.resize(860, 430)
    w.set_data(_offset_snapshot())
    _painted(w)
    xs, ytop, _yb, _vm, _vx = w._drawn[9]
    _click(w, float(xs[len(xs) // 2]), float(ytop[len(xs) // 2]))
    _painted(w)
    close = next(r for i, kind, r in w._tag_hits if i == 0 and kind == "close")
    _click(w, close.center().x(), close.center().y())
    assert w.tags == []


def test_a_tag_box_can_be_dragged_aside_without_moving_the_waveform(app):
    from PySide6.QtCore import Qt, QPointF
    from PySide6.QtGui import QMouseEvent

    w = ScopeTraceWidget()
    w.resize(860, 430)
    w.set_data(_offset_snapshot())
    _painted(w)
    xs, ytop, _yb, _vm, _vx = w._drawn[9]
    k = len(xs) // 2
    _click(w, float(xs[k]), float(ytop[k]))
    _painted(w)
    _i, box = w._tag_boxes[0]
    anchor_t = w.tags[0]["t"]
    view_before = (w._delay_s, dict(w._gain), dict(w._offset))

    start = box.center()
    w.mousePressEvent(QMouseEvent(QMouseEvent.Type.MouseButtonPress, start,
                                  Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    moved = QPointF(start.x() + 60, start.y() - 40)
    w.mouseMoveEvent(QMouseEvent(QMouseEvent.Type.MouseMove, moved,
                                 Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    w.mouseReleaseEvent(QMouseEvent(QMouseEvent.Type.MouseButtonRelease, moved,
                                    Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    _painted(w)

    assert w.tags[0]["dx"] == 60 and w.tags[0]["dy"] == -40
    _i, box_after = w._tag_boxes[0]
    assert box_after.center().x() > box.center().x()
    assert w.tags[0]["t"] == anchor_t, "dragging the box moved what it points at"
    assert (w._delay_s, dict(w._gain), dict(w._offset)) == view_before
    assert len(w.tags) == 1, "the drag press pinned a second tag"


# -- Geometry, probed independently of the widget's own cache -----------------
#
# The hover and tag tests read their probe point out of _drawn, the very cache
# the code under test computes, so they cannot see the whole picture sliding.
# These place a feature at a time WE choose and check the pixel it lands on.

def _spike_widget(t_per_div, fs=100_000.0, channel=8):
    """A widget showing one 1 V spike on the sample at the screen centre."""
    w = ScopeTraceWidget()
    w.resize(860, 430)
    w.set_enabled([channel])
    w.set_time_per_div(t_per_div)
    n = int(round(10 * t_per_div * fs)) * 3
    frames = np.zeros((n, len(AI_CHANNEL_NAMES)), dtype=np.int16)
    mk = lambda: ScopeSnapshot(frames=frames, fs_hz=fs, names=tuple(AI_CHANNEL_NAMES),
                               end_frame_index=n)
    w.set_data(mk())
    seg = w._visible_segment()
    frames[n - len(seg) + len(seg) // 2, channel] = int(1.0 / AI_VOLTS_PER_COUNT)
    w.set_data(mk())
    _painted(w)
    return w


@pytest.mark.parametrize("t_per_div", TIME_PER_DIV[:10])
def test_a_feature_at_the_screen_centre_is_drawn_at_the_centre(app, t_per_div):
    """m samples span m intervals, not m-1. Dividing by m-1 pinned the last
    sample ON the right edge while x_left was derived from len(seg)/fs, an
    EXCLUSIVE right edge -- stretching the trace by m/(m-1). That put the
    trigger edge 43.8 px (0.556 division) off the centre it is pinned to at
    10 us/div, and every one of the other tests passed throughout."""
    w = _spike_widget(t_per_div)
    xs, _ytop, _ybot, _vmin, vmax = w._drawn[8]
    x = float(xs[int(np.argmax(vmax))])
    off = x - w._plot_rect().center().x()
    assert abs(off) <= 1.0, f"{t_per_div * 1e6:g} us/div: spike drawn {off:+.2f} px off centre"


def test_the_hover_readout_agrees_with_the_drawn_geometry(app):
    """hover_text computes time from the continuous x mapping while the trace
    is drawn from the envelope index. If those two disagree the callout lies
    about the feature it is pointing at."""
    from PySide6.QtCore import QPointF

    w = _spike_widget(50e-6)
    xs, ytop, _ybot, _vmin, vmax = w._drawn[8]
    k = int(np.argmax(vmax))
    lines = w.hover_text(8, QPointF(float(xs[k]), float(ytop[k])), w._plot_rect())
    assert lines[-1].split()[1] == "0", f"spike at the centre reported as {lines[-1]!r}"


def _burst_snapshot(seconds=0.6, fs=100_000.0, slices=5, step_v=0.05, idle_after=0.05):
    """A Z-stack burst: `slices` triggers 100 ms apart, the AO block starting
    12 ms before each DIO4 edge with the Z piezo stepping per slice, then
    everything at 0 for `idle_after` (stop_free_run -> safe_state)."""
    n = int(seconds * fs)
    t = np.arange(n) / fs
    v = np.zeros((n, len(AI_CHANNEL_NAMES)))
    end = seconds - idle_after
    for k in range(slices):
        t_dio = end - (slices - k) * 0.1
        t_blk = t_dio - 0.012
        v[(t >= t_dio) & (t < t_dio + 1e-4), IDX_DIO4] = 1.25
        v[(t >= t_blk) & (t < t_blk + 0.088), 10] = step_v * k
    frames = np.clip(v / AI_VOLTS_PER_COUNT, -32768, 32767).astype(np.int16)
    return ScopeSnapshot(frames=frames, fs_hz=fs, names=tuple(AI_CHANNEL_NAMES), end_frame_index=n)


def _quiet_snapshot(seconds=0.6, fs=100_000.0):
    n = int(seconds * fs)
    return ScopeSnapshot(frames=np.zeros((n, len(AI_CHANNEL_NAMES)), dtype=np.int16), fs_hz=fs,
                         names=tuple(AI_CHANNEL_NAMES), end_frame_index=n)


def test_trigger_view_holds_the_last_burst_between_stack_passes(app):
    """A Z stack is a burst every few seconds. Between passes the fresh
    snapshot has no edge; the view must keep the last triggered frame (a
    scope's Normal trigger), not drop to a flat live line -- on the real FPGA
    that made a 0..200 mV piezo staircase look like nothing was driven."""
    w = ScopeTraceWidget()
    w.resize(860, 430)
    w.set_enabled([10, IDX_DIO4])
    w.set_trigger_column(IDX_DIO4)
    w.set_time_per_div(20e-3)
    w.set_data(_burst_snapshot())
    _painted(w)
    assert w._trig_info == "trig'd"
    top_before = float(w._drawn[10][4].max())
    assert top_before > 0.15                                  # the staircase is on screen

    w.set_data(_quiet_snapshot())                             # between passes: no edge at all
    _painted(w)
    assert w._trig_info == "trig'd (held)"
    assert 10 in w._drawn and float(w._drawn[10][4].max()) == pytest.approx(top_before)

    w.set_time_per_div(5e-3)                                  # zooming re-slices the held frame
    _painted(w)
    assert w._trig_info == "trig'd (held)"

    w.set_trigger_column(None)                                # free run: genuinely live again
    _painted(w)
    assert w._trig_info == ""
    assert float(w._drawn[10][4].max()) < 1e-3


def test_trigger_mode_asks_for_the_whole_buffer(app):
    w = ScopeTraceWidget()
    w.set_time_per_div(20e-3)
    w.set_trigger_column(IDX_DIO4)
    assert w.seconds_needed() >= 10.0      # the panel caps it at "# of seconds to buff"


# -- FpgaScopePanel: Source Hardware/Simulated (2026-09-07) ----------------------------
# The user's ask: a fully simulated view where no voltage leaves the FPGA card, without
# losing or endangering the real hardware scope. Hardware stays the default and every
# hardware-facing line of the panel is untouched; Simulated only ever reaches
# show_computed_waveform(), which touches self.trace/labels and nothing else. No
# separate Preview button (the user's own pushback, "why so many options" -- selecting
# Simulated IS the action, computed and shown immediately).

class _FakeLiveScope:
    """Just enough of FpgaScope's interface for _refresh() -- no hardware."""
    def __init__(self, snap):
        self._snap = snap
        self.running = True
        self.fs_hz = snap.fs_hz
        self.channels = len(snap.names)
        self.backlog_max = 0
        self.ai_error: dict = {}
        self.last_error = ""
        self.ring = type("R", (), {"total": len(snap.frames)})()

    def snapshot(self, seconds=None):
        return self._snap

    def seconds_buffered(self):
        return len(self._snap.frames) / self._snap.fs_hz if self._snap.fs_hz else 0.0

    def clear(self):
        pass


def test_scope_panel_defaults_to_hardware_source(app):
    assert FpgaScopePanel().source_combo.currentText() == "Hardware"


def test_selecting_simulated_emits_a_compute_request(app):
    """No separate button: picking Simulated from the combo IS the action.
    MainWindow (not under test here) is what actually computes and calls
    back via show_computed_waveform()."""
    p = FpgaScopePanel()
    got = []
    p.preview_requested.connect(lambda: got.append(1))
    p.source_combo.setCurrentText("Simulated")
    assert got == [1]


def test_simulated_source_ignores_the_live_scope(app):
    """Switching to Simulated must stop _refresh() from ever pulling in
    live data -- proves the two data paths cannot cross."""
    p = FpgaScopePanel()
    live = _FakeLiveScope(_quiet_snapshot())
    p.set_scope(live)                      # as MainWindow does on FPGA connect
    p._refresh()
    assert p.trace._snap is live._snap     # Hardware (default): live data shown, as before

    p.source_combo.setCurrentText("Simulated")
    hot = _burst_snapshot()
    live._snap = hot                       # the "live" scope now has fresh data
    p._refresh()                           # a timer tick while Simulated must be a no-op
    assert p.trace._snap is not hot

    preview = _quiet_snapshot(seconds=0.05)
    p.show_computed_waveform(preview)
    assert p.trace._snap is preview        # only show_computed_waveform can reach the screen here


def test_switching_back_to_hardware_resumes_the_live_view_immediately(app):
    p = FpgaScopePanel()
    live = _FakeLiveScope(_quiet_snapshot())
    p.set_scope(live)
    p.show_computed_waveform(_burst_snapshot())    # forces Simulated on its own
    p.source_combo.setCurrentText("Hardware")
    assert p.trace._snap is live._snap     # back immediately, not on the next 100 ms tick


def test_show_computed_waveform_never_touches_the_live_scope(app):
    """The structural safety property, at the panel level: showing a
    computed waveform must not read or write self._scope at all."""
    class _Landmine:
        def __getattr__(self, name):
            raise AssertionError(f"show_computed_waveform touched the live scope's .{name}")

    p = FpgaScopePanel()
    p._scope = _Landmine()                 # would raise on ANY attribute access
    p.show_computed_waveform(_quiet_snapshot(seconds=0.05))   # must not touch p._scope at all
    assert p.source_combo.currentText() == "Simulated"        # forces the mode too


def test_show_computed_waveform_widens_time_per_div_to_show_the_whole_buffer(app):
    """The actual bug the user hit: a Z stack's slow axes (Z Piezo included)
    are constant WITHIN one slice and only step BETWEEN slices -- correctly
    flat over any narrow timebase left over from viewing the live scope
    (or a previous zoom). fit_each_channel() only ever rescales volts, never
    the timebase (see its own docstring) -- showing a computed waveform
    must widen T/div itself, or every step looks like nothing happened."""
    p = FpgaScopePanel()
    p.tdiv_combo.setCurrentIndex(TIME_PER_DIV.index(20e-3))   # a narrow window, as if left from live viewing
    assert p.trace.time_per_div == pytest.approx(20e-3)

    n = int(2.0 * 10_000)                     # 2 s of computed buffer at 10 kHz -- several slices' worth
    frames = np.zeros((n, len(AI_CHANNEL_NAMES)), dtype=np.int16)
    frames[:, 10] = 1000                        # Z Piezo: nonzero, so it would be visible if shown at all
    snap = ScopeSnapshot(frames=frames, fs_hz=10_000.0, names=AI_CHANNEL_NAMES, end_frame_index=n)

    p.show_computed_waveform(snap)

    assert p.trace.time_per_div * HDIV >= 2.0 - 1e-9, "T/div must widen enough to show the whole buffer"
    assert p.tdiv_combo.currentData() == pytest.approx(p.trace.time_per_div)      # dropdown reflects it


def test_show_computed_waveform_switches_to_free_run(app):
    """A computed snapshot has no real Int Sync / DIO4 edges (those columns
    are 0 -- this is the AO side only); trigger-searching them finds
    nothing, so showing one must not leave a stale trigger selection over
    from Hardware mode."""
    p = FpgaScopePanel()
    p.trig_combo.setCurrentIndex(1)              # "D4 cam trig", Hardware mode's default
    p.show_computed_waveform(_quiet_snapshot(seconds=0.1))
    assert p.trig_combo.currentIndex() == 0      # "Free run"
    assert p.trace._trigger_col is None


def test_computed_waveform_ticks_whatever_carries_data(app):
    """The user's report: nothing on the screen, and the live laser's AOTF
    channel never shown. DEFAULT_ACTIVE hard-codes AOTF 0/1, but 488 is
    AOTF 2 on this rig -- so the channel with the level was unticked while
    two empty ones were ticked. Showing a computed waveform must tick
    whatever actually holds data."""
    p = FpgaScopePanel()
    for chk in p.channel_checks.values():        # start from nothing ticked at all
        chk.setChecked(False)

    n = 500
    frames = np.zeros((n, len(AI_CHANNEL_NAMES)), dtype=np.int16)
    frames[:, 10] = 1234                          # Z Piezo
    frames[:, 19] = 800                           # AOTF 2 -- the live laser, not in DEFAULT_ACTIVE
    snap = ScopeSnapshot(frames=frames, fs_hz=10_000.0, names=AI_CHANNEL_NAMES, end_frame_index=n)

    p.show_computed_waveform(snap)

    assert p.channel_checks[10].isChecked()
    assert p.channel_checks[19].isChecked(), "the live laser's AOTF channel must come up on its own"
    assert not p.channel_checks[16].isChecked(), "an empty channel is not ticked just because it is a default"


def test_computed_waveform_leaves_a_working_selection_completely_alone(app):
    """The user: "everytime I hit acquire the defaults displays are kicking
    in. I want only the waveforms I checked be displayed." So once a ticked
    channel has data, showing a computed waveform must not touch the
    selection at all -- not even additively."""
    p = FpgaScopePanel()
    for chk in p.channel_checks.values():
        chk.setChecked(False)
    p.channel_checks[10].setChecked(True)         # the user wants ONLY Z Piezo

    n = 300
    frames = np.zeros((n, len(AI_CHANNEL_NAMES)), dtype=np.int16)
    for col in (8, 9, 10, 11, 15, 19):            # plenty of other channels carry data
        frames[:, col] = 700
    snap = ScopeSnapshot(frames=frames, fs_hz=10_000.0, names=AI_CHANNEL_NAMES, end_frame_index=n)

    for _ in range(3):                            # repeated Acquires must not creep
        p.show_computed_waveform(snap)

    assert p.enabled_channels() == [10], f"selection was overridden: {p.enabled_channels()}"


def test_computed_waveform_never_unticks_the_users_own_picks(app):
    p = FpgaScopePanel()
    p.channel_checks[0].setChecked(True)          # AI0: user's own pick, no data in the snapshot
    n = 100
    frames = np.zeros((n, len(AI_CHANNEL_NAMES)), dtype=np.int16)
    frames[:, 8] = 500
    snap = ScopeSnapshot(frames=frames, fs_hz=10_000.0, names=AI_CHANNEL_NAMES, end_frame_index=n)
    p.show_computed_waveform(snap)
    assert p.channel_checks[0].isChecked(), "additive -- it must not clear what the user ticked"
    assert p.channel_checks[8].isChecked()


# -- Standard DSO display: one slot per channel (2026-09-07, user) ---------
# "can we switch to a standard digital scope display. I think it has too
# many custom display which is messing up the waveforms." Fit rescaled each
# channel but left every one of them centred on the SAME line, so six traces
# drew on top of one another. A scope gives each channel its own slot, its
# own volts/div and a numbered ground marker showing where its 0 V is.

def _short_buffer_widget(t_per_div=0.1, fs=10_000.0, n=2000):
    """A 0.2 s buffer shown in a 1 s window: 80% of the screen is blank."""
    w = ScopeTraceWidget()
    w.resize(860, 430)
    frames = np.zeros((n, len(AI_CHANNEL_NAMES)), dtype=np.int16)
    frames[:, 8] = (np.sin(np.linspace(0, 8 * np.pi, n)) * 800).astype(np.int16)
    frames[:, 10] = np.linspace(1500, 3000, n).astype(np.int16)      # a different offset
    w.set_enabled([8, 10])
    w.set_trigger_column(None)                                       # free run: nothing pins x
    w.set_time_per_div(t_per_div)
    w.set_data(ScopeSnapshot(frames=frames, fs_hz=fs, names=AI_CHANNEL_NAMES, end_frame_index=n))
    return _painted(w)


def _drawn_centre(w, c):
    """(x, y) of the middle of channel c's drawn extent, relative to the
    middle of the plot area -- 0, 0 means dead centre."""
    plot = w._plot_rect()
    xs, ytop, ybot, _mn, _mx = w._drawn[c]
    return ((float(xs.min()) + float(xs.max())) / 2 - plot.center().x(),
            (float(ytop.min()) + float(ybot.max())) / 2 - plot.center().y())


# -- a LIVE view keeps rolling against the right edge ----------------------
# Regression, same day: the horizontal centring latched onto the live sweep
# too, so an Acquire with a buffer shorter than the window left the trace
# parked mid-screen. "do you see that the real acquire button isn't
# displaying the signals on scope real time" -- it was, at 5.5M points with a
# rock-steady 127.0000 ms DIO4; it just no longer looked like it.

def _panel_with_live_scope(t_per_div=0.5, seconds=0.2):
    p = FpgaScopePanel()
    p.trace.resize(860, 430)
    p.trig_combo.setCurrentIndex(0)                     # free run
    p.trace.set_time_per_div(t_per_div)                 # 5 s window, 0.2 s of data
    snap = _quiet_snapshot(seconds=seconds)
    snap.frames[:, 8] = 600
    p.set_scope(_FakeLiveScope(snap))                   # calls _refresh() itself
    return p


# -- the screen must fill, whatever the stats window is set to -------------
# "do you not seeing the problem? The waveforms appear on the right hand
# side of the scope." The ring is a fixed 5 s and the widest timebase
# (0.5 s/div x 10) is a 5 s window, so the ring can ALWAYS fill the screen.
# _refresh was clamping the screen fetch to "# of seconds to buff" (2.00 by
# default), so at 500 ms/div the trace could only occupy the right-hand 40%.

def test_the_screen_fills_regardless_of_the_stats_window(app):
    p = FpgaScopePanel()
    p.trace.resize(860, 430)
    p.trig_combo.setCurrentIndex(0)                       # free run
    p.window_spin.setValue(2.0)                           # the shipped default
    p.trace.set_time_per_div(0.5)                         # 5 s window
    p.set_scope(_FakeLiveScope(_quiet_snapshot(seconds=5.0)))
    p._refresh()

    have_s = len(p.trace._snap.frames) / p.trace._snap.fs_hz
    assert have_s == pytest.approx(5.0, rel=0.01), \
        f"only {have_s:.2f} s pulled into a 5 s window -- the rest of the screen is blank"


def test_the_stats_window_still_honours_the_spinbox(app):
    """The spinbox keeps its own job -- how much history DIO4's period is
    measured over -- it just no longer starves the screen."""
    p = FpgaScopePanel()
    p.trig_combo.setCurrentIndex(0)
    p.trace.set_time_per_div(0.5)
    p.window_spin.setValue(0.25)                          # 2 trigger periods at 100 ms
    p.set_scope(_FakeLiveScope(_snapshot(seconds=2.0)))   # DIO4 pulses 100 ms apart
    p._refresh()
    assert "DIO4: 2 edges" in p.status_label.text(), p.status_label.text()

    p.window_spin.setValue(1.0)
    p._refresh()
    assert "DIO4: 10 edges" in p.status_label.text(), p.status_label.text()


def _drawn_band(w, c):
    """(top, bottom) pixel rows channel c's trace actually occupies."""
    _xs, ytop, ybot, _mn, _mx = w._drawn[c]
    return float(np.min(ytop)), float(np.max(ybot))


def test_autoset_gives_every_channel_its_own_slot(app):
    """The core of the change: traces must not pile up on one another."""
    w = _short_buffer_widget()
    w.set_enabled([8, 9, 10, 11])
    snap = w._snap
    snap.frames[:, 9] = 300                       # flat, and tiny
    snap.frames[:, 11] = np.linspace(-3000, 3000, len(snap.frames)).astype(np.int16)
    w.set_data(snap)
    _painted(w)
    w.autoset()
    _painted(w)

    bands = {c: _drawn_band(w, c) for c in (8, 9, 10, 11)}
    order = sorted(bands, key=lambda c: bands[c][0])
    assert order == [8, 9, 10, 11], f"slots are not in channel order: {order}"
    for a, b in zip(order, order[1:]):
        assert bands[a][1] <= bands[b][0] + 1.0, f"ch{a} and ch{b} overlap: {bands[a]} {bands[b]}"


def test_autoset_fills_each_slot_without_overflowing_it(app):
    w = _short_buffer_widget()
    w.set_enabled([8, 10])
    w.autoset()
    _painted(w)
    slot_px = w._plot_rect().height() / 2          # two channels -> half the screen each
    for c in (8, 10):
        top, bot = _drawn_band(w, c)
        assert (bot - top) <= slot_px + 1.0, f"ch{c} overflows its slot"
        assert (bot - top) >= slot_px * 0.4, f"ch{c} barely uses its slot ({bot - top:.0f}px)"


def test_slot_numbers_run_top_to_bottom(app):
    w = _short_buffer_widget()
    w.set_enabled([10, 8, 11])                     # any order in -- sorted on screen
    assert [w.slot_index(c) for c in (8, 10, 11)] == [1, 2, 3]
    centres = w.slot_centres()
    assert centres[8] > centres[10] > centres[11]  # divisions above centre are positive


def test_ground_markers_sit_at_each_channels_zero_volts(app):
    w = _short_buffer_widget()
    w.set_enabled([8, 10])
    w.autoset()
    _painted(w)
    plot = w._plot_rect()
    for c in (8, 10):
        y = float(w._volts_to_y(c, 0.0, plot))
        # the marker is drawn at 0 V for that channel, whatever its gain is
        assert y == pytest.approx(plot.center().y() - w.offset(c) * (plot.height() / 8))


def test_ticking_a_channel_re_deals_the_stack(app):
    """A slot depends on how many channels are up, so the others have to
    move over -- keeping their own gains."""
    w = _short_buffer_widget()
    w.set_enabled([8, 10])
    w.autoset()
    _painted(w)
    gains = {c: w.gain(c) for c in (8, 10)}
    offs = {c: w.offset(c) for c in (8, 10)}
    before = w.slot_centres()
    w.set_enabled([8, 10, 11])
    assert all(w.gain(c) == gains[c] for c in (8, 10)), "re-dealing must not rescale"
    after = w.slot_centres()
    assert after[8] != before[8] and after[10] != before[10]
    # each channel moved by exactly its slot's shift -- a delta, so anything
    # the user had nudged within the slot is carried along rather than reset
    for c in (8, 10):
        assert w.offset(c) - offs[c] == pytest.approx(after[c] - before[c])


def test_a_tag_button_scales_only_its_own_channel(app):
    w = _short_buffer_widget()
    w.set_enabled([8, 10])
    w.autoset()
    other = w.gain(10)
    w.step_gain(8, -1, from_tag=True)
    assert w.gain(10) == other, "a per-channel control moved another channel"


def test_the_screen_carries_no_volts_axis(app):
    """There is no single voltage per pixel row once every channel has its
    own scale, so the numbers up the side are gone -- the ground markers and
    the bar under the screen say where and at what scale each trace is."""
    w = _short_buffer_widget()
    assert not hasattr(w, "uniform_scale")
    assert w.LEFT < 40, "the left gutter is only wide enough for ground markers now"


def test_autoset_survives_a_flat_channel(app):
    """AOTF lines sit at a constant 0 V under the simulate-on-FPGA clamp;
    autoset must place them, not divide by zero."""
    w = _short_buffer_widget()
    snap = w._snap
    snap.frames[:, 12] = 0
    w.set_enabled([8, 12])
    w.set_data(snap)
    _painted(w)
    w.autoset()
    _painted(w)
    top, bot = _drawn_band(w, 12)
    plot = w._plot_rect()
    assert plot.top() <= top <= bot <= plot.bottom()
    assert w.gain(12) in VOLTS_PER_DIV


def test_double_click_autosets(app):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    w = _short_buffer_widget()
    w.set_enabled([8, 10])
    w.set_gain(8, 5.0)
    w.set_gain(10, 5.0)
    pos = QPointF(w._plot_rect().center())
    w.mouseDoubleClickEvent(QMouseEvent(QMouseEvent.Type.MouseButtonDblClick, pos,
                                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    _painted(w)
    assert w.gain(8) < 5.0 and w.gain(10) < 5.0
    assert _drawn_band(w, 8)[1] <= _drawn_band(w, 10)[0] + 1.0


def test_autoset_button_is_the_only_scaling_button(app):
    p = FpgaScopePanel()
    assert hasattr(p, "autoset_btn")
    for gone in ("fit_btn", "center_btn", "perch_chk"):
        assert not hasattr(p, gone), f"{gone} should be gone"


def test_a_flat_dc_channel_gets_a_real_volts_per_div(app):
    """A channel parked at 0.8 V has no peak-to-peak at all. Scaling on p-p
    alone picked the smallest gain in the list, 1 mV/div -- a meaningless
    readout that also put the channel's ground marker 800 divisions off
    screen. |midpoint| has to count towards the scale too."""
    w = _short_buffer_widget()
    snap = w._snap
    snap.frames[:, 9] = int(0.8 / AI_VOLTS_PER_COUNT)      # Z galvo parked, dead flat
    w.set_enabled([8, 9])
    w.set_data(snap)
    _painted(w)
    w.autoset()
    _painted(w)

    assert w.gain(9) >= 0.1, f"a 0.8 V DC line was scaled to {w.gain(9)} V/div"
    # the real invariant: the level is representable within half a screen of
    # its own baseline, so "0.8 V at 200 mV/div" is a reading you can do in
    # your head. (Where 0 V then lands can still be off screen when the slot
    # is near an edge -- the marker parks on that edge, hollow, as a scope's
    # does; it is not dropped.)
    assert abs(0.8 / w.gain(9)) <= VDIV / 2


def test_the_trace_still_lands_in_its_slot_whatever_its_dc_level(app):
    """The DC level changes the SCALE, never the placement: offset absorbs
    it, so every channel is drawn on its own slot line regardless."""
    w = _short_buffer_widget()
    snap = w._snap
    snap.frames[:, 9] = int(0.8 / AI_VOLTS_PER_COUNT)
    snap.frames[:, 11] = int(-2.5 / AI_VOLTS_PER_COUNT)
    w.set_enabled([8, 9, 11])
    w.set_data(snap)
    _painted(w)
    w.autoset()
    _painted(w)
    plot = w._plot_rect()
    px_per_div = plot.height() / 8
    for c in (9, 11):
        top, bot = _drawn_band(w, c)
        want = plot.center().y() - w.slot_centres()[c] * px_per_div
        assert (top + bot) / 2 == pytest.approx(want, abs=1.5)


# -- Overlay / Reset (2026-09-07, user) ------------------------------------
# "add a widget that centers and overlays the waveforms. Also add a reset
# button that shows the waveforms as shown by a standard oscilloscope."
# Two presentations of the same data: stacked (the scope default, one slot
# per channel) and overlaid (everything on the centre line, for comparing
# an edge on one channel against an edge on another).

def test_overlay_puts_every_channel_on_the_centre_line(app):
    w = _short_buffer_widget()
    w.set_enabled([8, 10, 11])
    w.autoset()
    _painted(w)
    stacked = {c: _drawn_band(w, c) for c in (8, 10, 11)}
    assert stacked[8][1] <= stacked[10][0] + 1.0            # separated, to start with

    w.set_overlay(True)
    _painted(w)
    cy = w._plot_rect().center().y()
    for c in (8, 10, 11):
        top, bot = _drawn_band(w, c)
        assert (top + bot) / 2 == pytest.approx(cy, abs=2.0), f"ch{c} is not centred"


def test_overlay_makes_the_traces_actually_overlap(app):
    """The point of the mode: the bands have to share screen, or nothing is
    being compared."""
    w = _short_buffer_widget()
    w.set_enabled([8, 10])
    w.set_overlay(True)
    _painted(w)
    (t8, b8), (t10, b10) = _drawn_band(w, 8), _drawn_band(w, 10)
    assert min(b8, b10) - max(t8, t10) > 0, "the traces do not overlap at all"


def test_overlay_removes_the_dc_level(app):
    """Two channels at very different DC levels must still land on top of
    one another -- the level is exactly what you are ignoring here."""
    w = _short_buffer_widget()
    snap = w._snap
    snap.frames[:, 9] = int(0.8 / AI_VOLTS_PER_COUNT)
    snap.frames[:, 11] = int(-2.5 / AI_VOLTS_PER_COUNT)
    w.set_enabled([9, 11])
    w.set_data(snap)
    w.set_overlay(True)
    _painted(w)
    assert _drawn_band(w, 9)[0] == pytest.approx(_drawn_band(w, 11)[0], abs=2.0)


def test_reset_returns_the_standard_scope_view(app):
    w = _short_buffer_widget()
    w.set_enabled([8, 10, 11])
    w.set_overlay(True)
    w.set_gain(8, 5.0)                                   # and a hand adjustment on top
    _painted(w)
    w.reset_view()
    _painted(w)

    assert w.overlay is False
    bands = {c: _drawn_band(w, c) for c in (8, 10, 11)}
    order = sorted(bands, key=lambda c: bands[c][0])
    assert order == [8, 10, 11]
    for a, b in zip(order, order[1:]):
        assert bands[a][1] <= bands[b][0] + 1.0, "traces still overlap after Reset"
    assert w.gain(8) != 5.0, "Reset kept a hand-set volts/div"


def test_reset_leaves_the_measurement_settings_alone(app):
    """It is a display reset. Wiping the timebase, the trigger or the ticked
    channels is the defaults-kicking-in behaviour the user objected to."""
    w = _short_buffer_widget()
    w.set_enabled([8, 10])
    w.set_time_per_div(5e-3)
    w.set_trigger_column(IDX_DIO4)
    w.reset_view()
    assert w.time_per_div == 5e-3
    assert w._trigger_col == IDX_DIO4
    assert w._visible_columns() == [8, 10]


def test_the_readout_says_when_it_is_overlaid(app):
    """Traces piled on one another look like a fault unless the screen says
    it is a mode."""
    w = _short_buffer_widget()
    assert "OVERLAY" not in w.status_text(0.0)
    w.set_overlay(True)
    assert w.status_text(0.0).startswith("OVERLAY")
    w.set_held(True)
    assert "OVERLAY" in w.status_text(0.0) and "HOLD" in w.status_text(0.0)
    w.reset_view()
    assert "OVERLAY" not in w.status_text(0.0)


def test_the_buttons_stay_in_step_with_the_mode(app):
    p = FpgaScopePanel()
    p.trace.resize(860, 430)
    p.trace.set_data(_quiet_snapshot(seconds=0.5))
    p.overlay_btn.setChecked(True)
    assert p.trace.overlay is True
    p.reset_btn.click()
    assert p.trace.overlay is False
    assert p.overlay_btn.isChecked() is False, "the button lied about the mode"
