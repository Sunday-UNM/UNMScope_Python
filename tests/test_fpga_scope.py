"""Pure-logic tests for the FPGA Scope decoder (no hardware)."""
import numpy as np
import pytest

from unmscope.hardware.fpga_scope import (
    AI_CHANNEL_NAMES, DIGITAL_TRUE, IDX_DIO4, IDX_INT_SYNC, FrameRing, ScopeSnapshot,
    digital_edges, measure_period,
)


def _pulse_train(n_samples, period, high, offset=0, level=DIGITAL_TRUE):
    col = np.zeros(n_samples, dtype=np.int16)
    for start in range(offset, n_samples, period):
        col[start:start + high] = level
    return col


def test_channel_map_matches_decode_run():
    assert AI_CHANNEL_NAMES[IDX_INT_SYNC] == "Int Sync (Cycle Only)"
    assert AI_CHANNEL_NAMES[IDX_DIO4] == "Cam Ext Trigger Out (DIO4)"
    # columns 16-28 per HHMI - FPGA AI Loop.vi's Build Array (wire-traced 2026-09-05)
    assert AI_CHANNEL_NAMES[16:24] == ("AOTF0", "AOTF1", "Perfusion?", "AOTF2", "AOTF3", "AOTF4", "AOTF5", "AOTF6")
    assert AI_CHANNEL_NAMES[24] == "Shutter" and AI_CHANNEL_NAMES[28] == "Channel Shutter 3"
    assert len(AI_CHANNEL_NAMES) == 29


def test_edges_and_period_of_a_100us_pulse_at_20ks():
    fs = 20_000.0                       # 50 us samples
    col = _pulse_train(60_000, period=2000, high=2, offset=7)   # 100 ms period, 100 us high
    rising, falling = digital_edges(col)
    assert len(rising) == 30 and len(falling) == 30
    st = measure_period(col, fs)
    assert st.edges == 30
    assert st.period_ms == pytest.approx(100.0)
    assert st.period_sd_ms == 0.0
    assert st.high_ms == pytest.approx(0.1)
    assert st.duty == pytest.approx(60 / 60_000)
    assert st.hz == pytest.approx(10.0)
    assert st.sample_ms == pytest.approx(0.05)


def test_measure_period_needs_two_edges():
    assert measure_period(_pulse_train(1000, period=5000, high=2), 20_000.0) is None


def test_frame_ring_wraps_and_returns_newest_first_in_order():
    ring = FrameRing(capacity=5, channels=2)
    ring.append(np.arange(6).reshape(3, 2))            # rows 0,1,2
    assert ring.total == 3
    np.testing.assert_array_equal(ring.latest(), np.arange(6).reshape(3, 2))
    ring.append(np.arange(6, 14).reshape(4, 2))         # rows 3..6 -> wraps, keeps 2..6
    assert ring.total == 7
    out = ring.latest()
    assert out.shape == (5, 2)
    np.testing.assert_array_equal(out[0], [4, 5])       # oldest kept row is row 2
    np.testing.assert_array_equal(out[-1], [12, 13])
    np.testing.assert_array_equal(ring.latest(2), np.array([[10, 11], [12, 13]]))
    assert ring.latest(0).shape == (0, 2)


def test_frame_ring_append_larger_than_capacity_keeps_newest():
    ring = FrameRing(capacity=3, channels=1)
    ring.append(np.arange(10).reshape(10, 1))
    np.testing.assert_array_equal(ring.latest().ravel(), [7, 8, 9])
    assert ring.total == 3


def test_snapshot_accessors():
    frames = np.zeros((4, 16), dtype=np.int16)
    frames[:, 0] = 3277                                 # ~1.0 V
    frames[1, IDX_DIO4] = DIGITAL_TRUE
    snap = ScopeSnapshot(frames=frames, fs_hz=20_000.0, names=AI_CHANNEL_NAMES[:16], end_frame_index=4)
    assert snap.analog_volts(0)[0] == pytest.approx(1.0, abs=1e-3)
    assert snap.digital(IDX_DIO4).tolist() == [False, True, False, False]
    assert snap.column("Cam Ext Trigger Out (DIO4)")[1] == DIGITAL_TRUE
    np.testing.assert_allclose(snap.t_s, [0, 5e-5, 1e-4, 1.5e-4])
