"""SimulatedCamera driven by external edges ("simulate on FPGA"), mirroring
the Orca behaviour measured in spikes/24. No hardware."""
import pytest

from unmscope.hardware.camera import Camera, CameraError, SimulatedCamera


def _armed(mode):
    cam = SimulatedCamera(width=8, height=8)
    cam.connect()
    cam.set_trigger_source("EXTERNAL")
    cam.set_trigger_active(mode)
    cam.start_sequence(None)
    return cam


def test_simulated_camera_does_not_react_to_dio4_itself():
    assert SimulatedCamera.reacts_to_dio4 is False


def test_edge_mode_one_frame_per_edge():
    cam = _armed(Camera.TRIGGER_EDGE)
    assert cam.remaining_image_count() == 0
    assert cam.external_trigger(5) == 5
    assert cam.remaining_image_count() == 5
    for _ in range(5):
        cam.pop_image()
    assert cam.remaining_image_count() == 0
    with pytest.raises(CameraError):
        cam.pop_image()


def test_syncreadout_fresh_camera_gives_edges_minus_one():
    cam = _armed(Camera.TRIGGER_SYNCREADOUT)
    assert cam.external_trigger(5) == 4          # first edge only starts an exposure
    assert cam.remaining_image_count() == 4


def test_syncreadout_open_exposure_survives_restart_and_comes_out_first():
    cam = _armed(Camera.TRIGGER_SYNCREADOUT)
    cam.external_trigger(11)                     # a 10-slice run: 11 edges -> 10 frames
    cam.discard_buffered_frames()
    cam.stop_sequence()
    cam.start_sequence(None)                     # like the Orca, this does NOT clear the open exposure
    assert cam.external_trigger(11) == 11        # garbage frame + 10 real ones
    cam.connect()                                # a fresh device does
    cam.set_trigger_source("EXTERNAL")
    cam.set_trigger_active(Camera.TRIGGER_SYNCREADOUT)
    cam.start_sequence(None)
    assert cam.external_trigger(11) == 10


def test_edges_ignored_when_internal_or_not_running():
    cam = SimulatedCamera(width=8, height=8)
    cam.connect()
    cam.set_trigger_source("EXTERNAL")
    assert cam.external_trigger(3) == 0          # no sequence yet
    cam.set_trigger_source("INTERNAL")
    cam.start_sequence(None)
    assert cam.external_trigger(3) == 0          # internal pacing instead
