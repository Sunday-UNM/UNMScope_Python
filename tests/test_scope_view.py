"""Pure-logic test for the Waveforms tab's decimation (no Qt display)."""
import numpy as np

from unmscope.gui.scope_view import envelope


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
