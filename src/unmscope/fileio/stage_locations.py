"""LouisXIV's two stage-location files, read and written exactly as
``Motion/3D Stage/Load Location File.vi`` and ``Location Sequence FG.vi``
(+ the Sample Stage Control GUI's 'Save Locations File' case) do, so the
files stay interchangeable with LouisXIV.

Files (``HHMI - Filepaths global.vi``: <project>/SPIM Support files/...):

* ``SPIMProject 3D Stage Locations.txt`` -- the Saved Locations table.
  Columns: lock symbol (41 = unlocked, 42 = locked: the 'Custom Item Symbols'
  indices the GUI installs on its listbox), name, x, y, z, rel_offset, theta.
* ``SPIMProject 3D Stage Sequence Locations.txt`` -- the Location Sequence
  (multi-position list). Columns: running # (rewritten 1..N on every Write),
  name, x, y, z, rel_offset, theta.

Format (both, verified against the backup copies byte for byte): tab
separated, CRLF line ends, a CRLF after the last row, no header, values as
the strings the tables hold ('1.00', 'NaN', '25.0000'), i.e. LabVIEW's
``Array To Spreadsheet String`` with format ``%s``.

Load Location File.vi: reads the whole file as strings; 7 columns -> OK,
5 or 6 columns -> a column holding ``--`` is appended ("5 cols found, add
6th"), any other count -> the rows are thrown out ("invalid # cols"); a
missing file is not an error (error 7 cleared) and gives no rows. NaN /
non-numeric cells read back as NaN ("treat non numerics as NaN").

Values written by the Save Location Dialog: X/Y/Z with 2 decimals, Theta
with 4, Z Rel. Offset with LabVIEW's default 6, and ``NaN`` when 'Save Rel.
Offset' / 'Save Theta' are unchecked (``format_location_row``).

Persistence rule: LouisXIV's own files are read-only input; the panel works
on copies under ``~/.unmscope`` made on first use (``ensure_user_copy``).
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Callable

from unmscope.config.paths import LOUISXIV_SUPPORT_DIR
from unmscope.hardware.stage import Vec3, calc_max_move_time_s

SUPPORT_DIR = LOUISXIV_SUPPORT_DIR
LOCATIONS_FILENAME = "SPIMProject 3D Stage Locations.txt"
SEQUENCE_FILENAME = "SPIMProject 3D Stage Sequence Locations.txt"
DEFAULT_LOCATIONS_FILE = SUPPORT_DIR / LOCATIONS_FILENAME
DEFAULT_SEQUENCE_FILE = SUPPORT_DIR / SEQUENCE_FILENAME

USER_DIR = Path.home() / ".unmscope"

LOCK_SYMBOL = 42       # 'Lock Symbol Index' constant on the GUI diagram
UNLOCK_SYMBOL = 41     # 'Unlock Symbol Index'
MISSING_CELL = "--"    # what Load Location File appends for a short row
N_COLUMNS = 7
EOL = "\r\n"


def user_locations_path() -> Path:
    return USER_DIR / LOCATIONS_FILENAME


def user_sequence_path() -> Path:
    return USER_DIR / SEQUENCE_FILENAME


def ensure_user_copy(source: Path, dest: Path) -> Path:
    """Copy LouisXIV's file to the UNMScope location on first use (byte for
    byte); a missing source gives no file, which loads as 'no rows'."""
    from unmscope.config.paths import ensure_user_copy as _ensure_user_copy
    return _ensure_user_copy(source, dest)


# -- Load Location File.vi ----------------------------------------------------
def load_location_file(path: Path) -> tuple[list[list[str]], bool]:
    """Rows of 7 strings and 'File Loaded?'. See the module docstring."""
    path = Path(path)
    if not path.exists():
        return [], False
    text = path.read_bytes().decode("utf-8", errors="replace")
    rows: list[list[str]] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line == "":
            continue
        cells = line.split("\t")
        if len(cells) == N_COLUMNS:
            rows.append(cells)
        elif len(cells) in (5, 6):
            cells = cells + [MISSING_CELL]
            while len(cells) < N_COLUMNS:
                cells.append("")
            rows.append(cells)
        # any other column count: "invalid # cols, throw out"
    return rows, True


def save_location_file(path: Path, rows: list[list[str]]) -> None:
    """``Array To Spreadsheet String`` (%s, tab, CRLF) + Write to Text File."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join("\t".join(r) + EOL for r in rows)
    path.write_bytes(text.encode("utf-8"))


def cell_to_float(cell: str) -> float:
    """'treat non numerics as NaN' (LabVIEW Fract/Exp String To Number with a
    NaN default)."""
    try:
        return float(cell.strip())
    except (ValueError, AttributeError):
        return math.nan


def fmt(value: float, decimals: int) -> str:
    """LabVIEW 'Number To Fractional String': NaN prints as 'NaN'."""
    if value is None or math.isnan(value):
        return "NaN"
    return f"{value:.{decimals}f}"


def format_location_row(name: str, x: float, y: float, z: float, rel_offset: float | None,
                        theta: float | None) -> list[str]:
    """Save Location Dialog's 'Row out': [Name, X, Y, Z (precision 2),
    Rel. Offset (precision 6) or NaN, Theta (precision 4) or NaN]. Pass None
    for an unchecked 'Save Rel. Offset' / 'Save Theta'."""
    return [name, fmt(x, 2), fmt(y, 2), fmt(z, 2),
            "NaN" if rel_offset is None else fmt(rel_offset, 6),
            "NaN" if theta is None else fmt(theta, 4)]


# -- the Saved Locations table ---------------------------------------------------
class SavedLocations:
    """The GUI's 'Saved Locations' listbox: item names (6 columns: name, x,
    y, z, rel_offset, theta) plus the lock symbol per row. Every mutating
    method is one consumer case of the GUI; ``save()`` is 'Save Locations
    File' (called by LouisXIV after every mutation)."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else user_locations_path()
        self.rows: list[list[str]] = []
        self.locked: list[bool] = []
        self.loaded = False

    def load(self) -> bool:
        """'Load Locations File': column 0 -> symbols, the rest -> names."""
        rows, loaded = load_location_file(self.path)
        self.rows = [r[1:] for r in rows]
        self.locked = [int(cell_to_float(r[0]) if cell_to_float(r[0]) == cell_to_float(r[0]) else 0) == LOCK_SYMBOL
                       for r in rows]
        self.loaded = loaded
        return loaded

    def save(self) -> None:
        """'Save Locations File': symbol index as a decimal string in column 0."""
        save_location_file(self.path, [[str(LOCK_SYMBOL if lk else UNLOCK_SYMBOL)] + r
                                       for r, lk in zip(self.rows, self.locked)])

    def __len__(self) -> int:
        return len(self.rows)

    def insert(self, row: list[str]) -> int:
        """'Insert Location': append the row, unlocked; returns its index."""
        self.rows.append(list(row))
        self.locked.append(False)
        return len(self.rows) - 1

    def update(self, index: int, row: list[str]) -> None:
        """'Update Location': replace the selected row's names (lock kept)."""
        self.rows[index] = list(row)

    def remove(self, index: int) -> None:
        """'Remove'."""
        del self.rows[index]
        del self.locked[index]

    def remove_all_unlocked(self) -> None:
        """'Remove All': "Remove ALL unlocked saved locations?" -- locked rows survive."""
        keep = [(r, lk) for r, lk in zip(self.rows, self.locked) if lk]
        self.rows = [r for r, _ in keep]
        self.locked = [lk for _, lk in keep]

    def toggle_lock(self, index: int) -> bool:
        """'Toggle Lock' (double-click on the symbol column)."""
        self.locked[index] = not self.locked[index]
        return self.locked[index]

    def xyz(self, index: int) -> Vec3:
        r = self.rows[index]
        return (cell_to_float(r[1]), cell_to_float(r[2]), cell_to_float(r[3]))

    def rel_offset_and_theta(self, index: int) -> tuple[float, float]:
        r = self.rows[index]
        return cell_to_float(r[4]), cell_to_float(r[5])


# -- Location Sequence FG.vi ------------------------------------------------------
class LocationSequence:
    """The multi-position list (``Location Sequence FG.vi``: Load | Write |
    Read), shared between the stage panel and, later, the acquisition
    (``Sample Stage - Move to Stack Index.vi``). Rows are 7 strings; column 0
    is renumbered 1..N on every ``write`` ("rewrite # col is correct").
    ``changed`` callbacks fire after every write (the GUI's 'Configure Stack'
    message to SPIM MAIN)."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else user_sequence_path()
        self.rows: list[list[str]] = []
        self.loaded = False
        self._listeners: list[Callable[["LocationSequence"], None]] = []

    def add_listener(self, fn: Callable[["LocationSequence"], None]) -> None:
        self._listeners.append(fn)

    def _notify(self) -> None:
        for fn in list(self._listeners):
            fn(self)

    def load(self) -> list[list[str]]:
        """'Load': read the sequence file (missing file -> empty)."""
        self.rows, self.loaded = load_location_file(self.path)
        return self.read()

    def read(self) -> list[list[str]]:
        return [list(r) for r in self.rows]

    def write(self, rows: list[list[str]]) -> list[list[str]]:
        """'Write': replace the list, renumber column 0, rewrite the file."""
        self.rows = [[str(i + 1)] + list(r[1:]) for i, r in enumerate(rows)]
        save_location_file(self.path, self.rows)
        self._notify()
        return self.read()

    # -- the GUI's sequence cases --------------------------------------------------------
    def append_saved(self, saved_row: list[str]) -> None:
        """'Add to Sequence': a Saved Locations row (6 columns) gets a dummy
        '#' column in front and is appended."""
        self.write(self.rows + [["#"] + list(saved_row)])

    def remove(self, index: int) -> None:
        """'Remove Sequence'."""
        rows = self.read()
        del rows[index]
        self.write(rows)

    def remove_all(self) -> None:
        """'Sequence Remove All'."""
        self.write([])

    # -- the readers (Location Sequence FG 'Read' outputs) ---------------------------------
    def __len__(self) -> int:
        return len(self.rows)

    def positions_um(self) -> list[Vec3]:
        """'Location Sequence out (XYZ array)': columns 2..4."""
        return [(cell_to_float(r[2]), cell_to_float(r[3]), cell_to_float(r[4])) for r in self.rows]

    def rel_offsets_um(self) -> list[float]:
        """'Relative Offsets (um)': column 5, NaN when not numeric."""
        return [cell_to_float(r[5]) if len(r) > 5 else math.nan for r in self.rows]

    def thetas_deg(self) -> list[float]:
        """'Theta (deg)': column 6."""
        return [cell_to_float(r[6]) if len(r) > 6 else math.nan for r in self.rows]

    def max_move_time_s(self, velocity_um_s: float, settling_ms: float, enabled: bool = True) -> float:
        """'Max Move Time (s)' = Calculate Max Move Time over the positions."""
        return calc_max_move_time_s(self.positions_um(), velocity_um_s, settling_ms, enabled)

    def xyz(self, index: int) -> Vec3:
        return self.positions_um()[index]

    def rel_offset_and_theta(self, index: int) -> tuple[float, float]:
        r = self.rows[index]
        return (cell_to_float(r[5]) if len(r) > 5 else math.nan,
                cell_to_float(r[6]) if len(r) > 6 else math.nan)
