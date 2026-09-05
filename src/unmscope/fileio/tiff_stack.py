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
from pathlib import Path

import numpy as np
import tifffile

TIFF_EXTENSION = "tif"          # Image File Type to File Extension.vi: "TIFF" -> "tif"
COMPANION_FILENAME = "AcqInfo.txt"


def stack_filename(base: str, channel: int, timepoint: int, ext: str = TIFF_EXTENSION) -> str:
    """LouisXIV's ``_CH%02d_%06d`` naming (Build Image Path.vi)."""
    return f"{base}_CH{int(channel):02d}_{int(timepoint):06d}.{ext}"


def stack_path(folder: str | Path, base: str, channel: int, timepoint: int,
               position: int | None = None, separate_position_folders: bool = False) -> Path:
    """Full path for a stack; ``position %d`` subfolder only when the
    multi-position-separate-folders setting is on, as in LouisXIV."""
    folder = Path(folder)
    if position is not None and separate_position_folders:
        folder = folder / f"position {int(position)}"
    return folder / stack_filename(base, channel, timepoint)


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


def write_acq_info(folder: str | Path, settings: dict) -> Path:
    """The ``AcqInfo.txt`` companion: one ``key = value`` line per setting,
    with a timestamp header. Nested dicts become ``[section]`` blocks, the
    style of SPIMProject.ini."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / COMPANION_FILENAME
    lines = [f"# UNMScope acquisition info  {datetime.datetime.now().isoformat(timespec='seconds')}"]
    flat = {k: v for k, v in settings.items() if not isinstance(v, dict)}
    for k, v in flat.items():
        lines.append(f"{k} = {v}")
    for k, v in settings.items():
        if isinstance(v, dict):
            lines.append("")
            lines.append(f"[{k}]")
            for k2, v2 in v.items():
                lines.append(f"{k2} = {v2}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def read_tiff_stack(path: str | Path) -> np.ndarray:
    """Read a stack back as (n, H, W) -- for tests and the viewer."""
    data = tifffile.imread(str(path))
    return data[None, ...] if data.ndim == 2 else data
