"""Camera ROI rules ported from LouisXIV's DCAM VIs, and the camera backends'
ROI / binning / sensor-mode behaviour on the simulated camera."""
import pytest

from unmscope.hardware.camera import Camera, OrcaFlash4Camera, SimulatedCamera
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
    # [21] "Center ROI at XY": X -/+ (512 IQ 2) -> 44..556, one pixel wider than
    # 512; the ROI handler's coercion trims it afterwards
    assert (at.left, at.right, at.top, at.bottom) == (44, 556, 144, 656)
    assert at.width == 513 and at.center == (300.0, 400.0)


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


# -- what the real camera and the LabVIEW source actually say ----------------
# Both measured / read on 2026-09-05, ROADMAP item 10. These lock in facts
# that were guesses until then; a change here means the hardware or the
# LouisXIV source disagrees with us, not that the test needs relaxing.

def test_louisxiv_sensor_mode_enum_is_the_six_cases_in_the_set_vi():
    """DCAM - Set Sensor Mode.vi / DCAM - Get Sensor Mode.vi hidden frames."""
    assert Camera.SENSOR_MODES == ("Normal Scan", "Light Sheet", "Split View",
                                   "Dual LS", "Rolling Top", "Rolling Bottom")


def test_sensor_modes_split_between_two_dcam_properties():
    """Four names set SENSOR MODE; Rolling Top/Bottom set READOUT DIRECTION."""
    props = OrcaFlash4Camera.SENSOR_MODE_PROPERTIES
    assert props["Normal Scan"] == ("SENSOR MODE", "AREA")
    assert props["Split View"] == ("SENSOR MODE", "SPLIT VIEW")
    assert props["Rolling Top"] == ("READOUT DIRECTION", "FORWARD")
    assert props["Rolling Bottom"] == ("READOUT DIRECTION", "BACKWARD")
    # Measured: this camera + adapter refuses PROGRESSIVE, so LouisXIV's
    # Light Sheet (12) and Dual LS (16) have no route through pymmcore.
    assert set(OrcaFlash4Camera.UNSUPPORTED_SENSOR_MODES) == {"Light Sheet", "Dual LS"}
    assert set(props) | set(OrcaFlash4Camera.UNSUPPORTED_SENSOR_MODES) == set(Camera.SENSOR_MODES)


def test_coerced_rois_are_fixed_points_of_the_real_driver():
    """coerce_roi must hand the driver something it will not change again.

    MEASURED 2026-09-05 (spike 34): this camera snaps subarray positions
    DOWN to a multiple of 4 and rounds sizes DOWN to a multiple of 4. So the
    guarantee we need is that our output is already 4-aligned, in bounds,
    and unchanged by coercing a second time.

    Note our coercion is NOT the driver's. coerce_roi coerces a rectangle
    the way LouisXIV's DCAM - Coerce ROI.vi does, holding Right/Bottom while
    Left/Top snap down, so the width can grow by one unit; the driver
    coerces position and size independently and keeps the requested size.
    Requesting (2, 2, 1022, 1022) gives (0, 0, 1024, 1024) here and
    (0, 0, 1020, 1020) from the driver. Both are valid; ours is the one that
    keeps the edge the user dragged where they put it.
    """
    requested = [(1, 1, 2046, 2046), (2, 2, 1022, 1022), (3, 3, 510, 510),
                 (5, 5, 254, 254), (7, 7, 130, 130), (9, 9, 66, 66),
                 (100, 200, 301, 401), (1023, 1023, 1025, 1025)]
    for x, y, w, h in requested:
        got = subarray_from_roi(coerce_roi(roi_from_subarray(x, y, w, h), 2048, 2048))
        assert all(v % 4 == 0 for v in got), f"({x},{y},{w},{h}) -> {got} not 4-aligned"
        assert got[0] + got[2] <= 2048 and got[1] + got[3] <= 2048
        again = subarray_from_roi(coerce_roi(roi_from_subarray(*got), 2048, 2048))
        assert again == got, f"coercing {got} again moved it to {again}"


def test_the_two_cases_where_we_agree_with_the_driver_exactly():
    """Where Left/Top are already on the unit, ours and the driver's agree."""
    for req, driver in [((5, 5, 254, 254), (4, 4, 252, 252)),
                        ((9, 9, 66, 66), (8, 8, 64, 64)),
                        ((100, 200, 301, 401), (100, 200, 300, 400)),
                        ((1, 1, 2046, 2046), (0, 0, 2044, 2044))]:
        assert subarray_from_roi(coerce_roi(roi_from_subarray(*req), 2048, 2048)) == driver
