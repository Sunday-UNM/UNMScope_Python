"""Max-intensity projections of a slanted (stage-scanned) light-sheet stack,
ported from LouisXIV's `SPIM LV8.6 VIs/Image/PSF/` VIs (block diagrams in
H:\\UNM_Lightsheet\\VI_Diagrams, rendered 2026-08-28):

- ``HHMI - Deskew Stack data into XY Max projection.vi``: each slice is
  shifted laterally by ``slice_index * X step (pix)`` into a canvas widened
  by ``(# of slices - 1) * X step`` -- the integral part as an array offset,
  the fractional part by interpolating neighbouring pixels ("Find shifted
  pixel value") -- and the shifted slices are max-combined into the XY
  projection (``HHMI - Get XY Max Projection of slanted stack.vi``).
- ``HHMI - Get Max Projection of slanted stack.vi``: per slice, the column
  max (one row per slice -> XZ) and the row max (-> YZ), stacked over Z.
- ``HHMI - Deskew Max column data into XZ Array.vi``: the same shear applied
  to the XZ column-max data (Y is collapsed in YZ, so YZ is not sheared).
- ``HHMI - Calc XZ and YZ Max Projection from slanted stack.vi``: the Z axis
  is rescaled by ``Zpixsize / X and Y pixsize`` so the side views are drawn
  in XY-pixel units, and both are flipped horizontally; output is U16.

Geometry. The sample stage moves at ``Angle between stage and bessel beam``
(SPIMProject.ini ``[Sample stage]``, 45 deg on this rig). A stage step of
``ds`` um per slice therefore moves the image laterally by ``ds*cos(theta)``
and in depth by ``ds*sin(theta)``. In pixels:

    X step (pix)        = ds * cos(theta) / xy_pixel_um
    Zpixsize/XYpixsize  = ds * sin(theta) / xy_pixel_um

The LabVIEW VIs take ``X step (pix)`` and the pixel-size ratio as inputs
computed upstream from the per-slice "Sample Piezo" positions; the two
formulas above are the geometric derivation of those inputs and are the one
part of this port inferred from geometry rather than read off a diagram.
"""
from __future__ import annotations

import math

import numpy as np

#: SPIMProject.ini [Sample stage] "Angle between stage and bessel beam (deg)".
DEFAULT_STAGE_ANGLE_DEG = 45.0


def x_step_pixels(s_step_um: float, angle_deg: float, xy_pixel_um: float) -> float:
    """Lateral image shift per slice, in pixels (LouisXIV's 'X step (pix)')."""
    return s_step_um * math.cos(math.radians(angle_deg)) / xy_pixel_um


def z_over_xy_pixel(s_step_um: float, angle_deg: float, xy_pixel_um: float) -> float:
    """Depth step per slice in XY-pixel units ('Zpixsize / X and Y pixsize')."""
    return s_step_um * math.sin(math.radians(angle_deg)) / xy_pixel_um


def _shift_row_block(block: np.ndarray, shift: float, width_out: int, offset0: float) -> np.ndarray:
    """Shift a (rows, w) block right by ``shift`` pixels (relative to
    ``offset0``, the smallest shift in the stack so nothing is negative) into
    a (rows, width_out) canvas: integral part as an offset, fractional part
    as a linear blend of neighbouring source pixels."""
    rows, w = block.shape
    rel = shift - offset0
    i_shift = int(math.floor(rel))
    frac = rel - i_shift
    out = np.zeros((rows, width_out), dtype=np.float64)
    src = block.astype(np.float64)
    if frac > 1e-12:
        # Sub-pixel shift right by `frac`: source pixel j contributes
        # (1-frac) to output j and frac to output j+1 -- a two-tap spread
        # that CONSERVES intensity, including the last pixel's spill into
        # column w (hence the w+1 width; rounding the canvas down lost it).
        spread = np.zeros((rows, w + 1), dtype=np.float64)
        spread[:, :w] += (1.0 - frac) * src
        spread[:, 1:] += frac * src
        src = spread
    sw = src.shape[1]
    lo = max(0, i_shift)
    hi = min(width_out, i_shift + sw)
    if hi > lo:
        out[:, lo:hi] = src[:, lo - i_shift:hi - i_shift]
    return out


def deskew_canvas_width(width: int, n_slices: int, x_step_pix: float) -> int:
    """Width of the sheared canvas: ``w + ceil((n-1)*|x_step|)``.

    LouisXIV converts the total shift to I32 (a round); ceil is used here so
    a fractional final shift never spills intensity off the right edge --
    at most one pixel wider than the LabVIEW canvas, never narrower."""
    return width + int(math.ceil((max(n_slices, 1) - 1) * abs(x_step_pix) - 1e-9))


def xy_max_projection(stack: np.ndarray, x_step_pix: float) -> np.ndarray:
    """Deskewed XY max projection, uint16, shape (H, W + total shift)."""
    stack = np.asarray(stack)
    if stack.ndim != 3 or stack.shape[0] == 0:
        raise ValueError("stack must be a non-empty (n, H, W) array")
    n, h, w = stack.shape
    width_out = deskew_canvas_width(w, n, x_step_pix)
    shifts = [k * x_step_pix for k in range(n)]
    offset0 = min(shifts)
    acc = np.zeros((h, width_out), dtype=np.float64)
    for k in range(n):
        np.maximum(acc, _shift_row_block(stack[k], shifts[k], width_out, offset0), out=acc)
    return np.clip(np.rint(acc), 0, 65535).astype(np.uint16)


def _resample_z(rows: np.ndarray, ratio: float) -> np.ndarray:
    """Linearly resample axis 0 of (n, m) by ``ratio`` so Z is in XY-pixel
    units (LouisXIV rescales by Zpixsize / XYpixsize)."""
    n = rows.shape[0]
    if n < 2 or ratio <= 0 or abs(ratio - 1.0) < 1e-9:
        return rows.astype(np.float64)
    new_n = max(2, int(round(n * ratio)))
    pos = np.linspace(0, n - 1, new_n)
    lo = np.floor(pos).astype(int)
    hi = np.minimum(lo + 1, n - 1)
    t = (pos - lo)[:, None]
    src = rows.astype(np.float64)
    return (1.0 - t) * src[lo] + t * src[hi]


def xz_yz_max_projections(stack: np.ndarray, x_step_pix: float, z_over_xy: float
                          ) -> tuple[np.ndarray, np.ndarray]:
    """(XZ, YZ) side-view max projections, uint16, Z rescaled to XY-pixel
    units and flipped horizontally as LouisXIV draws them.

    XZ is the per-slice column max (one row per slice) sheared by the same
    ``x_step_pix`` as the XY deskew; YZ is the per-slice row max, unsheared
    (X is collapsed there)."""
    stack = np.asarray(stack)
    if stack.ndim != 3 or stack.shape[0] == 0:
        raise ValueError("stack must be a non-empty (n, H, W) array")
    n, h, w = stack.shape
    col_max = stack.max(axis=1)                  # (n, W): Z rows, X columns
    row_max = stack.max(axis=2)                  # (n, H): Z rows, Y columns
    # shear the XZ rows like the slices they came from
    width_out = deskew_canvas_width(w, n, x_step_pix)
    shifts = [k * x_step_pix for k in range(n)]
    offset0 = min(shifts)
    xz = np.zeros((n, width_out), dtype=np.float64)
    for k in range(n):
        xz[k] = _shift_row_block(col_max[k:k + 1], shifts[k], width_out, offset0)[0]
    xz = _resample_z(xz, z_over_xy)
    yz = _resample_z(row_max, z_over_xy)
    xz = np.fliplr(xz)
    yz = np.fliplr(yz)
    to_u16 = lambda a: np.clip(np.rint(a), 0, 65535).astype(np.uint16)
    return to_u16(xz), to_u16(yz)


def estimate_drift_sign(stack: np.ndarray) -> int:
    """Which way image content walks along X from the first slice to the
    last: +1 (rightwards), -1 (leftwards), 0 if it cannot tell.

    LouisXIV's ``X step (pix)`` carries this sign from the per-slice Sample
    Piezo positions; the optical orientation that fixes it is not in the ini,
    so it is measured here from the data: the lag of the peak of the
    cross-correlation between the first and last slices' column-max profiles.
    """
    stack = np.asarray(stack)
    n = stack.shape[0]
    if n < 2:
        return 0
    a = stack[0].max(axis=0).astype(np.float64)
    b = stack[-1].max(axis=0).astype(np.float64)
    a -= a.mean(); b -= b.mean()
    if not a.any() or not b.any():
        return 0
    corr = np.correlate(b, a, mode="full")          # lag > 0: b is a shifted RIGHT
    lag = int(np.argmax(corr)) - (len(a) - 1)
    return (lag > 0) - (lag < 0)


def stack_projections(stack: np.ndarray, s_step_um: float, xy_pixel_um: float,
                      angle_deg: float = DEFAULT_STAGE_ANGLE_DEG, deskew: bool = True,
                      direction: int | str = "auto") -> dict[str, np.ndarray]:
    """The three projections the Stack Projections tab shows, keyed
    'XY', 'YZ', 'XZ'. With ``deskew`` off the shear is zero (a straight
    max along each axis), as when LouisXIV's DeSkew box is unticked.

    ``direction``: the sign of the lateral drift per slice, +1 / -1, or
    "auto" to measure it (estimate_drift_sign). The shear always OPPOSES
    the drift, so a stationary feature collapses to one spot."""
    if not deskew:
        xy = xy_max_projection(stack, 0.0)
        xz, yz = xz_yz_max_projections(stack, 0.0, 1.0)
        return {"XY": xy, "YZ": yz, "XZ": xz}
    sign = estimate_drift_sign(stack) if direction == "auto" else int(np.sign(direction))
    if sign == 0:
        sign = 1
    x_step = -sign * x_step_pixels(s_step_um, angle_deg, xy_pixel_um)
    ratio = z_over_xy_pixel(s_step_um, angle_deg, xy_pixel_um)
    xy = xy_max_projection(stack, x_step)
    xz, yz = xz_yz_max_projections(stack, x_step, ratio)
    return {"XY": xy, "YZ": yz, "XZ": xz}
