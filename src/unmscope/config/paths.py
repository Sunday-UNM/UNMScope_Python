"""Where UNMScope keeps its own settings: the user's ~/.unmscope, or the
directory named by the UNMSCOPE_HOME environment variable (tests point it
at a temporary directory so nothing touches the real home). LouisXIV's own
files under UNMScope_Source are read-only inputs everywhere."""
from __future__ import annotations

import os
from pathlib import Path


def user_dir() -> Path:
    override = os.environ.get("UNMSCOPE_HOME")
    return Path(override) if override else Path.home() / ".unmscope"


def user_ini() -> Path:
    """UNMScope's copy of SPIMProject.ini (created from LouisXIV's on first use
    by whichever module needs it first)."""
    return user_dir() / "SPIMProject.ini"
