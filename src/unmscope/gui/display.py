"""Image display the way LouisXIV's Images tab does it (SPIM MAIN.vi, state
"Set User Palette", read from the hidden-frames export, 2026-09-05):

- **Scale** selector -> IMAQ "16-bit Display Mapping" of the Image Display
  (and the XY / YZ / XZ projections and the background image):
  "Autoscale Z" -> Full Dynamic (the frame's own min..max);
  "By constant"  -> Given Range, Bright min .. Bright max (the panel's
  Max Counts with Scale to Counts on);
  "No Selection" -> Given Range, 0 .. Max # of camera counts.
- **Pallete Color** -> IMAQ Palette Type: Gray -> Grayscale (0), Gradient
  -> Gradient (2), Rainbow -> Rainbow (3).
- **Zoom to fit** -> the display's Zoom to Fit Mode, then "Update scan
  display".
- [88] "Show Length Scale" / "Show Text Info Overlay" -> overlays.

The IMAQ palettes themselves are not in the LabVIEW source (they are the
Vision runtime's). Gray and Rainbow follow NI's definition (Rainbow: blue
-> cyan -> green -> yellow -> red). Gradient follows NI's description ("a
gradation from red to white with a prominent range of light blue shades in
the middle") and is APPROXIMATE -- compare with LouisXIV on a real image.
"""
from __future__ import annotations

from collections import deque

import numpy as np
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap

PALETTES = ("Gray", "Gradient", "Rainbow")
CAMERA_MAX_COUNTS = 65535          # "Max # of camera counts" for the 16-bit Orca


def _interp_lut(stops: list[tuple[int, tuple[int, int, int]]]) -> np.ndarray:
    """256 x 3 uint8 LUT from (index, rgb) stops."""
    idx = np.arange(256)
    xs = [s[0] for s in stops]
    lut = np.stack([np.interp(idx, xs, [s[1][c] for s in stops]) for c in range(3)], axis=1)
    return np.clip(np.rint(lut), 0, 255).astype(np.uint8)


_LUTS = {
    "Gray": _interp_lut([(0, (0, 0, 0)), (255, (255, 255, 255))]),
    "Rainbow": _interp_lut([(0, (0, 0, 255)), (64, (0, 255, 255)), (128, (0, 255, 0)),
                            (192, (255, 255, 0)), (255, (255, 0, 0))]),
    # NI's description of IMAQ Gradient; approximate (see module docstring)
    "Gradient": _interp_lut([(0, (128, 0, 0)), (64, (255, 64, 64)), (128, (160, 208, 255)),
                             (192, (208, 232, 255)), (255, (255, 255, 255))]),
}


def palette_lut(name: str) -> np.ndarray:
    return _LUTS.get(name, _LUTS["Gray"])


def display_range(frame: np.ndarray, *, autoscale: bool, scale_to_counts: bool, max_counts: int,
                  bright_min: int = 0, camera_max: int = CAMERA_MAX_COUNTS) -> tuple[float, float]:
    """The (min, max) the 16-bit display mapping uses, per the Scale cases."""
    if autoscale:                                       # "Autoscale Z": Full Dynamic
        lo, hi = float(frame.min()), float(frame.max())
    elif scale_to_counts:                               # "By constant": Given Range Bright min..max
        lo, hi = float(bright_min), float(max_counts)
    else:                                               # "No Selection": 0 .. camera max
        lo, hi = 0.0, float(camera_max)
    if hi <= lo:
        hi = lo + 1.0
    return lo, hi


def map_to_8bit(frame: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """IMAQ 16-bit display mapping: lo..hi -> 0..255, clipped."""
    return np.clip(np.rint((frame.astype(np.float32) - lo) / (hi - lo) * 255.0), 0, 255).astype(np.uint8)


class FrameAverager:
    """'Frames to Avg': the display shows the running mean of the last N
    frames (acquisition and saving keep the raw frames)."""

    def __init__(self, n: int = 1):
        self._n = max(1, int(n))
        self._buf: deque[np.ndarray] = deque(maxlen=self._n)

    @property
    def n(self) -> int:
        return self._n

    def set_n(self, n: int) -> None:
        n = max(1, int(n))
        if n != self._n:
            self._n = n
            self._buf = deque(list(self._buf)[-n:], maxlen=n)

    def reset(self) -> None:
        self._buf.clear()

    def push(self, frame: np.ndarray) -> np.ndarray:
        if self._buf and self._buf[-1].shape != frame.shape:
            self._buf.clear()
        self._buf.append(frame)
        if self._n == 1 or len(self._buf) == 1:
            return frame
        return np.mean(np.stack(self._buf), axis=0).astype(frame.dtype)


def render_frame(frame: np.ndarray, *, palette: str = "Gray", lo: float | None = None,
                 hi: float | None = None, label_text: str | None = None,
                 scalebar_um_per_px: float | None = None) -> QPixmap:
    """A display-ready QPixmap: 16-bit mapping (lo..hi; None -> the frame's
    own min..max) -> palette LUT -> optional burned-in text and scale bar."""
    if lo is None or hi is None:
        lo, hi = display_range(frame, autoscale=True, scale_to_counts=False, max_counts=0)
    idx = map_to_8bit(frame, lo, hi)
    h, w = idx.shape
    if palette == "Gray":
        qimg = QImage(idx.data, w, h, w, QImage.Format_Grayscale8).copy()
    else:
        rgb = np.ascontiguousarray(palette_lut(palette)[idx])          # (h, w, 3) uint8
        qimg = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
    pix = QPixmap.fromImage(qimg)
    if label_text or scalebar_um_per_px:
        painter = QPainter(pix)
        if label_text:
            font = QFont()
            font.setPointSize(max(14, w // 40))
            font.setBold(True)
            painter.setFont(font)
            metrics = painter.fontMetrics()
            text_rect = metrics.boundingRect(label_text)
            pad = 8
            box = QRect(10, 10, text_rect.width() + 2 * pad, text_rect.height() + 2 * pad)
            painter.fillRect(box, QColor(0, 0, 0, 160))
            painter.setPen(QColor(255, 255, 0))
            painter.drawText(box, Qt.AlignCenter, label_text)
        if scalebar_um_per_px:
            bar_um = _nice_scalebar_um(w * scalebar_um_per_px)
            bar_px = int(round(bar_um / scalebar_um_per_px))
            margin = max(10, w // 60)
            thick = max(3, h // 150)
            x1, y1 = w - margin, h - margin
            painter.fillRect(QRect(x1 - bar_px, y1 - thick, bar_px, thick), QColor(255, 255, 255))
            font = QFont()
            font.setPointSize(max(12, w // 50))
            painter.setFont(font)
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(QRect(x1 - bar_px, y1 - thick - 4 * font.pointSize(), bar_px, 3 * font.pointSize()),
                             Qt.AlignCenter | Qt.AlignBottom, f"{bar_um:g} um")
        painter.end()
    return pix


def _nice_scalebar_um(fov_um: float) -> float:
    """A 1-2-5 length about a fifth of the field of view."""
    target = max(fov_um / 5.0, 1e-9)
    exp = np.floor(np.log10(target))
    for m in (1, 2, 5, 10):
        if m * 10 ** exp >= target:
            return float(m * 10 ** exp)
    return float(10 ** (exp + 1))
