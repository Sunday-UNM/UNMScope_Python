"""The Waveforms tab: decimation, axis labelling and trigger alignment."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from unmscope.gui.scope_view import (  # noqa: E402
    HDIV, TIME_PER_DIV, FpgaScopePanel, ScopeTraceWidget, eng_time, eng_volts, envelope, time_axis_label,
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


def test_axis_drops_volts_once_the_gains_differ(app):
    """With per-channel gain there is no single voltage per pixel row, so a
    Volts axis would be lying about every trace it does not belong to."""
    w = ScopeTraceWidget()
    w.resize(860, 430)
    w.set_data(_offset_snapshot())
    _painted(w)
    assert w.uniform_scale() is not None            # all channels start equal
    w.set_gain(9, 0.05)
    assert w.uniform_scale() is None


def test_fit_each_channel_equalises_amplitudes(app):
    """The point of per-channel gain: two waveforms of very different size
    should end up filling comparable amounts of the screen."""
    w = ScopeTraceWidget()
    w.resize(860, 430)
    snap = _snapshot()
    n = len(snap.frames)
    t = np.arange(n) / snap.fs_hz
    snap.frames[:, 8] = (0.02 * np.sin(2 * np.pi * 50 * t) / AI_VOLTS_PER_COUNT).astype(np.int16)
    snap.frames[:, 11] = (0.90 * np.sin(2 * np.pi * 50 * t) / AI_VOLTS_PER_COUNT).astype(np.int16)
    w.set_enabled([8, 11])
    w.set_data(snap)
    _painted(w)
    w.fit_each_channel()
    _painted(w)
    heights = {}
    for c in (8, 11):
        _xs, ytop, ybot, _vm, _vx = w._drawn[c]
        heights[c] = float(ybot.max() - ytop.min())
    ratio = max(heights.values()) / max(1e-9, min(heights.values()))
    assert ratio < 2.5, f"45x amplitude difference still renders {ratio:.1f}x apart: {heights}"


def test_shared_mode_keeps_one_scale_for_everything(app):
    """The mode is an invariant: in shared mode a tag's +/- must move every
    channel, or the axis would silently stop being able to show volts."""
    w = ScopeTraceWidget()
    w.resize(860, 430)
    w.set_data(_offset_snapshot())
    _painted(w)
    assert w.per_channel_scale is True                # independent by default
    assert w.uniform_scale() is not None
    w.set_per_channel_scale(False)                   # gang them for this test
    w.step_gain(9, -1)
    assert w.uniform_scale() is not None, "one channel drifted off the shared scale"


def test_leaving_custom_mode_collapses_to_the_coarsest_gain(app):
    """Nothing that was on screen may fall off it on the way back."""
    w = ScopeTraceWidget()
    w.resize(860, 430)
    w.set_per_channel_scale(True)
    w.set_data(_offset_snapshot())
    _painted(w)
    w.set_gain(8, 0.005)
    w.set_gain(9, 0.5)
    assert w.uniform_scale() is None
    w.set_per_channel_scale(False)
    uniform = w.uniform_scale()
    assert uniform is not None and uniform[0] == 0.5


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


def test_enabling_a_channel_keeps_the_shared_scale_intact(app):
    """In shared mode a newly ticked channel arriving on the default gain
    would break the invariant and flip the axis to Divisions unannounced."""
    w = ScopeTraceWidget()
    w.resize(860, 430)
    w.set_enabled([8, 11])
    w.set_per_channel_scale(False)
    w.set_data(_offset_snapshot())
    _painted(w)
    w.step_gain(8, -2)                       # move the shared scale off default
    assert w.uniform_scale() is not None
    w.set_enabled([8, 11, 9])                # tick another legend box
    assert w.uniform_scale() is not None, "the new channel broke the shared scale"
    assert w.gain(9) == w.gain(8)


def test_a_tag_button_scales_only_its_own_waveform_even_when_ganged(app):
    """The tag buttons look per-waveform, so they must behave that way: using
    one while the scales are ganged switches to independent scales rather than
    quietly dragging every other trace along with it."""
    w = ScopeTraceWidget()
    w.resize(860, 430)
    w.set_enabled([8, 11])
    w.set_per_channel_scale(False)
    w.set_data(_offset_snapshot())
    _painted(w)
    before_other = w.gain(11)
    xs, ytop, _yb, _vm, _vx = w._drawn[8]
    _click(w, float(xs[len(xs) // 2]), float(ytop[len(xs) // 2]))
    _painted(w)
    plus = next(r for i, kind, r in w._tag_hits if i == 0 and kind == "plus")
    _click(w, plus.center().x(), plus.center().y())
    assert w.per_channel_scale is True, "the tag button did not switch to independent scales"
    assert w.gain(11) == before_other, "the other waveform moved too"


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
