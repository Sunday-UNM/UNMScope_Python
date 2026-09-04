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
"""
from __future__ import annotations

import configparser
import math
from dataclasses import dataclass
from pathlib import Path

DEFAULT_INI = Path(r"H:\UNM_Lightsheet\UNMScope_Source\SPIM\SPIM Support files\SPIMProject.ini")


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
    def um_min(self) -> float:
        return self.v_to_um(self.v_min)

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


@dataclass(frozen=True)
class Calibration:
    x_galvo: Axis
    z_galvo: Axis
    z_piezo: Axis
    dither_galvo: Axis
    sample_piezo: Axis
    x_tile: Axis
    aotf: Aotf
    source: str


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
    cal = "Microns to Volt calibrations"
    return Calibration(
        aotf=aotf,
        x_galvo=Axis("X Galvo", _get(cp, cal, "Galvo cmd X um/X Volt", 2000.0),
                     _get(cp, "X Galvo Limits (V)", "Min (V)", -2.5), _get(cp, "X Galvo Limits (V)", "Max (V)", 2.5)),
        z_galvo=Axis("Z Galvo", _get(cp, cal, "Galvo cmd Z um/Z Volt", 7.0),
                     _get(cp, "Z Galvo Limits (V)", "Min (V)", -2.5), _get(cp, "Z Galvo Limits (V)", "Max (V)", 2.5)),
        z_piezo=Axis("Z Piezo", _get(cp, cal, "Zpiezo um/Zpiezo Volt", 8.0),
                     _get(cp, "Z Piezo Limits (V)", "Min (V)", -2.5), _get(cp, "Z Piezo Limits (V)", "Max (V)", 10.0)),
        dither_galvo=Axis("Dither Galvo", _get(cp, cal, "Dither Galvo um/V", 10.0),
                          _get(cp, "D Galvo Limits (V)", "Min (V)", -5.5), _get(cp, "D Galvo Limits (V)", "Max (V)", 5.5)),
        sample_piezo=Axis("Sample Piezo", _get(cp, cal, "SamplePiezo um/SamplePiezo Volt", 100.0), -10.0, 10.0),
        x_tile=Axis("X Tile", _get(cp, cal, "XTile um/V", 1.0), -10.0, 10.0),
        source=source,
    )
