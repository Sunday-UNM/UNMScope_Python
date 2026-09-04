"""Pure-logic tests for the free-run timing helpers (no hardware)."""
import pytest

from unmscope.hardware.fpga_trigger import (
    MIN_TRIGGER_UP_TICKS, TICKS_PER_S, FpgaTriggerError, FreeRunStatus, free_run_timing,
)


def test_cycle_ticks_is_the_period():
    cycle, _ = free_run_timing(0.1097, 0.100)
    assert cycle == round(0.1097 * TICKS_PER_S) == 4_388_000


def test_trigger_up_is_half_the_gap_like_labview():
    # LabVIEW: Trigger up = (cycle - exposure) / 2
    cycle, up = free_run_timing(0.1338, 0.100)
    assert up == round((0.1338 - 0.100) / 2 * TICKS_PER_S)
    assert MIN_TRIGGER_UP_TICKS <= up <= cycle - MIN_TRIGGER_UP_TICKS


def test_trigger_up_never_equals_cycle():
    # Equal values were the original 1-2-pulses-then-nothing race.
    cycle, up = free_run_timing(0.020, 0.020)   # zero gap
    assert up == MIN_TRIGGER_UP_TICKS
    assert up < cycle


@pytest.mark.parametrize("period_s, exposure_s", [
    (0.00025, 0.0),      # tiny cycle (10,000 ticks): clamps bite
    (0.010, 0.0),        # zero exposure
    (0.1338, 0.100),     # the real Orca case
    (2.0, 1.999),        # long exposure, tiny gap
])
def test_trigger_up_always_inside_the_cycle(period_s, exposure_s):
    cycle, up = free_run_timing(period_s, exposure_s)
    assert MIN_TRIGGER_UP_TICKS <= up <= cycle - MIN_TRIGGER_UP_TICKS


def test_period_too_short_raises():
    with pytest.raises(FpgaTriggerError):
        free_run_timing(0.0001, 0.0)


def _status(t, t_last, n):
    return FreeRunStatus(t=t, t_last_trigger=t_last, triggers_read=n, triggers_ignored=0,
                         ao_generated=n, ao_points_left=0, ao_waveform_state=1,
                         ao_wvfrm_ready=True, ao_dma_error={}, int_cycle_mismatches=0,
                         int_cycle_samples=0, fifo_empty_remaining=0,
                         refill_words_written=0, refill_timeouts=0)


def test_achieved_hz_uses_fencepost_and_last_trigger_time():
    # 10 triggers at 0, P, ..., 9P with P = 0.1 s -> last at 0.9 s; now is 1.05 s.
    st = _status(t=1.05, t_last=0.9, n=10)
    assert st.achieved_hz == pytest.approx(10.0)


def test_achieved_hz_zero_until_two_triggers():
    assert _status(t=0.5, t_last=0.0, n=1).achieved_hz == 0.0
    assert _status(t=0.5, t_last=0.0, n=0).achieved_hz == 0.0
