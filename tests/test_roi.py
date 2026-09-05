"""Camera ROI rules ported from LouisXIV's DCAM VIs, and the camera backends'
ROI / binning / sensor-mode behaviour on the simulated camera."""
import pytest

from unmscope.hardware.camera import SimulatedCamera
from unmscope.hardware.roi import (
    Roi, adjust_roi_for_pixels, binning_from_xy, center_roi, center_roi_at, centered_roi,
    coerce_roi, fov_um, full_roi, pixels_for_um, roi_from_subarray, subarray_from_roi,
)


def test_roi_is_one_based_inclusive():
    r = full_roi(2048, 2048)
    assert (r.left, r.top, r.right, r.bottom) == (1, 1, 2048, 2048)
    assert r.width == 2048 and r.height == 2048
    assert subarray_from_roi(r) == (0, 0, 2048, 2048)          # DCAM 0-based hpos/vpos/hsize/vsize
    assert roi_from_subarray(0, 0, 2048, 2048) == r


def test_coerce_snaps_position_down_to_the_unit_and_size_down_to_the_unit():
    # Coerce ROI: Left/Top to the nearest position unit <= desired
    # Coerce ROI size (Down): size rounded down to the size unit
    r = coerce_roi(Roi(left=6, top=7, right=1030, bottom=1031), 2048, 2048)
    assert (r.left, r.top) == (5, 5)                            # 0-based 5,6 -> snapped to 4 -> 1-based 5
    assert r.width % 4 == 0 and r.height % 4 == 0
    assert r.width == 1024 and r.height == 1024                 # 1025/1025 rounded down


def test_coerce_clamps_into_the_sensor_and_never_below_one():
    r = coerce_roi(Roi(left=-50, top=0, right=5000, bottom=9000), 2048, 2048)
    assert (r.left, r.top) == (1, 1)
    assert r.right == 2048 and r.bottom == 2048
    r = coerce_roi(Roi(left=2047, top=2047, right=2048, bottom=2048), 2048, 2048)
    assert r.left >= 1 and r.width >= 4 and r.right <= 2048      # a unit-sized ROI at the far corner


def test_full_sensor_roi_is_a_fixed_point():
    r = full_roi(2048, 2048)
    assert coerce_roi(r, 2048, 2048) == r


def test_adjust_roi_grows_and_shrinks_about_the_centre():
    r = Roi(513, 513, 1536, 1536)                               # 1024 x 1024 centred
    bigger = adjust_roi_for_pixels(r, (1224, 1224), (1024, 1024))
    assert (bigger.left, bigger.right) == (513 - 100, 1536 + 100)
    assert (bigger.top, bigger.bottom) == (513 - 100, 1536 + 100)
    smaller = adjust_roi_for_pixels(r, (512, 512), (1024, 1024))
    assert smaller.width == 512 and smaller.center == r.center
    # integer quotient by 2, as the LabVIEW IQ node does
    odd = adjust_roi_for_pixels(r, (1025, 1024), (1024, 1024))
    assert odd.width == 1024                                     # (1025-1024) IQ 2 = 0


def test_binning_is_symmetric_and_only_1_2_4():
    assert binning_from_xy(4, 4) == 4 and binning_from_xy(2, 2) == 2
    assert binning_from_xy(1, 1) == 1 and binning_from_xy(2, 4) == 1 and binning_from_xy(3, 3) == 1


def test_fov_from_pixel_size():
    assert fov_um(2048, 0.2167) == pytest.approx(443.8, abs=0.1)     # the Camera tab's 444 um
    assert pixels_for_um(443.8, 0.2167) == pytest.approx(2048, abs=1)


def test_button_helpers_keep_size_and_centre():
    assert centered_roi(1024, 1024, 2048, 2048) == Roi(513, 513, 1536, 1536)
    assert centered_roi(512, 512, 2048, 2048).center == (1024.5, 1024.5)
    r = Roi(1, 1, 512, 512)
    c = center_roi(r, 2048, 2048)
    assert c.width == 512 and c.center == (1024.5, 1024.5)
    at = center_roi_at(r, 300.0, 400.0)
    assert at.width == 512
    assert abs(at.center[0] - 300.0) <= 0.5 and abs(at.center[1] - 400.0) <= 0.5   # even width: x.5 centres


# -- the simulated camera honours ROI / binning / sensor mode --------------------

def test_simulated_camera_roi_and_binning_change_the_delivered_frame():
    cam = SimulatedCamera()
    cam.connect()
    assert cam.get_roi() == full_roi(2048, 2048)
    assert cam.info.width == 2048 and cam.info.height == 2048
    got = cam.set_roi(Roi(101, 201, 612, 712))                  # 512 x 512, off-centre
    assert got.width == 512 and got.height == 512 and got.left == 101
    assert (cam.info.width, cam.info.height) == (512, 512)
    cam.set_binning(2)
    assert (cam.info.width, cam.info.height) == (256, 256)
    frame = cam.snap()
    assert frame.shape == (256, 256)
    cam.set_binning(1)
    cam.set_roi(full_roi(2048, 2048))
    assert (cam.info.width, cam.info.height) == (2048, 2048)


def test_simulated_camera_sensor_mode():
    cam = SimulatedCamera()
    assert cam.get_sensor_mode() == "Normal Scan"
    assert cam.set_sensor_mode("Split View") == "Split View"
    with pytest.raises(ValueError):
        cam.set_sensor_mode("Banana")
