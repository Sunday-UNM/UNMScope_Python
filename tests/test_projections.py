"""Stack projections ported from LouisXIV's Image/PSF VIs."""
import math

import numpy as np
import pytest

from unmscope.analysis.projections import (
    DEFAULT_STAGE_ANGLE_DEG, deskew_canvas_width, stack_projections, x_step_pixels,
    xy_max_projection, xz_yz_max_projections, z_over_xy_pixel,
)


def _slanted_point_stack(n=8, h=16, w=24, x_step=1.5, x0=3, y0=7, value=4000):
    """A single bright point that walks right by x_step per slice -- what a
    stationary point looks like in a stage-scanned (slanted) stack."""
    stack = np.zeros((n, h, w), dtype=np.uint16)
    for k in range(n):
        x = x0 + k * x_step
        lo = int(math.floor(x)); frac = x - lo
        stack[k, y0, lo] = int(round(value * (1 - frac)))
        if frac > 0 and lo + 1 < w:
            stack[k, y0, lo + 1] = int(round(value * frac))
    return stack


def test_geometry_at_45_degrees():
    # a 1 um stage step at 45 deg splits equally into lateral and depth
    assert x_step_pixels(1.0, 45.0, 0.5) == pytest.approx(math.cos(math.radians(45)) / 0.5)
    assert z_over_xy_pixel(1.0, 45.0, 0.5) == pytest.approx(math.sin(math.radians(45)) / 0.5)
    assert DEFAULT_STAGE_ANGLE_DEG == 45.0


def test_canvas_widens_by_total_shift():
    assert deskew_canvas_width(100, 5, 2.0) == 108      # (5-1)*2
    assert deskew_canvas_width(100, 1, 2.0) == 100
    assert deskew_canvas_width(100, 5, -2.0) == 108     # either direction


def test_deskew_zero_shift_is_a_plain_max():
    stack = np.random.default_rng(0).integers(0, 1000, (5, 10, 12), dtype=np.uint16)
    np.testing.assert_array_equal(xy_max_projection(stack, 0.0), stack.max(axis=0))


def test_deskew_collapses_a_walking_point_back_to_one_spot():
    """The whole point of the shear: a point that drifts x_step per slice in
    the raw stack lands on the same column in every deskewed slice, so the
    XY projection is one compact spot rather than a streak."""
    x_step = 1.5
    stack = _slanted_point_stack(x_step=x_step)
    streak = stack.max(axis=0)                       # no deskew: a line
    spot = xy_max_projection(stack, -x_step)         # undo the walk
    assert (streak[7] > 0).sum() >= 8                # raw: spread over many columns
    assert (spot[7] > 0).sum() <= 3, "deskew did not bring the point back together"
    # intensity conserved: the raw point is worth 4000 in every slice, and the
    # max-projection of aligned copies must still carry (very nearly) all of it
    assert spot[7].astype(int).sum() >= 0.95 * 4000


def test_deskew_sub_pixel_shift_conserves_intensity():
    stack = np.zeros((2, 4, 10), dtype=np.uint16)
    stack[0, 1, 4] = 1000
    stack[1, 1, 4] = 1000
    out = xy_max_projection(stack, 0.5)              # slice 1 shifts by half a pixel
    assert out.shape == (4, 11)
    row = out[1].astype(int)
    assert row.sum() >= 1000                          # nothing lost
    assert row.max() == 1000                          # slice 0 keeps its full pixel


def test_side_views_shapes_dtype_and_flip():
    stack = np.zeros((6, 8, 10), dtype=np.uint16)
    stack[:, 2, 1] = np.arange(6) * 100 + 100         # a column that brightens with Z, near the left edge
    xz, yz = xz_yz_max_projections(stack, 0.0, 1.0)
    assert xz.dtype == np.uint16 and yz.dtype == np.uint16
    assert xz.shape == (6, 10) and yz.shape == (6, 8)
    # flipped horizontally: the bright column that was at x=1 is now at x=8
    assert int(np.argmax(xz[5])) == 10 - 1 - 1
    # Z rescaling by 2 doubles the Z rows
    xz2, yz2 = xz_yz_max_projections(stack, 0.0, 2.0)
    assert xz2.shape[0] == 12 and yz2.shape[0] == 12


def test_stack_projections_bundle_and_deskew_toggle():
    stack = _slanted_point_stack()
    on = stack_projections(stack, s_step_um=1.0, xy_pixel_um=0.5, deskew=True)
    off = stack_projections(stack, s_step_um=1.0, xy_pixel_um=0.5, deskew=False)
    assert set(on) == {"XY", "YZ", "XZ"}
    np.testing.assert_array_equal(off["XY"], stack.max(axis=0))    # DeSkew unticked = plain max
    assert on["XY"].shape[1] > off["XY"].shape[1]                   # sheared canvas is wider


def _bead_stack(step_px, n=20, h=60, w=160):
    x0 = 30 if step_px >= 0 else 30 + int(round(n * abs(step_px)))   # stay in bounds either way
    stack = np.zeros((n, h, w), dtype=np.uint16)
    for k in range(n):
        x = int(round(x0 + k * step_px))
        assert 0 <= x and x + 4 <= w
        stack[k, 20:24, x:x + 4] = 3000
    return stack


@pytest.mark.parametrize("step_px", [2.0, -2.0])
def test_auto_direction_collapses_a_bead_drifting_either_way(step_px):
    """The shear must OPPOSE whichever way the image content walks. With the
    sign wrong the bead's streak DOUBLES (38 -> 80 columns on the 20-slice
    stack that exposed this) instead of collapsing."""
    from unmscope.analysis.projections import estimate_drift_sign
    stack = _bead_stack(step_px)
    assert estimate_drift_sign(stack) == (1 if step_px > 0 else -1)
    # ds chosen so x_step_pixels(ds, 45, 0.2167) == 2.0 px
    ds = 2.0 * 0.2167 / math.cos(math.radians(45))
    on = stack_projections(stack, s_step_um=ds, xy_pixel_um=0.2167, deskew=True)
    streak = int((stack.max(axis=0)[21] > 1000).sum())
    spot = int((on["XY"][21] > 1000).sum())
    assert streak >= 38
    assert spot <= 6, f"bead did not collapse: {spot} columns wide (undeskewed {streak})"


def test_explicit_direction_overrides_auto():
    stack = _bead_stack(2.0)
    ds = 2.0 * 0.2167 / math.cos(math.radians(45))
    right = stack_projections(stack, ds, 0.2167, direction=+1)   # drift is +X: collapses
    wrong = stack_projections(stack, ds, 0.2167, direction=-1)   # told the opposite: doubles the streak
    assert int((right["XY"][21] > 1000).sum()) <= 6
    assert int((wrong["XY"][21] > 1000).sum()) > 38
