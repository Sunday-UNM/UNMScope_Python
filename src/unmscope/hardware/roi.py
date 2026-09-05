"""Camera ROI arithmetic ported from LouisXIV (block diagrams in
H:\\UNM_Lightsheet\\VI_Diagrams, `Image Acq/DCAM/Setup` and
`Image Acq/High level/ROI`). ROIs are **1-based, all-inclusive** on the
front panel and in these functions, exactly as the LabVIEW panel shows them
(Left 1 .. Right 2048); the DCAM driver's 0-based subarray is an internal
conversion inside the camera backend.

- ``DCAM - Coerce ROI.vi``: 1-based -> 0-based; Left/Top coerced into the
  CCD (0..hmax/vmax); set to the nearest position unit ``<=`` the desired
  value (hposunit / vposunit from the subarray info); ``Coerce LRTB to 1
  Min``; then ``DCAM - Coerce ROI size.vi`` with Round = Down.
- ``DCAM - Coerce ROI size.vi``: h size = Right-Left+1 (0-based), rounded to
  the size unit (Up or Down), within hmax; Right = Left + size - 1; same for
  v; back to 1-based; ``Coerce LRTB to 1 Min``.
- ``DCAM - Set ROI.vi``: coerce, then subarray hpos=Left, vpos=Top,
  hsize=Right-Left+1, vsize=Bottom-Top+1 (0-based) via DCAM SETPARAM.
- ``HHMI - Adjust ROI based upon change in number of pixels.vi``: on a
  change of "# of image pixels", d = (new - old) integer-divided by 2 per
  axis; Right += d, Left -= d; Bottom += d, Top -= d (the ROI grows/shrinks
  about its centre).
- ``DCAM - Set Binning.vi``: (4,4) -> 4x4, (2,2) -> 2x2, else 1x1.
- ``Camera Image Pixel sizes.vi``: um = pixels * pixel size; pixels = um /
  pixel size (pixel size = CCD pitch * binning / mag, see calibration).

The Camera tab's buttons are SPIM MAIN.vi event cases (read from a
hidden-frames export of the diagram, 2026-09-05): [9] "All pixels?" sets
(1, # of CCD pix X, 1, # of CCD pix Y); [5] "512x512"/"1024x1024" take the
constant ROI (1, size, 1, size) and [22] "Center ROI" the current ROI, and
shift all four edges by round(round(CCD/2) - mean(Left, Right)) per axis;
[21] "Center ROI at XY" sets Left/Right = X -/+ (width IQ 2) and Top/Bottom =
Y -/+ (height IQ 2) of the coerced ROI; [1] "# of image pixels" calls the
Adjust-ROI VI with the old value. Every one of them then fires [73] "ROI":
Value Change, which coerces (Camera Check USER ROI), updates # of image
pixels, and sends "Set Camera" to the scan engine.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

#: Orca Flash 4.0 DCAM subarray steps (hposunit/vposunit and hunit/vunit
#: from the driver's subarray info). The real backend reads the driver's
#: values when it can; these are the documented defaults for this camera.
DEFAULT_POSITION_UNIT = 4
DEFAULT_SIZE_UNIT = 4


@dataclass(frozen=True)
class Roi:
    """1-based, all-inclusive, as on the LouisXIV Camera tab."""
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left + 1

    @property
    def height(self) -> int:
        return self.bottom - self.top + 1

    @property
    def center(self) -> tuple[float, float]:
        return ((self.left + self.right) / 2.0, (self.top + self.bottom) / 2.0)


def full_roi(hmax: int, vmax: int) -> Roi:
    return Roi(1, 1, int(hmax), int(vmax))


def _snap_le(value: int, unit: int) -> int:
    """'Set to nearest unit <= desired' (Coerce ROI): floor to the unit."""
    unit = max(1, int(unit))
    return (int(value) // unit) * unit


def _round_size(size: int, unit: int, direction: str) -> int:
    """Coerce ROI size: round the size to the unit, Up or Down, never 0."""
    unit = max(1, int(unit))
    if direction.lower() == "up":
        return max(unit, int(math.ceil(size / unit)) * unit)
    return max(unit, (int(size) // unit) * unit)


def coerce_roi(roi: Roi, hmax: int, vmax: int, *, hposunit: int = DEFAULT_POSITION_UNIT,
               vposunit: int = DEFAULT_POSITION_UNIT, hunit: int = DEFAULT_SIZE_UNIT,
               vunit: int = DEFAULT_SIZE_UNIT, round_size: str = "Down") -> Roi:
    """``DCAM - Coerce ROI`` followed by ``DCAM - Coerce ROI size`` (the
    sequence ``DCAM - Set ROI`` performs), 1-based in and out."""
    # 1-based -> 0-based
    left, top = roi.left - 1, roi.top - 1
    right, bottom = roi.right - 1, roi.bottom - 1
    # Coerce to within CCD bounds, then to the nearest position unit <= desired
    left = _snap_le(min(max(left, 0), hmax - 1), hposunit)
    top = _snap_le(min(max(top, 0), vmax - 1), vposunit)
    # Coerce ROI size (Round = Down in Set ROI): size rounded to the unit,
    # then clamped to what remains of the sensor from Left/Top
    hsize = _round_size(max(1, right - left + 1), hunit, round_size)
    vsize = _round_size(max(1, bottom - top + 1), vunit, round_size)
    hsize = max(hunit, min(hsize, _snap_le(hmax - left, hunit)))
    vsize = max(vunit, min(vsize, _snap_le(vmax - top, vunit)))
    right, bottom = left + hsize - 1, top + vsize - 1
    # back to 1-based, 'Coerce LRTB to 1 Min'
    return Roi(max(1, left + 1), max(1, top + 1), max(1, right + 1), max(1, bottom + 1))


def subarray_from_roi(roi: Roi) -> tuple[int, int, int, int]:
    """``DCAM - Set ROI``: (hpos, vpos, hsize, vsize), 0-based, for the driver."""
    return (roi.left - 1, roi.top - 1, roi.width, roi.height)


def roi_from_subarray(hpos: int, vpos: int, hsize: int, vsize: int) -> Roi:
    return Roi(hpos + 1, vpos + 1, hpos + hsize, vpos + vsize)


def adjust_roi_for_pixels(roi: Roi, new_pixels: tuple[int, int], old_pixels: tuple[int, int]) -> Roi:
    """``HHMI - Adjust ROI based upon change in number of pixels``: grow or
    shrink symmetrically by (new - old) IQ 2 on each side, per axis."""
    dx = (int(new_pixels[0]) - int(old_pixels[0])) // 2
    dy = (int(new_pixels[1]) - int(old_pixels[1])) // 2
    return Roi(roi.left - dx, roi.top - dy, roi.right + dx, roi.bottom + dy)


def binning_from_xy(bx: int, by: int) -> int:
    """``DCAM - Set Binning``: 4x4 only for (4,4), 2x2 only for (2,2), else 1x1."""
    if int(bx) == 4 and int(by) == 4:
        return 4
    if int(bx) == 2 and int(by) == 2:
        return 2
    return 1


def fov_um(pixels: int, pixel_size_um: float) -> float:
    """``Camera Image Pixel sizes``: um = Pixels * pixel size."""
    return float(pixels) * float(pixel_size_um)


def pixels_for_um(um: float, pixel_size_um: float) -> float:
    return float(um) / float(pixel_size_um)


# -- Camera-tab buttons: SPIM MAIN.vi event cases (see the module docstring) ------

def _to_i32(x: float) -> int:
    """LabVIEW Round / To I32: half to even, as Python's round()."""
    return int(round(x))


def shift_to_sensor_center(roi: Roi, hmax: int, vmax: int) -> Roi:
    """[22] "Center ROI" (and [5] for the presets): per axis, shift by
    round(round(CCD pix / 2) - mean(Left, Right)); all four edges move."""
    cx, cy = _to_i32(hmax / 2), _to_i32(vmax / 2)
    dx = _to_i32(cx - (roi.left + roi.right) / 2)
    dy = _to_i32(cy - (roi.top + roi.bottom) / 2)
    return Roi(roi.left + dx, roi.top + dy, roi.right + dx, roi.bottom + dy)


def centered_roi(width: int, height: int, hmax: int, vmax: int) -> Roi:
    """[5] "512x512" / "1024x1024": the constant (1, size, 1, size) ROI,
    shifted to the sensor centre."""
    return shift_to_sensor_center(Roi(1, 1, int(width), int(height)), hmax, vmax)


def center_roi(roi: Roi, hmax: int, vmax: int) -> Roi:
    """[22] "Center ROI": the current ROI shifted to the sensor centre."""
    return shift_to_sensor_center(roi, hmax, vmax)


def center_roi_at(roi: Roi, cx: float, cy: float) -> Roi:
    """[21] "Center ROI at XY": Left/Right = X -/+ (width IQ 2), Top/Bottom =
    Y -/+ (height IQ 2). For even sizes this is one pixel wider than the ROI;
    the ROI handler's coercion (size rounded Down) trims it."""
    hw, hh = roi.width // 2, roi.height // 2
    cx, cy = int(cx), int(cy)
    return Roi(cx - hw, cy - hh, cx + hw, cy + hh)
