"""Trigger-active mode accounting on the Camera base class (via
SimulatedCamera -- no hardware). The numbers mirror what was measured on
the Orca 2026-09-03 (spikes/19_free_run_trigger.py)."""
import pytest

from unmscope.hardware.camera import Camera, SimulatedCamera


def _cam(exposure_ms=100.0):
    cam = SimulatedCamera()
    cam.connect()
    cam.set_exposure_ms(exposure_ms)
    return cam


def test_default_mode_is_edge():
    cam = _cam()
    assert cam.trigger_active == Camera.TRIGGER_EDGE
    assert cam.closing_triggers() == 0


def test_prepare_sequence_starts_and_discards_nothing():
    cam = _cam()
    assert not cam.is_sequence_running()
    assert cam.prepare_sequence() == 0
    assert cam.is_sequence_running()
    cam.set_trigger_active(Camera.TRIGGER_SYNCREADOUT)
    assert cam.prepare_sequence() == 0      # fresh/restarted sequence: no garbage frame
    assert cam.is_sequence_running()


def test_edge_period_is_exposure_plus_readout_plus_margin():
    cam = _cam(100.0)
    assert cam.min_frame_period_ms() == pytest.approx(100.0 + cam.READOUT_MS)
    assert cam.trigger_period_ms(100.0) == pytest.approx(100.0 + cam.READOUT_MS + cam.edge_margin_ms())
    # 10 lines + safety, with 1H = readout / (2048/2)
    assert cam.edge_margin_ms() == pytest.approx(10 * cam.READOUT_MS / 1024 + Camera.SAFETY_MARGIN_MS)


def test_syncreadout_period_is_the_exposure_above_the_floor():
    cam = _cam(100.0)
    cam.set_trigger_active(Camera.TRIGGER_SYNCREADOUT)
    assert cam.trigger_active == Camera.TRIGGER_SYNCREADOUT
    assert cam.min_frame_period_ms() == pytest.approx(cam.READOUT_MS)
    assert cam.trigger_period_ms(100.0) == pytest.approx(100.0)          # frame time = exposure
    assert cam.closing_triggers() == 1                                   # frame n comes out on edge n+1


def test_syncreadout_lengthens_exposures_below_the_floor():
    cam = _cam(5.0)
    cam.set_trigger_active(Camera.TRIGGER_SYNCREADOUT)
    floor = cam.READOUT_MS + cam.syncreadout_margin_ms()
    assert cam.syncreadout_margin_ms() == pytest.approx(18 * cam.READOUT_MS / 1024 + Camera.SAFETY_MARGIN_MS)
    assert cam.trigger_period_ms(5.0) == pytest.approx(floor)
    assert cam.trigger_period_ms(floor + 1.0) == pytest.approx(floor + 1.0)


def test_unknown_mode_rejected():
    cam = _cam()
    with pytest.raises(ValueError):
        cam.set_trigger_active("LEVEL")
    assert cam.trigger_active == Camera.TRIGGER_EDGE


def test_bounded_trigger_count_includes_closing_trigger():
    cam = _cam()
    cam.set_trigger_active(Camera.TRIGGER_SYNCREADOUT)
    n_frames = 10
    assert n_frames + cam.closing_triggers() == 11
    cam.set_trigger_active(Camera.TRIGGER_EDGE)
    assert n_frames + cam.closing_triggers() == 10
