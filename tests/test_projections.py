"""Stack projections ported from LouisXIV's Image/PSF VIs -- tests pin the
LabVIEW conventions, not a nicer alternative."""
import math

import numpy as np
import pytest

from unmscope.analysis.projections import (
    DEFAULT_STAGE_ANGLE_DEG, deskew_canvas_width, s_step, stack_projections, to_i32,
    xy_max_projection, xy_shift_per_slice, xz_x_step_pixels, xz_yz_max_projections,
    z_over_xy_pixel,
)


def _bead_stack(step_px, n=20, h=60, w=160):
    """A bead that walks `step_px` per slice along X (what a stationary point
    looks like in a slanted stack)."""
    x0 = 30 if step_px >= 0 else 30 + int(round(n * abs(step_px)))
    stack = np.zeros((n, h, w), dtype=np.uint16)
    for k in range(n):
        x = int(round(x0 + k * step_px))
        assert 0 <= x and x + 4 <= w
        stack[k, 20:24, x:x + 4] = 3000
    return stack


def _width(row):
    return int((row > 1000).sum())


# -- the exact LabVIEW inputs ---------------------------------------------------

def test_s_step_is_signed_and_xy_step_is_its_absolute_value():
    # Deskew Max column data: S step = S[1] - S[0] (signed)
    assert s_step([10.0, 12.0, 14.0]) == 2.0
    assert s_step([14.0, 12.0, 10.0]) == -2.0
    # Get XY Max Projection of slanted stack: |Sample Piezo[1] - [0]|, position units
    assert xy_shift_per_slice([14.0, 12.0, 10.0]) == 2.0
    assert xy_shift_per_slice([10.0, 12.0, 14.0]) == 2.0
    assert s_step([5.0]) == 0.0


def test_xz_step_and_z_ratio_follow_the_stage_angle():
    c, s = math.cos(math.radians(45)), math.sin(math.radians(45))
    assert xz_x_step_pixels(1.0, 45.0, 0.5) == pytest.approx(c / 0.5)
    assert xz_x_step_pixels(-1.0, 45.0, 0.5) == pytest.approx(-c / 0.5)     # signed
    assert z_over_xy_pixel(1.0, 45.0, 0.5) == pytest.approx(s / 0.5)
    assert DEFAULT_STAGE_ANGLE_DEG == 45.0


def test_canvas_uses_labview_to_i32_rounding():
    assert to_i32(0.5) == 0 and to_i32(1.5) == 2 and to_i32(2.5) == 2       # half to even
    assert deskew_canvas_width(100, 5, 2.0) == 108
    assert deskew_canvas_width(100, 2, 0.5) == 100                           # (2-1)*0.5 -> I32 0
    assert deskew_canvas_width(100, 5, -2.0) == 108


# -- behaviour ------------------------------------------------------------------

def test_zero_shift_is_a_plain_max():
    stack = np.random.default_rng(0).integers(0, 1000, (5, 10, 12), dtype=np.uint16)
    np.testing.assert_array_equal(xy_max_projection(stack, 0.0), stack.max(axis=0))


def test_xy_deskew_collapses_a_bead_walking_against_the_shift():
    """LabVIEW shifts slice k by +k*|dS|. A feature that walks the OTHER way
    by the same amount lands on one column in every shifted slice."""
    stack = _bead_stack(-2.0)
    out = xy_max_projection(stack, 2.0)
    assert _width(stack.max(axis=0)[21]) >= 38          # undeskewed: a streak
    assert _width(out[21]) <= 6, f"bead did not collapse: {_width(out[21])} columns"
    assert out[21].astype(int).sum() >= 0.95 * 4 * 3000


def test_xy_uses_the_absolute_step_whichever_way_the_stack_was_scanned():
    stack = _bead_stack(-2.0)
    up = stack_projections(stack, s_positions=[0.0, 2.0, 4.0], xy_pixel_um=0.2167)
    down = stack_projections(stack, s_positions=[4.0, 2.0, 0.0], xy_pixel_um=0.2167)
    np.testing.assert_array_equal(up["XY"], down["XY"])   # |dS| is the same
    assert _width(up["XY"][21]) <= 6


def test_xz_shear_is_signed_by_the_scan_direction():
    stack = _bead_stack(-2.0)
    up = stack_projections(stack, s_positions=[0.0, 1.0], xy_pixel_um=0.2167)
    down = stack_projections(stack, s_positions=[1.0, 0.0], xy_pixel_um=0.2167)
    assert up["XZ"].shape == down["XZ"].shape
    assert not np.array_equal(up["XZ"], down["XZ"])        # opposite shear


def test_fractional_shift_interpolates_between_neighbours():
    stack = np.zeros((2, 4, 10), dtype=np.uint16)
    stack[0, 1, 4] = 1000
    stack[1, 1, 4] = 1000
    out = xy_max_projection(stack, 0.5)                   # slice 1: half-pixel shift
    assert out.shape == (4, 10)                           # (2-1)*0.5 To I32 = 0 extra columns
    row = out[1].astype(int)
    assert row[4] == 1000 and row[5] == 500               # 1000 max-combined with 500/500


def test_side_views_shapes_dtype_and_flip():
    stack = np.zeros((6, 8, 10), dtype=np.uint16)
    stack[:, 2, 1] = np.arange(6) * 100 + 100
    xz, yz = xz_yz_max_projections(stack, 0.0, 1.0)
    assert xz.dtype == np.uint16 and yz.dtype == np.uint16
    assert xz.shape == (6, 10) and yz.shape == (6, 8)
    assert int(np.argmax(xz[5])) == 10 - 1 - 1           # flipped horizontally
    xz2, yz2 = xz_yz_max_projections(stack, 0.0, 2.0)
    assert xz2.shape[0] == 12 and yz2.shape[0] == 12      # Z rescaled


def test_deskew_off_is_the_plain_projection_path():
    stack = _bead_stack(-2.0)
    off = stack_projections(stack, s_positions=[0.0, 2.0], xy_pixel_um=0.2167, deskew=False)
    np.testing.assert_array_equal(off["XY"], stack.max(axis=0))
    assert off["XZ"].shape == (stack.shape[0], stack.shape[2])
