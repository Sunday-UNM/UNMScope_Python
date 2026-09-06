"""UNMScope's own copy of LouisXIV's ``SPIMProject.ini`` and the stage
settings sections it carries.

Persistence rule (the user's, 2026-09-05): Python NEVER writes LouisXIV's
file (``config.calibration.DEFAULT_INI`` is read-only input). Anything that
writes settings writes ``~/.unmscope/SPIMProject.ini``: on first use the
LouisXIV file is copied there byte for byte, and from then on only the copy
is read and modified. Writes preserve every unknown section and key, the
CRLF line endings and the ``Key = Value`` spacing, so the copy stays a valid
LouisXIV ini (LabVIEW's config VIs rewrite whole sections; ours only touch
the keys they own).

Sections ported here (values as found 2026-09-05):

    [SIMP-285 3D Stage]                 SIMP-285 INI FG.vi / SIMP-285 Settings Cluster.ctl
    Enable? = False                     boolean
    COM Port = "COM8"                   VISA resource name, quoted string
    Velocity (um/s) = 2500              I32
    Settling Time (ms) = 300            DBL
    Simulate = FALSE                    boolean
    XYZ Assignment = 5                  enum index into XYZ_ASSIGNMENTS (5 = ZYX)

    [Rotation Stage (PI U651) Settings] Rotation Stage Settings INI FG.vi
    Enable? = FALSE, Simulate = FALSE, Serial Number = "", Speed (deg/s) = 72,
    Settling Time (ms) = 100

Boolean spelling: the live file mixes ``False`` (written by LabVIEW's
cluster-to-ini writer, e.g. ``Enable?``) and ``FALSE`` (``Simulate``, and the
whole rotation section). Both are read; we write ``True``/``False`` like the
most recently written key. Not verified which spelling LabVIEW's reader
prefers -- it accepts both (NI_LVConfig reads booleans case-insensitively).
"""
from __future__ import annotations

import configparser
import re
from dataclasses import dataclass
from pathlib import Path

from unmscope.config.calibration import DEFAULT_INI
from unmscope.config.ini_text import write_keys as _write_keys

#: The UNMScope-owned copy. Tests monkeypatch ``user_ini_path``.
USER_INI = Path.home() / ".unmscope" / "SPIMProject.ini"   # default; user_ini_path() honours UNMSCOPE_HOME

SIMP285_SECTION = "SIMP-285 3D Stage"
ROTATION_SECTION = "Rotation Stage (PI U651) Settings"

#: ``XYZ axis assignment enum.ctl`` item order (verified from the enum's
#: string table and the ini value 5 -> ZYX); see hardware.stage.
XYZ_ASSIGNMENTS = ("XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX")


def user_ini_path() -> Path:
    """UNMScope's copy of SPIMProject.ini (unmscope.config.paths; USER_INI is
    the module-level default kept for tests that monkeypatch it)."""
    from unmscope.config import paths
    default = Path.home() / ".unmscope" / "SPIMProject.ini"
    return paths.user_ini() if USER_INI == default else USER_INI


def ensure_user_ini(source: Path = DEFAULT_INI, dest: Path | None = None) -> Path:
    """Return the path of the UNMScope copy, creating it from ``source``
    (byte for byte) on first use. A missing source leaves an empty copy so
    the app still starts; the settings then fall back to their defaults."""
    from unmscope.config.paths import ensure_user_copy
    dest = Path(dest) if dest is not None else user_ini_path()
    return ensure_user_copy(source, dest, if_missing="empty")


def _parser() -> configparser.ConfigParser:
    p = configparser.ConfigParser(interpolation=None, strict=False)
    p.optionxform = str
    return p


def read_ini(path: Path) -> configparser.ConfigParser:
    p = _parser()
    if Path(path).exists():
        p.read_string(Path(path).read_bytes().decode("utf-8", errors="replace"))
    return p


_KEY_RE = re.compile(r"^(?P<key>[^=;#\[\]][^=]*?)(?P<sep>\s*=\s*)(?P<val>.*?)(?P<eol>\r?\n)?$")


def write_keys(path: Path, section: str, values: dict[str, str]) -> None:
    """Replace (or append) ``values`` in ``[section]`` of the ini at ``path``,
    touching nothing else.

    Kept as this module's spelling of the operation; the implementation is
    :func:`unmscope.config.ini_text.write_keys`, shared with ``hw_config``
    and ``um_per_volt``. This module used to have its own copy that decoded
    the file as UTF-8 with ``errors="replace"``, which would have replaced
    any non-UTF-8 byte LouisXIV wrote with U+FFFD.
    """
    _write_keys(path, section, values)

def _bool(s: str, default: bool) -> bool:
    s = s.strip().strip('"').lower()
    if s in ("true", "t", "1", "yes"):
        return True
    if s in ("false", "f", "0", "no"):
        return False
    return default


def _num(s: str, default: float) -> float:
    try:
        return float(s.strip().strip('"'))
    except ValueError:
        return default


def _fmt_num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else repr(float(v))


def _str(s: str) -> str:
    s = s.strip()
    return s[1:-1] if len(s) >= 2 and s[0] == s[-1] == '"' else s


@dataclass
class Simp285Settings:
    """[SIMP-285 3D Stage] -- LouisXIV's SIMP-285 Settings Cluster."""
    enabled: bool = False
    com_port: str = "COM8"
    velocity_um_s: float = 2500.0
    settling_ms: float = 300.0
    simulate: bool = False
    xyz_assignment: int = 5          # index into XYZ_ASSIGNMENTS (ZYX)

    @property
    def assignment_name(self) -> str:
        return XYZ_ASSIGNMENTS[self.xyz_assignment % len(XYZ_ASSIGNMENTS)]

    @classmethod
    def load(cls, path: Path | None = None) -> "Simp285Settings":
        p = read_ini(path if path is not None else user_ini_path())
        d = cls()
        if p.has_section(SIMP285_SECTION):
            s = p[SIMP285_SECTION]
            d.enabled = _bool(s.get("Enable?", ""), d.enabled)
            d.com_port = _str(s.get("COM Port", d.com_port))
            d.velocity_um_s = _num(s.get("Velocity (um/s)", ""), d.velocity_um_s)
            d.settling_ms = _num(s.get("Settling Time (ms)", ""), d.settling_ms)
            d.simulate = _bool(s.get("Simulate", ""), d.simulate)
            d.xyz_assignment = int(_num(s.get("XYZ Assignment", ""), d.xyz_assignment))
        return d

    def save(self, path: Path | None = None) -> Path:
        """SIMP-285 INI FG 'Write INI (input)': rewrite the section's keys."""
        path = Path(path) if path is not None else user_ini_path()
        write_keys(path, SIMP285_SECTION, {
            "Enable?": "True" if self.enabled else "False",
            "COM Port": f'"{self.com_port}"',
            "Velocity (um/s)": _fmt_num(self.velocity_um_s),
            "Settling Time (ms)": _fmt_num(self.settling_ms),
            "Simulate": "True" if self.simulate else "False",
            "XYZ Assignment": str(int(self.xyz_assignment)),
        })
        return path


@dataclass
class RotationStageSettings:
    """[Rotation Stage (PI U651) Settings] -- Rotation Stage Settings Cluster."""
    enabled: bool = False
    simulate: bool = False
    serial_number: str = ""
    speed_deg_s: float = 72.0
    settling_ms: float = 100.0

    @classmethod
    def load(cls, path: Path | None = None) -> "RotationStageSettings":
        p = read_ini(path if path is not None else user_ini_path())
        d = cls()
        if p.has_section(ROTATION_SECTION):
            s = p[ROTATION_SECTION]
            d.enabled = _bool(s.get("Enable?", ""), d.enabled)
            d.simulate = _bool(s.get("Simulate", ""), d.simulate)
            d.serial_number = _str(s.get("Serial Number", d.serial_number))
            d.speed_deg_s = _num(s.get("Speed (deg/s)", ""), d.speed_deg_s)
            d.settling_ms = _num(s.get("Settling Time (ms)", ""), d.settling_ms)
        return d

    def save(self, path: Path | None = None) -> Path:
        path = Path(path) if path is not None else user_ini_path()
        write_keys(path, ROTATION_SECTION, {
            "Enable?": "True" if self.enabled else "False",
            "Simulate": "True" if self.simulate else "False",
            "Serial Number": f'"{self.serial_number}"',
            "Speed (deg/s)": _fmt_num(self.speed_deg_s),
            "Settling Time (ms)": _fmt_num(self.settling_ms),
        })
        return path
