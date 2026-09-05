"""LouisXIV's "Waveform" cluster -- the Low-Level Waveform Config page of
SPIM MAIN.vi (Adv Setup > Low-Level Waveform Config; the cluster behind
``HHMI - SPIM Waveform Config functional global.vi``). Field names, order
and defaults are the panel's (rendered via LabVIEW COM, 2026-09-05).

What each field does in LouisXIV is documented in
docs/utilities_and_waveform_config.md, with the VIs it feeds. Only the fields
the Python waveform builder honours today are editable in the GUI; the rest
are shown greyed with their LouisXIV defaults so nothing looks wired that is
not (see the panel module).

Enum item lists: only the items that could be read from the source are
listed (the typedef strings are stored compressed). A one-item list means
"only the default is known".
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

DEFAULT_PATH = Path.home() / ".unmscope" / "waveform_config.json"

#: Enum items substantiated by the diagrams / panel (see module docstring).
WAVEFORM_TYPES = ("Linear",)                       # Generate SPIM Waveform: case "Linear"
Z_MOTION = ("Z galvo & piezo",)                    # panel default; other items unread
X_WAVE = ("Sawtooth", "Triangle")                  # [18] Cam settings sets Sawtooth; "X Triangle Pulses"
Z_WAVE = ("Step", "Sweep")                         # Read Waveform cluster: Z wave == Sweep -> Sweep Z?
AOTF_CYCLE = ("per Z", "per Stack", "None")        # HHMI - SPIM AOTF cycle enum
ONE_EXP_PER = ("Z plane",)
WAIT_FOR_Z_SETTLE = ("No settle",)                 # cases seen: "No settle.", "Skip imgs", "Z settle?"
DUAL_VIEW = ("No D.V.",)
AOTF_SWEEP_MODE = ("Sync",)


@dataclass
class AxisSettings:
    """One of the X / Xwvfrm / Z / Spiezo / Zpiezo / Dither sub-clusters:
    array index, first value, 'Size' (um per pixel / step) and 'Pixels'."""
    index: int = 0
    value: float = 0.0
    size: float = 0.0
    pixels: int = 1


@dataclass
class WaveformConfig:
    waveform: str = "Linear"
    x_single_direction: bool = True
    pixel_per_ms: float = 25.6              # indicator: AO rate (kHz) / Updates per Pixel
    updates_per_pix: int = 1
    fractional_flyback: float = 0.1
    fract_smoothing: float = 1.0
    aotf_delay_us: float = 0.0
    x_galvo_delay_us: float = 0.02
    z_galvo_delay_us: float = 0.0
    z_piezo_delay_us: float = 0.0
    sweep_period_um: float = 0.0
    duty_pct: float = 0.05
    n_integrations: int = 1
    cam_exp_s: float = 2.0                  # indicator: the camera exposure
    cycle_time_s: float = 0.0               # indicator unless Custom Cycle Time
    z_motion: str = "Z galvo & piezo"
    n_doe_beams: int = 1
    x_wave: str = "Sawtooth"
    z_bidirectional: bool = False
    virtual_confocal: bool = False          # LED
    custom_cycle_time: bool = False
    z_piezo_selector: int = 1
    linked: bool = True
    xz_correct: bool = True
    aotf_cycle: str = "per Stack"
    one_exp_per: str = "Z plane"
    wait_for_z_settle: str = "No settle"
    z_wave: str = "Step"
    dual_view: str = "No D.V."
    doe_period_um: float = 1.0
    x_triangle_pulses: float = 5.5
    aotf_sweep_mode: str = "Sync"
    dither_triangle_pulses: float = 5.5
    dither_fract_flyback: float = 0.1
    aotf_pulse_width_um: float = 0.0
    aotf_pulse_duty_pct: float = 5.0
    x: AxisSettings = field(default_factory=lambda: AxisSettings(0, 0.0, 0.2, 2))
    xwvfrm: AxisSettings = field(default_factory=lambda: AxisSettings(0, 0.0, 0.2, 1))
    z: AxisSettings = field(default_factory=lambda: AxisSettings(0, 0.0, 0.2, 4))
    spiezo: AxisSettings = field(default_factory=lambda: AxisSettings(0, 0.0, 0.0, 1))
    zpiezo: AxisSettings = field(default_factory=lambda: AxisSettings(0, 0.0, 0.0, 20))
    dither: AxisSettings = field(default_factory=lambda: AxisSettings(0, 0.0, 0.0, 1))

    # -- persistence -----------------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "WaveformConfig":
        cfg = cls()
        for f in fields(cls):
            if f.name not in d:
                continue
            v = d[f.name]
            if f.type == "AxisSettings" or isinstance(getattr(cfg, f.name), AxisSettings):
                if isinstance(v, dict):
                    setattr(cfg, f.name, AxisSettings(**{k: v[k] for k in ("index", "value", "size", "pixels") if k in v}))
            else:
                setattr(cfg, f.name, type(getattr(cfg, f.name))(v))
        return cfg

    def save(self, path: str | Path = DEFAULT_PATH) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path = DEFAULT_PATH) -> "WaveformConfig":
        p = Path(path)
        if not p.exists():
            return cls()
        try:
            return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return cls()

    # -- what the Python builder can honour today ---------------------------------
    def z_axes_moving(self) -> tuple[bool, bool]:
        """(Z galvo moves, Z piezo moves) for the 'Z motion' choice. Only the
        default item is known, so both move unless the string says otherwise."""
        m = self.z_motion.lower()
        return ("galvo" in m, "piezo" in m)
