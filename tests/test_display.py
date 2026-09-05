"""Images-tab display: LouisXIV's Scale / palette mapping, frames-to-average,
rendering (headless Qt)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from unmscope.gui.display import (
    CAMERA_MAX_COUNTS, FrameAverager, PALETTES, _nice_scalebar_um, display_range, map_to_8bit,
    palette_lut, render_frame,
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_scale_cases_follow_set_user_palette():
    f = np.array([[100, 200], [300, 4000]], dtype=np.uint16)
    assert display_range(f, autoscale=True, scale_to_counts=False, max_counts=1000) == (100.0, 4000.0)   # Full Dynamic
    assert display_range(f, autoscale=False, scale_to_counts=True, max_counts=1000) == (0.0, 1000.0)     # By constant
    assert display_range(f, autoscale=False, scale_to_counts=False, max_counts=1000) == (0.0, float(CAMERA_MAX_COUNTS))
    flat = np.full((4, 4), 7, dtype=np.uint16)
    lo, hi = display_range(flat, autoscale=True, scale_to_counts=False, max_counts=0)
    assert hi > lo                                                   # never a zero-width range


def test_16bit_mapping_clips():
    f = np.array([[0, 500, 1000, 2000]], dtype=np.uint16)
    assert map_to_8bit(f, 0, 1000).tolist() == [[0, 128, 255, 255]]


def test_palettes():
    for name in PALETTES:
        lut = palette_lut(name)
        assert lut.shape == (256, 3) and lut.dtype == np.uint8
    assert palette_lut("Gray")[0].tolist() == [0, 0, 0] and palette_lut("Gray")[255].tolist() == [255, 255, 255]
    rb = palette_lut("Rainbow")
    assert rb[0].tolist() == [0, 0, 255] and rb[255].tolist() == [255, 0, 0] and rb[128].tolist() == [0, 255, 0]


def test_frame_averager():
    av = FrameAverager(3)
    a = np.full((2, 2), 100, np.uint16); b = np.full((2, 2), 300, np.uint16)
    assert av.push(a)[0, 0] == 100
    assert av.push(b)[0, 0] == 200
    assert av.push(b)[0, 0] == 233                                   # mean of 100, 300, 300
    av.set_n(1)
    assert av.push(a)[0, 0] == 100
    av.set_n(2)
    assert av.push(np.zeros((3, 3), np.uint16)).shape == (3, 3)      # shape change resets the buffer


def test_render_frame_palette_and_overlays(app):
    f = (np.random.default_rng(1).integers(0, 4000, size=(64, 80))).astype(np.uint16)
    for name in PALETTES:
        pix = render_frame(f, palette=name, label_text="Frame #1", scalebar_um_per_px=0.2167)
        assert pix.width() == 80 and pix.height() == 64
    pix = render_frame(f, palette="Rainbow", lo=0, hi=4000)
    assert not pix.isNull()


def test_nice_scalebar():
    assert _nice_scalebar_um(443.7) == 100.0
    assert _nice_scalebar_um(50.0) == 10.0
    assert _nice_scalebar_um(12.0) == 5.0
