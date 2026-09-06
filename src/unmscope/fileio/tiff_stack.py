"""Save an acquired stack the way LouisXIV does.

From the LabVIEW source (block diagrams in H:\\UNM_Lightsheet\\VI_Diagrams):

- ``HHMI - SPIM Save Image.vi`` -> ``Tiff reader.lvlib:Save multiple images
  with metadata to new tiff stack.vi``: every slice converted to **U16**,
  written as one **multi-page TIFF** (first page saved, its IFD found, each
  further page appended), **no compression**, and with **OME-XML written into
  the ImageDescription tag of the first page** when metadata is on. The user
  manual: "Save File Type: TIFF (default). Save OME-XML: only valid when File
  Type = TIFF."
- ``Build Image Path.vi``: the file is ``<base>_CH%02d_%06d.<ext>`` (channel
  index, timepoint index), with ``Image File Type to File Extension.vi``
  giving ``tif`` for TIFF. With "Save Multi-position separate folders" on,
  a ``position %d`` subfolder is inserted. ``Convert tiff filename to
  channel-time-and-z.vi`` parses the same pattern back (Z starts at 0: all
  the slices of a stack live inside the one file).
- ``Write Companion Metadata File.vi``: an ``AcqInfo.txt`` written next to
  the images, built from the acquisition settings.
"""
from __future__ import annotations

import datetime
import re
from pathlib import Path

import numpy as np
import tifffile

TIFF_EXTENSION = "tif"          # Image File Type to File Extension.vi: "TIFF" -> "tif"
COMPANION_FILENAME = "AcqInfo.txt"


def stack_filename(base: str, channel: int, timepoint: int, ext: str = TIFF_EXTENSION) -> str:
    """LouisXIV's ``<base>_CH%02d_%06d.<ext>`` naming (Build Image Path.vi)."""
    return f"{base}_CH{int(channel):02d}_{int(timepoint):06d}.{ext}"


def position_folder_name(position: int) -> str:
    """``position %d`` -- **1-based**.

    `Build Image Path.vi` increments the Position Index before formatting it,
    so position index 0 is the folder ``position 1``. We had been writing the
    index straight through, one folder number low the whole way.
    """
    return f"position {int(position) + 1}"


def stack_path(folder: str | Path, base: str, channel: int, timepoint: int,
               position: int | None = None, separate_position_folders: bool = False) -> Path:
    """Full path for a stack.

    The ``position %d`` subfolder appears only when the multi-position
    separate-folders setting is on, as in LouisXIV, where that folder is
    gated on the "Save Multi-position separate folders" global.

    Not ported: the VI's ``Raw?`` input, which inserts a ``RAW`` element into
    the path for LouisXIV's autosave-raw feature. We do not save raw images.
    """
    folder = Path(folder)
    if position is not None and separate_position_folders:
        folder = folder / position_folder_name(position)
    return folder / stack_filename(base, channel, timepoint)


#: Characters Windows forbids in a filename, plus the ones that would collide
#: with the ``_CH00_000000`` suffix if a user typed them.
_BAD_BASE_CHARS = '<>:"/\\|?*'


def clean_base_filename(name: str, *, fallback: str = "img") -> str:
    """A usable base from whatever the save dialog returned.

    The user types a file name, so it usually arrives with an extension and
    may arrive with a suffix this code is about to add again. Strip both, and
    refuse to produce an empty base.
    """
    stem = Path(str(name).strip()).name
    for ext in (".tif", ".tiff", ".ome.tif"):
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break
    # a re-picked file like "beads_CH00_000000" should give back "beads"
    stem = re.sub(r"_CH\d{2}_\d{6}$", "", stem)
    stem = "".join(c for c in stem if c not in _BAD_BASE_CHARS).strip()
    return stem or fallback


def to_u16(stack: np.ndarray) -> np.ndarray:
    """LouisXIV's 'Convert to U16?' = True on every saved image."""
    a = np.asarray(stack)
    if a.dtype == np.uint16:
        return a
    return np.clip(np.rint(a), 0, 65535).astype(np.uint16)


def save_tiff_stack(path: str | Path, stack: np.ndarray, *, ome: bool = True,
                    pixel_size_um: float | None = None, z_step_um: float | None = None,
                    channel_name: str | None = None) -> Path:
    """Write ``stack`` (n, H, W) as a U16, uncompressed, multi-page TIFF with
    OME-XML in the first page's ImageDescription (``ome=True``), creating the
    folder tree like ``Save multiple images...`` does. Returns the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = to_u16(stack)
    if data.ndim == 2:
        data = data[None, ...]
    if data.ndim != 3:
        raise ValueError("stack must be (n, H, W) or (H, W)")
    if ome:
        meta = {"axes": "ZYX"}
        if pixel_size_um:
            meta["PhysicalSizeX"] = float(pixel_size_um)
            meta["PhysicalSizeY"] = float(pixel_size_um)
            meta["PhysicalSizeXUnit"] = meta["PhysicalSizeYUnit"] = "µm"
        if z_step_um:
            meta["PhysicalSizeZ"] = float(z_step_um)
            meta["PhysicalSizeZUnit"] = "µm"
        if channel_name:
            meta["Channel"] = {"Name": str(channel_name)}
        tifffile.imwrite(str(path), data, photometric="minisblack", compression=None,
                         ome=True, metadata=meta)
    else:
        tifffile.imwrite(str(path), data, photometric="minisblack", compression=None)
    return path


# -- AcqInfo.txt -------------------------------------------------------------
#
# LouisXIV's format, read field by field off the 26 hidden case frames of
# `Companion Metadata Cluster to String.vi` (2026-09-05). The VI is a
# for-each over `Companion Metadata File Fields Enum`, one line per field,
# in the order below -- which is NOT the order of the cluster it reads, and
# two of whose key names differ from their cluster fields (`AOTFCycleMode`
# for "AOTF cycle mode", `StageAngle_deg` for `Angle_deg`).
#
# The per-field format really does vary; do not "tidy" these into one:
#   %d      integers
#   %.3f    physical sizes and stage positions
#   %.4f    the stage angle, and only that
#   %f      LabVIEW's default 6 decimals, for the two time fields
#   "%s"    strings, quoted
#   arrays  elements formatted individually and joined with a COMMA, quoted
#           for strings ("%s") and bare for the wavelengths (%d)
#   TRUE/FALSE  booleans, as LabVIEW's Format Into String renders them
#
#: (key, kind) in the enum's order. See ``_format_acq_value`` for the kinds.
ACQ_INFO_FIELDS: tuple[tuple[str, str], ...] = (
    ("SizeX_px", "int"), ("SizeY_px", "int"), ("SizeZ_px", "int"),
    ("PhysicalSizeX_um", "f3"), ("PhysicalSizeY_um", "f3"), ("PhysicalSizeZ_um", "f3"),
    ("Timepoints", "int"), ("Cameras", "int"), ("Channels", "int"),
    ("AOTFCycleMode", "text"), ("TimeIncrement_s", "f6"),
    ("Username", "str"), ("CellLabeling", "str"), ("CellType", "str"),
    ("ExperimentDescription", "str"),
    ("Fluor", "strs"), ("ExcitationWavelength_nm", "ints"),
    ("EmissionWavelength_nm", "ints"), ("FilterType", "str"),
    ("CamExposure_s", "f6"), ("Multi-positionAcq", "bool"),
    ("PositionX_mm", "f3"), ("PositionY_mm", "f3"), ("PositionZ_mm", "f3"),
    ("StageAngle_deg", "f4"),
)

#: Written only when ``Multi-positionAcq`` is true. Each of these four cases
#: wires Multi-positionAcq through a NOT into the VI's ``skip?`` output, so
#: a single-position acquisition omits the lines rather than writing zeros.
ACQ_INFO_POSITION_FIELDS = frozenset(
    {"PositionX_mm", "PositionY_mm", "PositionZ_mm", "StageAngle_deg"})

#: Heading for the block of our own settings that LouisXIV's format has no
#: home for. Kept clearly separate so the file above it stays LouisXIV's.
ACQ_INFO_EXTRAS_SECTION = "UNMScope"


def _format_acq_value(value, kind: str) -> str:
    if kind == "int":
        return f"{int(value)}"
    if kind == "f3":
        return f"{float(value):.3f}"
    if kind == "f4":
        return f"{float(value):.4f}"
    if kind == "f6":
        return f"{float(value):f}"          # LabVIEW's %f: six decimals
    if kind == "str":
        return f'"{value}"'
    if kind == "text":                      # an enum's text, unquoted
        return f"{value}"
    if kind == "bool":
        return "TRUE" if value else "FALSE"
    if kind == "strs":
        return ",".join(f'"{v}"' for v in value)
    if kind == "ints":
        return ",".join(f"{int(v)}" for v in value)
    raise ValueError(f"unknown AcqInfo kind {kind!r}")


def render_acq_info(fields: dict, extras: dict | None = None) -> str:
    """LouisXIV's AcqInfo.txt text, optionally followed by our own block.

    ``fields`` are LouisXIV's keys; any the caller omits are left out. The
    four position fields are dropped unless ``Multi-positionAcq`` is true,
    which is what the VI's ``skip?`` flag does.
    """
    multi = bool(fields.get("Multi-positionAcq", False))
    lines: list[str] = []
    for key, kind in ACQ_INFO_FIELDS:
        if key not in fields:
            continue
        if key in ACQ_INFO_POSITION_FIELDS and not multi:
            continue
        lines.append(f"{key} = {_format_acq_value(fields[key], kind)}")
    if extras:
        lines.append("")
        lines.append(f"[{ACQ_INFO_EXTRAS_SECTION}]")
        lines.append("# Not part of LouisXIV's AcqInfo format: settings this")
        lines.append("# acquisition used that its fields have no home for.")
        lines.append(f"# Written {datetime.datetime.now().isoformat(timespec='seconds')}")
        for key, value in extras.items():
            lines.append(f"{key} = {value}")
    return "\n".join(lines) + "\n"


def write_acq_info(folder: str | Path, fields: dict, extras: dict | None = None) -> Path:
    """Write the ``AcqInfo.txt`` companion beside the images."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / COMPANION_FILENAME
    out.write_text(render_acq_info(fields, extras), encoding="utf-8")
    return out


def read_tiff_stack(path: str | Path) -> np.ndarray:
    """Read a stack back as (n, H, W) -- for tests and the viewer."""
    data = tifffile.imread(str(path))
    return data[None, ...] if data.ndim == 2 else data
