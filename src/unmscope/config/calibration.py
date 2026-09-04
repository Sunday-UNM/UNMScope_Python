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
class Calibration:
    x_galvo: Axis
    z_galvo: Axis
    z_piezo: Axis
    dither_galvo: Axis
    sample_piezo: Axis
    x_tile: Axis
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
    cal = "Microns to Volt calibrations"
    return Calibration(
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
