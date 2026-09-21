"""Where UNMScope keeps its own settings: the user's ~/.unmscope, or the
directory named by the UNMSCOPE_HOME environment variable (tests point it
at a temporary directory so nothing touches the real home). LouisXIV's own
files under UNMScope_Source are read-only inputs everywhere."""
from __future__ import annotations

import os
import shutil
from pathlib import Path


def user_dir() -> Path:
    override = os.environ.get("UNMSCOPE_HOME")
    return Path(override) if override else Path.home() / ".unmscope"


#: LouisXIV's install (read-only input everywhere; Python never writes here).
LOUISXIV_ROOT = Path(r"D:\UNM_Lightsheet\UNMScope_Source")
LOUISXIV_SUPPORT_DIR = LOUISXIV_ROOT / "SPIM" / "SPIM Support files"

# ---------------------------------------------------------------------------
# FPGA bitfile -- searched in order; first path that exists wins.
# The canonical LouisXIV location is under LOUISXIV_ROOT / bin / data /,
# but on this workstation the bitfile lives under C:\UnmScopeOpen\fpga\.
# ---------------------------------------------------------------------------
_BITFILE_NAME = "SPIMFPGAProject_SPIM_MAIN_VI.lvbitx"
_BITFILE_CANDIDATES = [
    # Canonical LouisXIV install location (primary)
    LOUISXIV_ROOT / "bin" / "data" / _BITFILE_NAME,
    # UNMScope open-source versioned builds -- use the newest hardware build
    # (v0_3_4) whose register names match what fpga_trigger.py expects.
    # The bare C:\UnmScopeOpen\fpga\ copy is an older bitfile with different
    # AOTF register names ("AOTF0" instead of "AOTF ch 0") and must NOT be
    # used.
    Path(r"C:\UnmScopeOpen\microscope_gui_hardware_v0_3_4\hardware") / _BITFILE_NAME,
    Path(r"C:\UnmScopeOpen\microscope_gui_hardware_v0_3_3\hardware") / _BITFILE_NAME,
    Path(r"C:\UnmScopeOpen\microscope_gui_hardware_v0_3_2\hardware") / _BITFILE_NAME,
    Path(r"C:\UnmScopeOpen\microscope_gui_hardware_v0_3_1\hardware") / _BITFILE_NAME,
]
FPGA_BITFILE: Path = next(
    (p for p in _BITFILE_CANDIDATES if p.exists()),
    _BITFILE_CANDIDATES[0],   # fall back to original so errors stay meaningful
)


def user_ini() -> Path:
    """UNMScope's copy of SPIMProject.ini (created from LouisXIV's on first use
    by whichever module needs it first)."""
    return user_dir() / "SPIMProject.ini"


def ensure_user_copy(source: Path, dest: Path, *, if_missing: str = "skip") -> Path:
    """Create ``dest`` as a byte-for-byte copy of ``source`` (LouisXIV's file)
    on first use; an existing ``dest`` is never touched. The four UNMScope
    modules that own a slice of SPIMProject.ini (spim_ini, hw_config,
    um_per_volt, fileio.stage_locations) all had their own copy of this
    "copy on first use" logic with a slightly different policy for a missing
    source -- this is the one copy, parametrised by that policy:

    - ``"skip"``  (default): return ``dest`` and create nothing.
    - ``"empty"``: write an empty ``dest`` so the app still starts.
    - ``"raise"``: raise ``FileNotFoundError``.
    """
    dest, source = Path(dest), Path(source)
    if dest.exists():
        return dest
    if source.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    elif if_missing == "empty":
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"")
    elif if_missing == "raise":
        raise FileNotFoundError(f"neither {dest} nor LouisXIV's {source} exists")
    # "skip": dest stays absent; callers fall back to in-memory defaults.
    return dest
