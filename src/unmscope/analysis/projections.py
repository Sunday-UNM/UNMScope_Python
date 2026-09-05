"""Max-intensity projections of a slanted (stage-scanned) light-sheet stack,
ported VI-for-VI from LouisXIV's `SPIM LV8.6 VIs/Image/PSF/` (block
diagrams in H:\\UNM_Lightsheet\\VI_Diagrams). Nothing here is derived; where
a diagram could not be read to the last detail it says so.

Call chain in LabVIEW (callers found by grepping the .vi files):
``HHMI - Get Max Projection of slanted stack.vi`` (called from SPIM MAIN /
Reviewer) -> ``HHMI - Get XY Max Projection of slanted stack.vi`` and
``HHMI - Calc XZ and YZ Max Projection from slanted stack.vi`` ->
``HHMI - Deskew Max column data into XZ Array.vi``.

XY (``Get XY Max Projection of slanted stack`` + ``Deskew Stack data into XY
Max projection``): the 'Sample Piezo' position is read from image[1] and
image[0], subtracted, and passed through **Absolute Value** -- so the XY
shift per slice is ``|S[1] - S[0]|`` in the units the position was recorded
in, with NO pixel-size conversion in that path (the VI has no link to a
pixel-size VI). Slice k is then shifted by ``k * step`` into a canvas widened
by ``(n-1)*step`` converted To I32; integral part as an array offset,
fractional part by interpolating neighbouring pixels; max-combined.

XZ / YZ (``Deskew Max column data into XZ Array``): ``S step = S[1] - S[0]``
(SIGNED), ``X step (um) = S step * cos(STAGE ANGLE)``, ``Z step (um) = S step
* sin(STAGE ANGLE)``, ``X step (pix) = X step (um) / Camera Image Pixel size``,
``Zpixsize/XYpixsize = Z step (um) / Camera Image Pixel size``. XZ is the
per-slice column max sheared by X step (pix); YZ the per-slice row max,
unsheared (X is collapsed there); both Z-rescaled by the ratio, flipped
horizontally, U16 (``Calc XZ and YZ Max Projection from slanted stack``).

Pixel size (``Image Acq/High level/Constants/Camera Image Pixel sizes.vi``):
``sensor pitch * binning / Mag`` -- see ``unmscope.config.calibration``.

Not resolvable from the rendered diagrams: whether the deskew's "Find shifted
pixel value" moves content to the right or to the left of the array for a
positive step. This port shifts to the RIGHT. LouisXIV works on this rig, so
the two agree iff a real point-like feature collapses after Calc; if it
smears, flip that one convention (see docs/stack_save_and_projections.md).
"""
from __future__ import annotations

import math

import numpy as np

#: SPIMProject.ini [Sample stage] "Angle between stage and bessel beam (deg)".
DEFAULT_STAGE_ANGLE_DEG = 45.0


def s_step(s_positions) -> float:
    """``S step (um) = S positions[1] - S positions[0]`` (signed), as in
    ``Deskew Max column data into XZ Array``."""
    s = np.asarray(s_positions, dtype=np.float64).ravel()
    if s.size < 2:
        return 0.0
    return float(s[1] - s[0])


def xy_shift_per_slice(s_positions) -> float:
    """``|Sample Piezo[1] - Sample Piezo[0]|`` -- the XY deskew step exactly as
    ``Get XY Max Projection of slanted stack`` feeds it: absolute, and in the
    position's own units (no pixel-size conversion in that VI)."""
    return abs(s_step(s_positions))


def xz_x_step_pixels(s_step_um: float, angle_deg: float, xy_pixel_um: float) -> float:
    """``X step (pix) = S step * cos(angle) / pixel size`` (signed)."""
    return s_step_um * math.cos(math.radians(angle_deg)) / xy_pixel_um


def z_over_xy_pixel(s_step_um: float, angle_deg: float, xy_pixel_um: float) -> float:
    """``Zpixsize / X and Y pixsize = S step * sin(angle) / pixel size``."""
    return s_step_um * math.sin(math.radians(angle_deg)) / xy_pixel_um


def to_i32(x: float) -> int:
    """LabVIEW 'To I32': round half to even, which Python's round() also does."""
    return int(round(x))


def deskew_canvas_width(width: int, n_slices: int, step: float) -> int:
    """``total # of pix shifts during slanted scan`` = ``(n-1) * step`` To I32,
    added to the row width (both deskew VIs)."""
    return width + abs(to_i32((max(n_slices, 1) - 1) * step))


def _shift_row_block(block: np.ndarray, shift: float, width_out: int, offset0: float) -> np.ndarray:
    """Place a (rows, w) block shifted right by ``shift`` (relative to
    ``offset0``, the smallest shift, so a signed XZ step never indexes
    negative): the integral part as an array offset, the fractional part as an
    interpolation between neighbouring pixels ("Find shifted pixel value").
    Anything past ``width_out`` is dropped, as the I32-sized canvas drops it."""
    rows, w = block.shape
    rel = shift - offset0
    i_shift = int(math.floor(rel))
    frac = rel - i_shift
    out = np.zeros((rows, width_out), dtype=np.float64)
    src = block.astype(np.float64)
    if frac > 1e-12:
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


def xy_max_projection(stack: np.ndarray, step: float) -> np.ndarray:
    """Deskewed XY max projection, uint16: slice k shifted by ``k * step``
    (``Deskew Stack data into XY Max projection``)."""
    stack = np.asarray(stack)
    if stack.ndim != 3 or stack.shape[0] == 0:
        raise ValueError("stack must be a non-empty (n, H, W) array")
    n, h, w = stack.shape
    width_out = deskew_canvas_width(w, n, step)
    shifts = [k * step for k in range(n)]
    offset0 = min(shifts)
    acc = np.zeros((h, width_out), dtype=np.float64)
    for k in range(n):
        np.maximum(acc, _shift_row_block(stack[k], shifts[k], width_out, offset0), out=acc)
    return np.clip(np.rint(acc), 0, 65535).astype(np.uint16)


def _resample_z(rows: np.ndarray, ratio: float) -> np.ndarray:
    """Rescale axis 0 by ``Zpixsize / XYpixsize`` so Z is in XY-pixel units.

    The ratio is a SIZE ratio, so its magnitude is used: for a stack scanned
    towards decreasing S the signed step makes it negative, and the sign is
    already carried by the XZ shear (an explicit call, not read off a diagram)."""
    ratio = abs(ratio)
    n = rows.shape[0]
    if n < 2 or ratio == 0 or abs(ratio - 1.0) < 1e-9:
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
    """(XZ, YZ), uint16, Z rescaled to XY-pixel units and flipped
    horizontally. XZ = per-slice column max sheared by the SIGNED
    ``x_step_pix``; YZ = per-slice row max, unsheared."""
    stack = np.asarray(stack)
    if stack.ndim != 3 or stack.shape[0] == 0:
        raise ValueError("stack must be a non-empty (n, H, W) array")
    n, h, w = stack.shape
    col_max = stack.max(axis=1)                  # (n, W): Z rows, X columns
    row_max = stack.max(axis=2)                  # (n, H): Z rows, Y columns
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


def stack_projections(stack: np.ndarray, s_positions, xy_pixel_um: float,
                      angle_deg: float = DEFAULT_STAGE_ANGLE_DEG, deskew: bool = True
                      ) -> dict[str, np.ndarray]:
    """The three projections of the Stack Projections tab, keyed 'XY', 'YZ',
    'XZ', from the per-slice Sample Piezo positions ``s_positions`` (LouisXIV
    reads them from each image's Position cluster).

    ``deskew=False`` is LouisXIV's non-slanted path (``Get Max Projection``):
    zero shear, a plain max along each axis, Z unscaled."""
    if deskew:
        xy_step = xy_shift_per_slice(s_positions)             # |dS|, position units
        ds = s_step(s_positions)                              # signed, um
        xz_step = xz_x_step_pixels(ds, angle_deg, xy_pixel_um)
        ratio = z_over_xy_pixel(ds, angle_deg, xy_pixel_um)
    else:
        xy_step, xz_step, ratio = 0.0, 0.0, 1.0
    xy = xy_max_projection(stack, xy_step)
    xz, yz = xz_yz_max_projections(stack, xz_step, ratio)
    return {"XY": xy, "YZ": yz, "XZ": xz}
