"""LouisXIV's "Microns to Volt calibrations" cluster and its ini persistence.

Port of (H:\\UNM_Lightsheet\\VI_Diagrams, plus the hidden-frame renders made
for the research pass):

- ``SPIM/SPIM LV8.6 VIs/Constants/HHMI - SPIM microns per volt calibrations.vi``
  -- the functional global behind the cluster. Read (first call): for every
  element of the typedef cluster (``Constants/Microns to Volt cluster.ctl``)
  it calls ``INI Read write dbl`` with section ``Microns to Volt
  calibrations`` and **key = the element's label text**, so the ini keys ARE
  the cluster labels, including the double space in
  ``Galvo position  cmd V/pos V``. Write: the same per element with the
  element's value.
- ``Common/Constants/HHMI - Microns per Volt.vi`` -- the accessor every
  consumer calls (20 callers). It unbundles the nine values and adds the
  derived ``Galvo Pos um/Galvo Pos Volt`` = ``Galvo cmd X um/X Volt`` x
  ``Galvo position  cmd V/pos V``.
- ``GUI/Microns per Volt Settings GUI.vi`` -- the dialog (see
  ``unmscope.gui.calibration_tab``).

Persistence (the user's decision): Python never writes LouisXIV's own
``SPIMProject.ini`` -- that file is read-only input. Anything that saves
settings works on a UNMScope-owned copy at ``~/.unmscope/SPIMProject.ini``:
on first use the LouisXIV file is copied there byte for byte
(:func:`ensure_unmscope_ini`), and only that copy is read and modified. The
copy is read with ``configparser`` (``optionxform=str``, ``interpolation=None``,
``strict=False``); :meth:`MicronsToVolt.save` rewrites ONLY the nine keys of
its own section, line by line, leaving every other byte of the file alone
(other sections and keys, CRLF line endings, the ``Key = Value`` spacing,
trailing spaces on the [Misc Settings] lines, and the ``# of beams`` /
``# Pts (Default)`` keys that a ``configparser.write`` round trip would drop
as comments). Numbers are written the way the live ini has them, ``%f``
style with six decimals, NaN as ``NaN``.

Deliberate differences from LouisXIV, both documented in
docs/um_per_volt_calibration.md: a missing key yields the cluster default in
memory but is NOT written back on read (``INI Read write dbl`` writes the
default into the file when ``Found? = FALSE``; here the file only changes on
Save, which writes all nine keys anyway); and the defaults themselves are the
saved control values of the cluster (53, 0.5, NaN, 53, -10, 1, 10, 10, 0 --
consistent across the GUI panel, the constants VI panel and the RemoteAcq
ini), the DefVal property being unreadable from a render.
"""
from __future__ import annotations

import configparser
import math
import re
import shutil
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from unmscope.config.calibration import Calibration

#: LouisXIV's own file: read-only input for Python (never written).
LOUISXIV_INI = Path(r"H:\UNM_Lightsheet\UNMScope_Source\SPIM\SPIM Support files\SPIMProject.ini")

#: The section the constants VI reads and writes (string constant in the VI).
INI_SECTION = "Microns to Volt calibrations"


def unmscope_ini_path(home: Path | None = None) -> Path:
    """The UNMScope-owned ini copy: ``<home>/.unmscope/SPIMProject.ini``."""
    return (home if home is not None else Path.home()) / ".unmscope" / "SPIMProject.ini"


def ensure_unmscope_ini(dest: Path | None = None, source: Path = LOUISXIV_INI) -> Path:
    """Create the UNMScope copy from LouisXIV's file on first use (byte for
    byte) and return its path. An existing copy is left untouched; if neither
    file exists the path is returned anyway (reads then fall back to
    defaults and the first Save creates the file with just this section)."""
    dest = Path(dest) if dest is not None else unmscope_ini_path()
    if not dest.exists() and Path(source).exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    return dest


def read_ini(path: Path) -> configparser.ConfigParser:
    """The repo's standard reader for SPIMProject-style files."""
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str        # keys are case-sensitive and contain spaces/slashes
    if Path(path).exists():
        cp.read(path, encoding="utf-8")
    return cp


def format_ini_double(v: float) -> str:
    """``Write Key (Double)`` as seen in the live ini: ``%f`` (six decimals);
    NaN/Inf spelled ``NaN`` / ``Inf`` / ``-Inf`` as LabVIEW prints them."""
    if math.isnan(v):
        return "NaN"
    if math.isinf(v):
        return "Inf" if v > 0 else "-Inf"
    return f"{v:.6f}"


def parse_ini_double(text: str) -> float:
    """Inverse of :func:`format_ini_double` (``float`` already accepts nan/inf
    in any case); raises ValueError on anything else."""
    return float(text.strip())


def _key(label: str, default: float, consumer: str) -> object:
    return field(default=default, metadata={"key": label, "consumer": consumer})


@dataclass
class MicronsToVolt:
    """The cluster, 1:1 in panel order. Each field's ``metadata['key']`` is the
    ini key (= the LabVIEW label), ``metadata['consumer']`` names what uses
    it in UNMScope today ("" = stored for LouisXIV parity only)."""

    galvo_cmd_x: float = _key("Galvo cmd X um/X Volt", 53.0, "X galvo sweep (AO1)")
    galvo_position_ratio: float = _key("Galvo position  cmd V/pos V", 0.5,
                                       "derived Galvo Pos um/Galvo Pos Volt only")
    galvo_cmd_y: float = _key("Galvo cmd Y um/Y Volt", math.nan, "")     # no Y galvo on this rig
    galvo_cmd_z: float = _key("Galvo cmd Z um/Z Volt", 53.0, "Z galvo steps (AO0)")
    zpiezo: float = _key("Zpiezo um/Zpiezo Volt", -10.0, "Z piezo steps (AO2)")
    xtile: float = _key("XTile um/V", 1.0, "")                            # Tiling galvo: no scan consumer yet
    sample_piezo: float = _key("SamplePiezo um/SamplePiezo Volt", 10.0, "")   # not an FPGA AO on this bitfile
    dither_galvo: float = _key("Dither Galvo um/V", 10.0, "Dither galvo triangle (AO4)")
    z_piezo2: float = _key("Z Piezo2 um/V", 0.0, "")                      # Enable Z Piezo 2 = FALSE on this rig

    # -- cluster metadata ------------------------------------------------------
    @classmethod
    def keys(cls) -> tuple[str, ...]:
        """The ini keys in panel order."""
        return tuple(f.metadata["key"] for f in fields(cls))

    @classmethod
    def field_names(cls) -> tuple[str, ...]:
        return tuple(f.name for f in fields(cls))

    @classmethod
    def key_of(cls, name: str) -> str:
        return next(f.metadata["key"] for f in fields(cls) if f.name == name)

    @classmethod
    def consumer_of(cls, name: str) -> str:
        return next(f.metadata["consumer"] for f in fields(cls) if f.name == name)

    def values(self) -> dict[str, float]:
        """``{ini key: value}`` in panel order."""
        return {f.metadata["key"]: getattr(self, f.name) for f in fields(self)}

    @property
    def galvo_pos_um_per_pos_v(self) -> float:
        """``Galvo Pos um/Galvo Pos Volt`` -- the derived indicator of the
        dialog ("Update Values" state) and the accessor VI's output: X um per
        commanded volt times commanded volts per position-readback volt."""
        return self.galvo_cmd_x * self.galvo_position_ratio

    # -- ini I/O ---------------------------------------------------------------
    @classmethod
    def from_configparser(cls, cp: configparser.ConfigParser,
                          fallback: "MicronsToVolt | None" = None) -> "MicronsToVolt":
        """Every key of the section, a missing or unparsable one taking the
        value from ``fallback`` (default: the cluster defaults)."""
        base = fallback if fallback is not None else cls()
        vals = {}
        for f in fields(cls):
            try:
                vals[f.name] = parse_ini_double(cp.get(INI_SECTION, f.metadata["key"]))
            except Exception:
                vals[f.name] = getattr(base, f.name)
        return cls(**vals)

    @classmethod
    def read(cls, path: Path | None = None) -> "MicronsToVolt":
        """Read the section from ``path`` (default: the UNMScope copy, created
        from LouisXIV's file on first use)."""
        p = Path(path) if path is not None else ensure_unmscope_ini()
        return cls.from_configparser(read_ini(p))

    def save(self, path: Path | None = None) -> Path:
        """Write the nine keys into ``[Microns to Volt calibrations]`` of the
        UNMScope copy (default path as in :meth:`read`), touching nothing
        else in the file. Existing key lines are rewritten in place keeping
        their ``Key = Value`` spacing; keys the section lacks are appended to
        it; a missing section is appended at the end of the file."""
        p = Path(path) if path is not None else ensure_unmscope_ini()
        p.parent.mkdir(parents=True, exist_ok=True)
        text = {k: format_ini_double(v) for k, v in self.values().items()}
        raw = p.read_bytes() if p.exists() else b""
        p.write_bytes(_rewrite_section(raw, INI_SECTION, text))
        return p

    # -- Calibration bridge ----------------------------------------------------
    def apply_to(self, cal: "Calibration") -> "Calibration":
        """A copy of ``cal`` whose axes carry these um/V values (limits and
        everything else unchanged) and whose ``um_per_volt`` is ``self``."""
        return replace(
            cal,
            x_galvo=replace(cal.x_galvo, um_per_volt=self.galvo_cmd_x),
            z_galvo=replace(cal.z_galvo, um_per_volt=self.galvo_cmd_z),
            z_piezo=replace(cal.z_piezo, um_per_volt=self.zpiezo),
            dither_galvo=replace(cal.dither_galvo, um_per_volt=self.dither_galvo),
            sample_piezo=replace(cal.sample_piezo, um_per_volt=self.sample_piezo),
            x_tile=replace(cal.x_tile, um_per_volt=self.xtile),
            um_per_volt=self,
        )


def load_calibration_from_unmscope_ini(path: Path | None = None) -> "Calibration":
    """``load_calibration`` on the UNMScope copy (created on first use), the
    file the calibration tab saves to."""
    from unmscope.config.calibration import load_calibration
    p = Path(path) if path is not None else ensure_unmscope_ini()
    return load_calibration(p)


# -- surgical section writer -------------------------------------------------------
_SECTION_RE = re.compile(rb"^\s*\[(?P<name>[^\]]*)\]\s*$")
_KEY_RE = re.compile(rb"^(?P<key>[^=\r\n]*?)(?P<sep>\s*=\s*)(?P<val>.*?)(?P<trail>\s*)$")


def _rewrite_section(raw: bytes, section: str, values: dict[str, str]) -> bytes:
    """Return ``raw`` with ``values`` applied inside ``[section]`` only.
    Line endings are whatever the file uses (CRLF for LouisXIV's file, kept
    per line); untouched lines are copied verbatim."""
    nl = b"\r\n" if b"\r\n" in raw or not raw else b"\n"
    lines = raw.split(b"\n")
    ends_with_newline = raw.endswith(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()                      # trailing newline -> no phantom empty line
    out: list[bytes] = []
    pending = dict(values)               # keys still to write
    in_section = False
    last_key_line = -1                   # index in out of the section's last key line
    section_seen = False

    def flush_pending() -> None:
        nonlocal last_key_line
        for k, v in pending.items():
            line = k.encode("utf-8") + b" = " + v.encode("utf-8") + nl
            out.insert(last_key_line + 1, line)
            last_key_line += 1
        pending.clear()

    for i, line in enumerate(lines):
        body = line[:-1] if line.endswith(b"\r") else line
        eol = b"\r\n" if line.endswith(b"\r") else b"\n"
        if i == len(lines) - 1 and not ends_with_newline:
            eol = b""                    # keep a file without a final newline as it was
        m = _SECTION_RE.match(body)
        if m:
            if in_section:
                flush_pending()
            in_section = m.group("name").decode("utf-8", "replace") == section
            if in_section:
                section_seen = True
                last_key_line = len(out)
            out.append(body + eol)
            continue
        if in_section:
            km = _KEY_RE.match(body)
            if km and km.group("key").strip():
                key = km.group("key").strip().decode("utf-8", "replace")
                if key in pending:
                    body = (km.group("key") + km.group("sep") + pending.pop(key).encode("utf-8")
                            + km.group("trail"))
                last_key_line = len(out)
        out.append(body + eol)
    if in_section:
        flush_pending()
    if not section_seen:
        if out and not out[-1].endswith(b"\n"):
            out[-1] += nl
        if out and out[-1].strip():
            out.append(nl)               # LouisXIV's file separates sections with a blank line
        out.append(b"[" + section.encode("utf-8") + b"]" + nl)
        last_key_line = len(out) - 1
        flush_pending()
    return b"".join(out)
