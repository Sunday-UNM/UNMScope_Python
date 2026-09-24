"""Microns-to-volts calibrations and voltage limits, read from LouisXIV's
``SPIMProject.ini`` so the Python GUI drives the same hardware with the
same numbers.

Sections used (values as found 2026-09-03):

    [Microns to Volt calibrations]
    Galvo cmd X um/X Volt = 2000        [X Galvo Limits (V)]  -2.5 .. 2.5
    Galvo cmd Z um/Z Volt = 7           [Z Galvo Limits (V)]  -2.5 .. 2.5
    Zpiezo um/Zpiezo Volt = 8           [Z Piezo Limits (V)]  -2.5 .. 10.0
    Dither Galvo um/V = 10              [D Galvo Limits (V)]  -5.5 .. 5.5
    SamplePiezo um/SamplePiezo Volt = 100
    XTile um/V = 1

The [Microns to Volt calibrations] keys come from ``unmscope.config.um_per_volt``
(LouisXIV's cluster, 1:1, with its Save); the full cluster rides along as
``Calibration.um_per_volt`` and the axes' ``um_per_volt`` values are taken
from that same object, so the two can never disagree. The fallback numbers
when a key is missing are the 2026-09-03 ini values above (a pre-existing
choice of this module; LouisXIV would use its cluster defaults instead, see
docs/um_per_volt_calibration.md).
"""
from __future__ import annotations

import configparser
import math
from dataclasses import dataclass, field
from pathlib import Path

from unmscope.config.um_per_volt import LOUISXIV_INI, MicronsToVolt

DEFAULT_INI = LOUISXIV_INI


@dataclass(frozen=True)
class Axis:
    name: str
    um_per_volt: float
    v_min: float
    v_max: float

    def um_to_v(self, um: float) -> float:
        if not self.um_per_volt or math.isnan(self.um_per_volt):
            raise ValueError(f"{self.name}: no um/V calibration")
        return um / self.um_per_volt

    def v_to_um(self, v: float) -> float:
        return v * self.um_per_volt

    def clamp_v(self, v: float) -> float:
        return min(self.v_max, max(self.v_min, v))

    @property
    def um_max(self) -> float:
        return self.v_to_um(self.v_max)


@dataclass(frozen=True)
class Aotf:
    """AOTF excitation config from the ini. ``labels`` are the wavelength
    strings in row order (row 0 = the first label); the analog level of an
    enabled row is its Power % mapped linearly onto [v_min, v_max] V, then
    to DAC counts by the FPGA (docs/aotf.md).

    The deployed bitfile drives each ``AOTF ch (V)`` channel as a DC level
    while a run is armed; there is no per-frame gating from Python yet, so
    "the AOTF is on for the whole acquisition" is the current time model.
    Row->channel routing is the identity here (row 0 -> AOTF ch 0); the
    physical laser on a channel is set by the patch panel, not software.
    """
    labels: tuple[str, ...]
    v_min: float
    v_max: float

    @property
    def n_channels(self) -> int:
        return len(self.labels)

    def power_pct_to_v(self, pct: float) -> float:
        """Power % (0..100) -> AOTF volts, linear across [v_min, v_max]."""
        frac = min(1.0, max(0.0, float(pct) / 100.0))
        return self.v_min + frac * (self.v_max - self.v_min)

    def channel_for_row(self, row_index: int) -> int:
        """Excitation row (0-based) -> AOTF channel index. Identity; see the
        class docstring on physical routing."""
        return int(row_index)


#: [Detection optics] ini keys for Magnification / Camera Pixel (um). Not a
#: LouisXIV section (no VI reads it; nothing in docs/vi_notes.md) -- a
#: UNMScope-only addition so the objective and camera pixel pitch driving
#: FOV/pixel-size math are editable without hand-editing the ini (user,
#: 2026-09-23: "is there any file where I can always change the
#: magnification... or should we add a section to the camera tab"). See
#: gui/camera_tab.py's "Detection Optics" box, which writes both keys with
#: unmscope.config.ini_text.write_keys the same way um_per_volt.py does.
DETECTION_SECTION = "Detection optics"
DETECTION_MAGNIFICATION_KEY = "Magnification"
DETECTION_CAMERA_PIXEL_KEY = "Camera Pixel (um)"


@dataclass(frozen=True)
class Detection:
    """[Detection optics]: what a camera pixel sees at the sample. The XY
    pixel size is the sensor pixel over the magnification -- 6.5 um / 30x =
    0.2167 um, which is the Camera tab's 444 um FOV over 2048 px."""
    magnification: float
    physical_pixel_z_um: float
    camera_pixel_um: float = 6.5          # Orca Flash 4.0 sensor pitch

    @property
    def xy_pixel_um(self) -> float:
        return self.pixel_size_um(binning=1)

    def pixel_size_um(self, binning: int = 1) -> float:
        """Camera Image Pixel sizes.vi: ``CCD pixel size * Binning / Mag``."""
        return self.camera_pixel_um * max(1, int(binning)) / self.magnification

    def save(self, path: Path | None = None) -> Path:
        """Write Magnification / Camera Pixel (um) into the UNMScope ini
        copy's [Detection optics] section, touching nothing else -- same
        surgical writer and same file um_per_volt.MicronsToVolt.save() uses,
        so a Save here survives a restart and never touches LouisXIV's own
        ini (that file is read-only input, project-wide)."""
        from unmscope.config.ini_text import write_keys
        from unmscope.config.um_per_volt import ensure_unmscope_ini
        p = Path(path) if path is not None else ensure_unmscope_ini()
        write_keys(p, DETECTION_SECTION, {
            DETECTION_MAGNIFICATION_KEY: f"{self.magnification:.6f}",
            DETECTION_CAMERA_PIXEL_KEY: f"{self.camera_pixel_um:.6f}",
        })
        return p


@dataclass(frozen=True)
class Calibration:
    detection: Detection
    x_galvo: Axis
    z_galvo: Axis
    z_piezo: Axis
    dither_galvo: Axis
    sample_piezo: Axis
    x_tile: Axis
    aotf: Aotf
    source: str
    #: The whole [Microns to Volt calibrations] cluster (all nine keys); the
    #: axes above carry the same um/V numbers.
    um_per_volt: MicronsToVolt = field(default_factory=MicronsToVolt)


#: Fallbacks for a missing [Microns to Volt calibrations] key: the 2026-09-03
#: ini numbers for the six keys this module always read, LouisXIV's cluster
#: defaults for the other three.
_UM_PER_VOLT_FALLBACK = MicronsToVolt(galvo_cmd_x=2000.0, galvo_cmd_z=7.0, zpiezo=8.0,
                                      dither_galvo=10.0, sample_piezo=100.0, xtile=1.0)


def _get(cp: configparser.ConfigParser, section: str, key: str, default: float) -> float:
    try:
        return float(cp.get(section, key).strip())
    except Exception:
        return default


def load_calibration(ini_path: str | Path | None = None) -> Calibration:
    """Parse the ini; every value falls back to the 2026-09-03 numbers if a
    key is missing, so the GUI never runs uncalibrated by accident."""
    path = Path(ini_path) if ini_path is not None else DEFAULT_INI
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str  # keys are case-sensitive and contain spaces/slashes
    source = "defaults"
    if path.exists():
        cp.read(path, encoding="utf-8", )
        source = str(path)
    labels_raw = ""
    try:
        labels_raw = cp.get("AOTF Settings", "Labels").strip().strip('"')
    except Exception:
        labels_raw = "637,561,488,405"
    labels = tuple(s.strip() for s in labels_raw.split(",") if s.strip())
    aotf = Aotf(labels=labels or ("637", "561", "488", "405"),
                v_min=_get(cp, "AOTF Limits (V)", "Min (V)", 0.0),
                v_max=_get(cp, "AOTF Limits (V)", "Max (V)", 5.0))
    mtv = MicronsToVolt.from_configparser(cp, fallback=_UM_PER_VOLT_FALLBACK)
    return Calibration(
        aotf=aotf,
        detection=Detection(magnification=_get(cp, DETECTION_SECTION, DETECTION_MAGNIFICATION_KEY, 30.0),
                            camera_pixel_um=_get(cp, DETECTION_SECTION, DETECTION_CAMERA_PIXEL_KEY, 6.5),
                            physical_pixel_z_um=_get(cp, "Detection optics", "PhysicalPixelSizeZ (um)", 0.2)),
        x_galvo=Axis("X Galvo", mtv.galvo_cmd_x,
                     _get(cp, "X Galvo Limits (V)", "Min (V)", -2.5), _get(cp, "X Galvo Limits (V)", "Max (V)", 2.5)),
        z_galvo=Axis("Z Galvo", mtv.galvo_cmd_z,
                     _get(cp, "Z Galvo Limits (V)", "Min (V)", -2.5), _get(cp, "Z Galvo Limits (V)", "Max (V)", 2.5)),
        z_piezo=Axis("Z Piezo", mtv.zpiezo,
                     _get(cp, "Z Piezo Limits (V)", "Min (V)", -2.5), _get(cp, "Z Piezo Limits (V)", "Max (V)", 10.0)),
        dither_galvo=Axis("Dither Galvo", mtv.dither_galvo,
                          _get(cp, "D Galvo Limits (V)", "Min (V)", -5.5), _get(cp, "D Galvo Limits (V)", "Max (V)", 5.5)),
        # 0 .. 10 V, not +-10: `HHMI - Z Piezo AOTF voltage limits.vi` wires
        # literal constants 0 and 10 to "Minimum/Maximum S Piezo Voltage".
        # Unlike the axes above there is no ini section for it, so this is
        # LouisXIV's fixed limit, not a per-rig setting.
        sample_piezo=Axis("Sample Piezo", mtv.sample_piezo, 0.0, 10.0),
        # ASSUMED. LouisXIV has no X-tile limits constant and SPIMProject.ini
        # has no section for it; +-10 V is the DAQ's own range, not a value
        # read off the source.
        x_tile=Axis("X Tile", mtv.xtile, -10.0, 10.0),
        source=source,
        um_per_volt=mtv,
    )
