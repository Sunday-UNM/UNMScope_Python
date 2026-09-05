"""The settings LouisXIV's ``HW Configuration GUI.vi`` (SPIM MAIN event case
[45] "HW Config") reads and writes in ``SPIMProject.ini``, as dataclasses.

Port of the four INI functional globals the panel drives (all rendered from
the LabVIEW source, see docs/hw_config.md):

- ``All Camera Settings INI FG.vi`` -> ``[Cam1.Camera Settings]`` ..
  ``[Cam5.Camera Settings]`` (``CameraSettings``)
- ``Imagine Optics Controller Settings INI FG.vi`` ->
  ``[Imagine Optics Settings]`` + ``[Imagine Optics Settings.Controller]``
  (``ImagineOpticsSettings`` / ``ImagineOpticsController``)
- ``Rotation Stage Settings INI FG.vi`` -> ``[Rotation Stage (PI U651) Settings]``
  (``RotationStageSettings``)
- ``Misc Settings INI FG.vi`` -> ``[Misc Settings]`` (``MiscSettings``)

Value encoding is LouisXIV's (``Write Configuration Section to INI File.vi``):
booleans ``TRUE`` / ``FALSE``, strings and paths double-quoted, enums as
their integer index (observed in the live file: Model = 3 = Orca4.0, Image
Transform = 0, Default Camera = 0, Default Analyis Method = 0, Z um/px source
= 1 = Z Piezo), the Binning ring as its U32 value (1 / 2 / 4), paths in
LabVIEW's ``/C/Users/...`` form.

Persistence (the user's decision): Python never writes LouisXIV's own file.
``user_ini_path()`` returns the UNMScope-owned copy
``~/.unmscope/SPIMProject.ini``, created byte-for-byte from LouisXIV's file
on first use; ``load_hw_config`` / ``save_hw_config`` read and modify only
that copy. The writer is line-preserving: it rewrites just the values of the
keys that changed (adding keys / sections the file lacks, as LouisXIV's
Read-then-Write cycle does), so every other byte -- unknown sections, key
order, ``Key = Value`` spacing, the four trailing spaces LouisXIV left after
every ``[Misc Settings]`` value, CRLF endings, the missing final newline --
survives untouched.

Enum item lists come from the typedef .ctl files (zlib-inflated strings) and
are complete for the enums used here. Nothing in this module talks to
hardware: the Imagine Optics, Rotation Stage and Misc sections have no Python
consumer yet (they are kept for LouisXIV's benefit and for the day those
subsystems are ported); of the camera keys, ``sync_readout`` and ``binning``
map onto what ``hardware.camera`` already understands.
"""
from __future__ import annotations

import configparser
import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from unmscope.config.calibration import DEFAULT_INI as LOUISXIV_INI

#: The UNMScope-owned copy of SPIMProject.ini (LouisXIV's stays read-only).
USER_INI = Path.home() / ".unmscope" / "SPIMProject.ini"

# -- enum item lists (typedef .ctl files, see module docstring) ----------------
CAMERA_IDS = ("Cam1", "Cam2", "Cam3", "Cam4", "Cam5")                       # Camera ID Enum.ctl
CAMERA_MODELS = ("Andor", "Andor sCMOS", "Orca2.8", "Orca4.0")              # HHMI - SPIM camera to use enum.ctl
IMAGE_TRANSFORMS = ("None", "Transpose", "V Flip", "H Flip", "Diag Flip",   # Transform type enum.ctl
                    "Rot 90", "Rot 180", "Rot 270")
BINNINGS = (1, 2, 4)                                                        # DCAM - Binning.ctl (1x1, 2x2, 4x4)
BINNING_LABELS = tuple(f"{b}x{b}" for b in BINNINGS)
Z_UM_PX_SOURCES = ("Z Galvo", "Z Piezo")                                    # Z um per px source enum.ctl
ANALYSIS_METHODS = ("RMS Contrast", "Peak Intensity", "ModulationDepth (Matlab)")  # Adaptive Optics ... Analysis Method Enum.ctl


# Section names, spelled exactly as LouisXIV writes them (do not "fix" them).
CAMERA_SECTION = "{cam}.Camera Settings"
IMAGINE_OPTICS_SECTION = "Imagine Optics Settings"
IMAGINE_OPTICS_CONTROLLER_SECTION = "Imagine Optics Settings.Controller"
ROTATION_STAGE_SECTION = "Rotation Stage (PI U651) Settings"
MISC_SECTION = "Misc Settings"


# -- the clusters ----------------------------------------------------------------
# Each dataclass lists its ini keys as KEYS = ((attr, "ini key", kind), ...) in
# the cluster's element order (= the key order LouisXIV writes). kind is one
# of bool / int / float / str / path (path = string in LabVIEW path form).

@dataclass
class CameraSettings:
    """``Camera INI Settings Cluster.ctl`` -> ``[CamN.Camera Settings]``.
    Defaults are the panel's (the rendered VI, not the ini)."""
    enabled: bool = False
    simulate: bool = False
    model: int = 3                  # index into CAMERA_MODELS (3 = Orca4.0)
    serial_number: str = ""
    save_index: int = 2
    sync_readout: bool = False
    image_transform: int = 0        # index into IMAGE_TRANSFORMS
    remote: bool = False
    remote_ip: str = ""
    dcam_port: int = 3365
    cmd_port: int = 2222
    binning: int = 1                # ring value, one of BINNINGS

    KEYS = (
        ("enabled", "Enabled", "bool"), ("simulate", "Simulate", "bool"),
        ("model", "Model", "int"), ("serial_number", "Serial Number", "str"),
        ("save_index", "Save Index", "int"), ("sync_readout", "Sync Readout", "bool"),
        ("image_transform", "Image Transform", "int"), ("remote", "Remote", "bool"),
        ("remote_ip", "RemoteIP", "str"), ("dcam_port", "DCAM Port", "int"),
        ("cmd_port", "Cmd Port", "int"), ("binning", "Binning", "int"),
    )

    @property
    def model_name(self) -> str:
        return _enum_name(CAMERA_MODELS, self.model)

    @property
    def image_transform_name(self) -> str:
        return _enum_name(IMAGE_TRANSFORMS, self.image_transform)


@dataclass
class ImagineOpticsController:
    """The ``Controller`` sub-cluster -> ``[Imagine Optics Settings.Controller]``."""
    wavefront_corrector_setup_file: str = ""
    haso_config_file: str = ""
    correction_interaction_matrix_file: str = ""
    default_wavefront_positions_file: str = ""
    flat_wavefront_positions_file: str = ""
    sleep_after_command_apply_ms: int = 20

    KEYS = (
        ("wavefront_corrector_setup_file", "Wavefront Corrector Setup File", "path"),
        ("haso_config_file", "HasoConfigFile", "path"),
        ("correction_interaction_matrix_file", "Correction Interaction Matrix File", "path"),
        ("default_wavefront_positions_file", "Default Wavefront Positions File", "path"),
        ("flat_wavefront_positions_file", "Flat Wavefront Positions File", "path"),
        ("sleep_after_command_apply_ms", "Sleep after command apply (ms)", "int"),
    )


@dataclass
class ImagineOpticsSettings:
    """``Imagine Optics All Settings Cluster.ctl`` -> ``[Imagine Optics Settings]``
    (+ the Controller section). 'Analyis' is LouisXIV's spelling of the key."""
    enable: bool = False
    simulate: bool = False
    n_pts_default: int = 5
    default_camera: int = 0         # index into CAMERA_IDS
    controller: ImagineOpticsController = field(default_factory=ImagineOpticsController)
    default_analysis_method: int = 0    # index into ANALYSIS_METHODS
    matlab_script_directory: str = ""

    KEYS = (
        ("enable", "Enable", "bool"), ("simulate", "Simulate", "bool"),
        ("n_pts_default", "# Pts (Default)", "int"), ("default_camera", "Default Camera", "int"),
        ("default_analysis_method", "Default Analyis Method", "int"),
        ("matlab_script_directory", "Matlab Script Directory", "path"),
    )


@dataclass
class RotationStageSettings:
    """``Rotation Stage Settings Cluster.ctl`` -> ``[Rotation Stage (PI U651) Settings]``."""
    enable: bool = False
    simulate: bool = False
    serial_number: str = ""
    speed_deg_s: float = 72.0
    settling_time_ms: float = 100.0

    KEYS = (
        ("enable", "Enable?", "bool"), ("simulate", "Simulate", "bool"),
        ("serial_number", "Serial Number", "str"), ("speed_deg_s", "Speed (deg/s)", "float"),
        ("settling_time_ms", "Settling Time (ms)", "float"),
    )


@dataclass
class MiscSettings:
    """``Misc Settings Cluster.ctl`` -> ``[Misc Settings]``. The typedef also
    holds a 'Show Length Scale' string that is neither on the panel nor in
    the ini (probably a removed element) -- not carried."""
    z_um_px_source: int = 0         # index into Z_UM_PX_SOURCES
    disable_xg_zg_zp_calibrations: bool = False
    enable_x_galvo_correction_lut: bool = False
    enable_z_piezo_2: bool = False
    use_alternating_galvo_channels: bool = False
    galvo1_alternating_first_v: float = 5.0
    galvo1_alternating_second_v: float = 5.0
    galvo2_alternating_first_v: float = 0.0
    galvo2_alternating_second_v: float = 0.0

    KEYS = (
        ("z_um_px_source", "Z um/px source", "int"),
        ("disable_xg_zg_zp_calibrations", "Disable Xg-Zg-Zp Calibrations", "bool"),
        ("enable_x_galvo_correction_lut", "Enable X Galvo Correction LUT", "bool"),
        ("enable_z_piezo_2", "Enable Z Piezo 2 (Dither Chnl.)", "bool"),
        ("use_alternating_galvo_channels", "Use Z Galvo and Dither as Alternating Galvo Channels", "bool"),
        ("galvo1_alternating_first_v", "Galvo 1 Alternating First (V)", "float"),
        ("galvo1_alternating_second_v", "Galvo 1 Alternating Second (V)", "float"),
        ("galvo2_alternating_first_v", "Galvo 2 Alternating First (V)", "float"),
        ("galvo2_alternating_second_v", "Galvo 2 Alternating Second (V)", "float"),
    )


@dataclass
class HwConfig:
    cameras: list[CameraSettings] = field(default_factory=lambda: [CameraSettings() for _ in CAMERA_IDS])
    imagine_optics: ImagineOpticsSettings = field(default_factory=ImagineOpticsSettings)
    rotation_stage: RotationStageSettings = field(default_factory=RotationStageSettings)
    misc: MiscSettings = field(default_factory=MiscSettings)

    def sections(self) -> list[tuple[str, object]]:
        """(ini section, cluster) pairs in LouisXIV's write order."""
        out: list[tuple[str, object]] = [(CAMERA_SECTION.format(cam=cam), c) for cam, c in zip(CAMERA_IDS, self.cameras)]
        out += [(IMAGINE_OPTICS_SECTION, self.imagine_optics),
                (IMAGINE_OPTICS_CONTROLLER_SECTION, self.imagine_optics.controller),
                (ROTATION_STAGE_SECTION, self.rotation_stage), (MISC_SECTION, self.misc)]
        return out


def _enum_name(items: tuple[str, ...], index: int) -> str:
    return items[index] if 0 <= index < len(items) else f"<{index}>"


# -- value encoding ----------------------------------------------------------------
def parse_value(raw: str, kind: str):
    """ini text -> Python, tolerant of the spellings other LouisXIV VIs use
    (True/False, quoted or bare strings, '5' or '5.000000')."""
    v = raw.strip()
    if kind == "bool":
        return v.upper() in ("TRUE", "T", "YES", "1")
    if kind in ("str", "path"):
        if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
            v = v[1:-1]
        return v
    if kind == "int":
        try:
            return int(v)
        except ValueError:
            return int(float(v))
    if kind == "float":
        return float(v)
    raise ValueError(kind)


def format_value(value, kind: str) -> str:
    """Python -> ini text the way ``Write Configuration Section to INI File``
    does it (see the module docstring). Floats are written in the shortest
    form ('5', '0.25'), which is what the live [Misc Settings] shows; other
    sections' '%.6f' values are never rewritten by this module."""
    if kind == "bool":
        return "TRUE" if value else "FALSE"
    if kind in ("str", "path"):
        return f'"{value}"'
    if kind == "int":
        return str(int(value))
    if kind == "float":
        f = float(value)
        return str(int(f)) if f.is_integer() else repr(f)
    raise ValueError(kind)


def labview_path_to_windows(p: str) -> str:
    """'/C/Users/x/y.dat' -> 'C:\\Users\\x\\y.dat' (LabVIEW's flattened path
    form as found in the ini). Empty and already-Windows strings pass through."""
    if p.startswith("/") and len(p) >= 2 and p[1].isalpha() and (len(p) == 2 or p[2] == "/"):
        return p[1] + ":" + p[2:].replace("/", "\\")
    return p


def windows_path_to_labview(p: str) -> str:
    """'C:\\Users\\x\\y.dat' -> '/C/Users/x/y.dat'."""
    p = str(p)
    if len(p) >= 2 and p[1] == ":" and p[0].isalpha():
        return "/" + p[0] + p[2:].replace("\\", "/")
    return p


# -- reading ------------------------------------------------------------------------
def _parser() -> configparser.ConfigParser:
    # comment_prefixes=() : LouisXIV keys start with '#' ("# Pts (Default)")
    cp = configparser.ConfigParser(interpolation=None, strict=False, comment_prefixes=(),
                                   inline_comment_prefixes=None)
    cp.optionxform = str  # type: ignore[assignment]
    return cp


def _fill(cluster, cp: configparser.ConfigParser, section: str) -> None:
    if not cp.has_section(section):
        return
    for attr, key, kind in cluster.KEYS:
        if cp.has_option(section, key):
            try:
                setattr(cluster, attr, parse_value(cp.get(section, key), kind))
            except ValueError:
                pass    # unparsable value: keep the panel default, like LouisXIV's Read


def load_hw_config(path: str | Path | None = None) -> HwConfig:
    """Read the owned copy (created from LouisXIV's file on first use)."""
    p = user_ini_path(path)
    cp = _parser()
    cp.read_string(_read(p))
    cfg = HwConfig()
    for section, cluster in cfg.sections():
        _fill(cluster, cp, section)
    return cfg


# -- writing (line-preserving) -----------------------------------------------------
class IniText:
    """A byte-faithful ini editor: only the value of a key that is set is
    touched (its 'Key = ' prefix, trailing whitespace and line ending stay);
    missing keys are appended to their section, missing sections to the
    file. Everything else round-trips untouched."""

    def __init__(self, text: str):
        self.nl = "\r\n" if "\r\n" in text else "\n"
        self.lines = text.splitlines(keepends=True)

    def text(self) -> str:
        return "".join(self.lines)

    def _section_span(self, section: str) -> tuple[int, int] | None:
        start = None
        for i, line in enumerate(self.lines):
            s = line.strip()
            if s.startswith("[") and s.endswith("]"):
                if start is not None:
                    return start, i
                if s[1:-1].strip() == section:
                    start = i
        return None if start is None else (start, len(self.lines))

    @staticmethod
    def _split(line: str) -> tuple[str, str, str, str] | None:
        """'Key = Value   \\r\\n' -> (prefix 'Key = ', value, trailing ws, eol)."""
        body = line.rstrip("\r\n")
        eol = line[len(body):]
        if "=" not in body or body.lstrip().startswith("["):
            return None     # '#' is NOT a comment here: "# Pts (Default) = 5" is a key
        k, _, rest = body.partition("=")
        stripped = rest.lstrip()
        prefix = k + "=" + rest[:len(rest) - len(stripped)]
        value = stripped.rstrip()
        trailing = stripped[len(value):]
        return prefix, value, trailing, eol

    def get(self, section: str, key: str) -> str | None:
        span = self._section_span(section)
        if span is None:
            return None
        for i in range(span[0] + 1, span[1]):
            parts = self._split(self.lines[i])
            if parts and parts[0].split("=")[0].strip() == key:
                return parts[1]
        return None

    def set(self, section: str, key: str, value: str) -> None:
        span = self._section_span(section)
        if span is None:
            self._ensure_final_newline()
            if self.lines and self.lines[-1].strip():
                self.lines.append(self.nl)
            self.lines.append(f"[{section}]{self.nl}")
            self.lines.append(f"{key} = {value}{self.nl}")
            return
        start, end = span
        for i in range(start + 1, end):
            parts = self._split(self.lines[i])
            if parts and parts[0].split("=")[0].strip() == key:
                prefix, _old, trailing, eol = parts
                self.lines[i] = f"{prefix}{value}{trailing}{eol}"
                return
        # key missing: insert after the section's last non-blank line
        insert = end
        while insert - 1 > start and not self.lines[insert - 1].strip():
            insert -= 1
        if insert == len(self.lines):
            self._ensure_final_newline()
        self.lines.insert(insert, f"{key} = {value}{self.nl}")

    def _ensure_final_newline(self) -> None:
        if self.lines and not self.lines[-1].endswith(("\n", "\r")):
            self.lines[-1] += self.nl


def _all_keys(cfg: HwConfig, on_disk: HwConfig) -> Iterable[tuple[str, str, str, object, object]]:
    """(section, key, kind, value in cfg, value in the file) for every owned key."""
    for (section, new), (_, old) in zip(cfg.sections(), on_disk.sections()):
        for attr, key, kind in new.KEYS:
            yield section, key, kind, getattr(new, attr), getattr(old, attr)


def save_hw_config(cfg: HwConfig, path: str | Path | None = None) -> Path:
    """Write ``cfg`` into the owned copy, changing only the keys that differ
    from the file (and adding the ones it lacks). Returns the path."""
    p = user_ini_path(path)
    raw = _read(p)
    ini = IniText(raw)
    on_disk = load_hw_config(p)
    for section, key, kind, new, old in _all_keys(cfg, on_disk):
        if new != old or ini.get(section, key) is None:
            ini.set(section, key, format_value(new, kind))
    out = ini.text()
    if out != raw:
        with open(p, "w", encoding="latin-1", newline="") as fh:   # latin-1: bytes in == bytes out; CRLF as-is
            fh.write(out)
    return p


def _read(p: Path) -> str:
    with open(p, encoding="latin-1", newline="") as fh:   # latin-1 round-trips every byte; no newline translation
        return fh.read()


# -- the owned copy ------------------------------------------------------------------
def user_ini_path(path: str | Path | None = None, source: str | Path = LOUISXIV_INI) -> Path:
    """The ini this module edits. ``path`` defaults to ``USER_INI``; when it
    does not exist yet it is created as a byte-for-byte copy of ``source``
    (LouisXIV's SPIMProject.ini, which is never written)."""
    if path is not None:
        p = Path(path)
    elif USER_INI != Path.home() / ".unmscope" / "SPIMProject.ini":
        p = USER_INI                      # monkeypatched by tests
    else:
        from unmscope.config.paths import user_ini
        p = user_ini()
    from unmscope.config.paths import ensure_user_copy
    return ensure_user_copy(source, p, if_missing="raise")


def copy_config(cfg: HwConfig) -> HwConfig:
    """Deep copy (the dialog keeps an editable copy and a from-disk one)."""
    return copy.deepcopy(cfg)
