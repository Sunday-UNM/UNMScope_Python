"""TIFF stack saving the way LouisXIV does it."""
import numpy as np
import tifffile

from unmscope.fileio.tiff_stack import (
    COMPANION_FILENAME, clean_base_filename, position_folder_name, read_tiff_stack,
    save_tiff_stack, stack_filename, stack_path, to_u16, write_acq_info,
)


def test_filename_follows_build_image_path():
    assert stack_filename("exp", 1, 0) == "exp_CH01_000000.tif"
    assert stack_filename("exp", 12, 345) == "exp_CH12_000345.tif"


def test_position_folder_only_when_separate_folders_is_on(tmp_path):
    p = stack_path(tmp_path, "exp", 0, 0, position=2, separate_position_folders=True)
    assert p.parent.name == "position 3" and p.name == "exp_CH00_000000.tif"
    q = stack_path(tmp_path, "exp", 0, 0, position=2, separate_position_folders=False)
    assert q.parent == tmp_path


def test_the_position_folder_is_one_based():
    """`Build Image Path.vi` increments Position Index before "position %d".

    So index 0 is the folder "position 1". We had been writing the raw index,
    one folder number low the whole way.
    """
    assert position_folder_name(0) == "position 1"
    assert position_folder_name(9) == "position 10"


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


def test_companion_acq_info_is_louisxivs_format(tmp_path):
    """LouisXIV's keys, order and per-field formats.

    From the 26 hidden case frames of `Companion Metadata Cluster to
    String.vi` (2026-09-05). The formats genuinely differ per field --
    %.3f for sizes, %.4f for the stage angle alone, %f for the two time
    fields -- so this pins each one.
    """
    out = write_acq_info(tmp_path, {
        "SizeX_px": 2048, "SizeY_px": 1024, "SizeZ_px": 10,
        "PhysicalSizeX_um": 0.21666666, "PhysicalSizeZ_um": 1.0,
        "Timepoints": 1, "AOTFCycleMode": "per Z", "TimeIncrement_s": 0.1333,
        "Username": "tonmoy", "Fluor": ["GFP", "RFP"],
        "ExcitationWavelength_nm": [488, 561], "EmissionWavelength_nm": [],
        "CamExposure_s": 0.1, "Multi-positionAcq": False, "StageAngle_deg": 45.0,
    })
    assert out.name == COMPANION_FILENAME
    lines = out.read_text(encoding="utf-8").splitlines()
    assert "SizeX_px = 2048" in lines                    # %d
    assert "PhysicalSizeX_um = 0.217" in lines           # %.3f, not %.4f
    assert "TimeIncrement_s = 0.133300" in lines         # %f -> six decimals
    assert "CamExposure_s = 0.100000" in lines
    assert 'Username = "tonmoy"' in lines                # strings are quoted
    assert 'Fluor = "GFP","RFP"' in lines                # quoted, comma-joined
    assert "ExcitationWavelength_nm = 488,561" in lines  # bare, comma-joined
    assert "EmissionWavelength_nm = " in lines           # empty array -> empty
    assert "Multi-positionAcq = FALSE" in lines          # LabVIEW's spelling
    # skip?: the four position fields are omitted unless multi-position
    assert not any(l.startswith(("PositionX_mm", "StageAngle_deg")) for l in lines)
    # the order is the enum's, not the cluster's: Timepoints comes before
    # TimeIncrement_s here, where the cluster has TimeIncrement_s much earlier
    assert lines.index("Timepoints = 1") < lines.index("TimeIncrement_s = 0.133300")


def test_position_fields_appear_only_for_a_multi_position_acquisition(tmp_path):
    out = write_acq_info(tmp_path, {
        "Multi-positionAcq": True, "PositionX_mm": 1.2345, "PositionY_mm": -2.5,
        "PositionZ_mm": 0.0, "StageAngle_deg": 45.0,
    })
    lines = out.read_text(encoding="utf-8").splitlines()
    assert "Multi-positionAcq = TRUE" in lines
    assert "PositionX_mm = 1.234" in lines               # %.3f
    assert "PositionY_mm = -2.500" in lines
    assert "StageAngle_deg = 45.0000" in lines           # %.4f, the only one


def test_our_extras_go_in_their_own_block_below_louisxivs_fields(tmp_path):
    """The user's call: keep what LouisXIV's format cannot carry, fenced off."""
    out = write_acq_info(tmp_path, {"SizeX_px": 2048},
                         {"Trigger mode": "SYNCREADOUT", "Camera serial": "102668"})
    text = out.read_text(encoding="utf-8")
    head, sep, extras = text.partition("[UNMScope]")
    assert sep, "the extras block should be present"
    assert "SizeX_px = 2048" in head and "Trigger mode" not in head
    assert "Trigger mode = SYNCREADOUT" in extras and "Camera serial = 102668" in extras


def test_no_extras_means_no_block(tmp_path):
    out = write_acq_info(tmp_path, {"SizeX_px": 2048})
    assert "[UNMScope]" not in out.read_text(encoding="utf-8")


def test_base_filename_is_cleaned_from_whatever_the_save_dialog_returns():
    """LouisXIV asks for a FILE; Build Image Path appends to the name typed."""
    assert clean_base_filename("H:/Data/beads.tif") == "beads"
    assert clean_base_filename("H:/Data/beads_200nm.tiff") == "beads_200nm"
    assert clean_base_filename("beads") == "beads"
    # re-picking a previously written stack should give the base back, not
    # "beads_CH00_000000", which would then gain a second suffix
    assert clean_base_filename("beads_CH00_000000.tif") == "beads"
    # never produce an empty or unusable name
    assert clean_base_filename("") == "img"
    assert clean_base_filename("   ") == "img"
    assert clean_base_filename('bad:name?.tif') == "badname"
    assert clean_base_filename(".tif", fallback="cells") == "cells"
