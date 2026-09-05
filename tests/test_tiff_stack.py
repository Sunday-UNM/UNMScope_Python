"""TIFF stack saving the way LouisXIV does it."""
import numpy as np
import tifffile

from unmscope.fileio.tiff_stack import (
    COMPANION_FILENAME, read_tiff_stack, save_tiff_stack, stack_filename, stack_path, to_u16,
    write_acq_info,
)


def test_filename_follows_build_image_path():
    assert stack_filename("exp", 1, 0) == "exp_CH01_000000.tif"
    assert stack_filename("exp", 12, 345) == "exp_CH12_000345.tif"


def test_position_folder_only_when_separate_folders_is_on(tmp_path):
    p = stack_path(tmp_path, "exp", 0, 0, position=2, separate_position_folders=True)
    assert p.parent.name == "position 2" and p.name == "exp_CH00_000000.tif"
    q = stack_path(tmp_path, "exp", 0, 0, position=2, separate_position_folders=False)
    assert q.parent == tmp_path


def test_saved_stack_is_u16_uncompressed_multipage_with_ome_on_first_page(tmp_path):
    stack = (np.random.default_rng(1).random((5, 6, 7)) * 3000).astype(np.float32)
    out = save_tiff_stack(tmp_path / "s_CH00_000000.tif", stack, ome=True,
                          pixel_size_um=0.2168, z_step_um=1.0, channel_name="488")
    assert out.exists()
    with tifffile.TiffFile(str(out)) as tf:
        assert len(tf.pages) == 5                          # one page per slice
        page = tf.pages[0]
        assert page.dtype == np.uint16                     # Convert to U16
        assert page.compression == 1                       # no compression
        assert tf.is_ome                                   # OME-XML in the first ImageDescription
        assert "OME" in (page.description or "")
        assert "488" in tf.ome_metadata
    back = read_tiff_stack(out)
    np.testing.assert_array_equal(back, to_u16(stack))


def test_plain_tiff_when_ome_off(tmp_path):
    out = save_tiff_stack(tmp_path / "s.tif", np.zeros((3, 4, 4), np.uint16), ome=False)
    with tifffile.TiffFile(str(out)) as tf:
        assert len(tf.pages) == 3 and not tf.is_ome


def test_companion_acq_info(tmp_path):
    out = write_acq_info(tmp_path, {"Exposure (ms)": 100.0, "Mode": "Z stack",
                                    "Camera": {"Model": "C11440-42U32", "Serial": "102668"}})
    assert out.name == COMPANION_FILENAME
    text = out.read_text(encoding="utf-8")
    assert "Exposure (ms) = 100.0" in text
    assert "[Camera]" in text and "Serial = 102668" in text
