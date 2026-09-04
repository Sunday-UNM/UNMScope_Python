"""The fake FPGA drives the REAL controller and scope code paths (no hardware)."""
import time

import numpy as np
import pytest

from unmscope.hardware.fake_fpga import FakeFpgaTriggerController
from unmscope.hardware.fpga_scope import IDX_DIO4, IDX_INT_SYNC, FpgaScope
from unmscope.hardware.waveform import build_scan_waveform


@pytest.fixture
def ctrl():
    c = FakeFpgaTriggerController()
    c.connect()
    yield c
    c.close()


def test_defaults_come_from_the_recorded_bitfile(ctrl):
    regs = ctrl._session.registers
    assert regs["AO Mode"].read() == 2
    assert regs["Trigger Look for Arm?"].read() is False
    assert regs["AO Limit Max (counts)"].read()["X Galvo"] == 32767


def test_bounded_run_fires_exactly_n_and_reports_counts(ctrl):
    counts = []
    ok = ctrl.start_free_run(0.020, 0.010, n_triggers=5, on_trigger_count=counts.append)
    assert ok and ctrl.free_run_active
    time.sleep(0.3)
    assert ctrl.last_status.triggers_read == 5
    final = ctrl.stop_free_run()
    assert final == 5 and counts[-1] == 5 and not ctrl.free_run_active
    assert ctrl._session.registers["# of triggers read"].read() == 0    # resets on disarm, like the FPGA


def test_continuous_run_rate_matches_cycle(ctrl):
    ok = ctrl.start_free_run(0.020, 0.010)
    assert ok
    time.sleep(0.5)
    final = ctrl.stop_free_run()
    assert 20 <= final <= 27        # 0.5 s / 20 ms, first edge at arm


def test_arm_refused_when_clamp_not_verified_is_not_triggered_by_fake(ctrl):
    ok = ctrl.start_free_run(0.020, 0.010, n_triggers=2, clamp_ao=True)
    assert ok and ctrl.ao_clamped
    ctrl.stop_free_run()


def test_scope_streams_and_sees_dio4_and_int_sync(ctrl):
    scope = FpgaScope(ctrl, channels=16, period_ticks=2000, buffer_seconds=2.0)   # 20 kS/s
    scope.start()
    ok = ctrl.start_free_run(0.050, 0.030)                    # Int Sync high = 10 ms
    assert ok
    time.sleep(0.6)
    ctrl.stop_free_run()
    time.sleep(0.05)
    st = scope.trigger_stats()
    scope.stop()
    assert st is not None and st.edges >= 8
    assert st.period_ms == pytest.approx(50.0, abs=1.0)
    assert st.high_ms == pytest.approx(0.1, abs=0.06)          # 4000 ticks, quantised to 50 us samples
    snap = scope.snapshot()
    sync = snap.frames[:, IDX_INT_SYNC] > 2048
    assert 0.15 < sync.mean() < 0.25                            # ~10 ms of 50 ms high


def test_waveform_words_show_up_on_the_fake_ao_columns(ctrl):
    wf = build_scan_waveform(0.010, x_range_v=0.2, n_slices=3, z_galvo_step_v=0.1, ticks_between_points=4000)
    scope = FpgaScope(ctrl, channels=16, period_ticks=2000, buffer_seconds=2.0)
    scope.start()
    ok = ctrl.start_free_run(0.020, 0.010, n_triggers=3, ao_points_per_trigger=wf.points_per_trigger,
                             ao_ticks_between_points=4000, ao_words=wf.words, clamp_ao=True, clamp_counts=2000)
    assert ok
    time.sleep(0.25)
    ctrl.stop_free_run()
    time.sleep(0.05)
    snap = scope.snapshot()
    scope.stop()
    z = np.unique(snap.frames[:, 9])                            # Z Galvo column: 0, 0.1 V, 0.2 V steps (clamped to 2000)
    assert set(z.tolist()) >= {0, 328, 655} or set(z.tolist()) >= {0, 327, 655}
    assert snap.frames[:, 8].max() <= 2000 and snap.frames[:, 8].min() >= -2000


def test_aotf_level_written_at_arm_and_zeroed_at_stop(ctrl):
    """Measured on the deployed bitfile (spikes/32): 'AOTF ch out (V)' follows
    'AOTF ch (V)' as a DC level while a run is armed with the gate permitted."""
    regs = ctrl._session.registers
    ok = ctrl.start_free_run(0.020, 0.010, n_triggers=3, aotf_levels={2: 16384})   # 488 nm row -> ch 2 = 5 V
    assert ok
    assert ctrl.aotf_levels["AOTF ch 2"] == 16384 and ctrl.aotf_levels["AOTF ch 0"] == 0
    assert regs["AOTF ch out (V)"].read()["AOTF ch 2"] == 16384
    assert ctrl.aotf_gate_allowed is True
    ctrl.stop_free_run()
    assert regs["AOTF ch (V)"].read()["AOTF ch 2"] == 0                              # safe_state zeroes
    assert regs["AOTF ch out (V)"].read()["AOTF ch 2"] == 0


def test_clamped_run_forces_aotf_off(ctrl):
    regs = ctrl._session.registers
    ok = ctrl.start_free_run(0.020, 0.010, n_triggers=2, clamp_ao=True, aotf_levels={0: 16384})
    assert ok and ctrl.ao_clamped
    assert regs["AOTF ch (V)"].read()["AOTF ch 0"] == 0                              # level forced to 0
    assert regs["AO Limit Max (counts)"].read()["AOTF on?"] is False                 # and the gate is not permitted
    assert regs["AOTF ch out (V)"].read()["AOTF ch 0"] == 0
    ctrl.stop_free_run()
