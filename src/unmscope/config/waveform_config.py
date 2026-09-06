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

from unmscope.config.paths import user_dir

def default_path() -> Path:
    return user_dir() / "waveform_config.json"


#: Enum items substantiated by the diagrams / panel (see module docstring).
WAVEFORM_TYPES = ("Linear",)                       # Generate SPIM Waveform: case "Linear"
Z_MOTION = ("Z galvo & piezo",)                    # panel default; other items unread
X_WAVE = ("Sawtooth", "Triangle")                  # [18] Cam settings sets Sawtooth; "X Triangle Pulses"
Z_WAVE = ("Step", "Sweep")                         # Read Waveform cluster: Z wave == Sweep -> Sweep Z?
AOTF_CYCLE = ("per Z", "per Stack", "None")        # HHMI - SPIM AOTF cycle enum

#: ``HHMI - Camera times to waveform times.vi``: the gap LouisXIV leaves
#: between the end of the AO waveform and the next camera trigger
#: ("otherwise X galvo waveform and/or the AOTF waveform will miss the
#: trigger.") -- docs/louisxiv_cycle_time_semantics.md, Q2.
CYCLE_GAP_S = 500e-9
#: Orca4.0 - Calculate SyncReadout/EdgeTrigger Exposure Time.vi: one sensor
#: line time (1H). ReadoutTime covers height/2 lines (two ports, centre-out).
ORCA_LINE_TIME_1H_S = 9.74436e-6
SYNCREADOUT_EXTRA_LINES = 18   # DCAM - Read cycle times -> Orca4.0 Calc SyncRdt Exp
EDGE_EXTRA_LINES = 10          # DCAM - Read cycle times -> Orca4.0 Calc EdgeTrig ExpTime
MAX_CAMERA_CYCLE_S = 10.0      # the SyncReadout VI's own upper bound on the camera cycle


def camera_cycle_s(exposure_s: float, vsize: int, sync_readout: bool,
                    line_time_s: float = ORCA_LINE_TIME_1H_S) -> float:
    """``DCAM - Read cycle times.vi`` (external trigger, Normal Scan): the
    camera's own frame period for the programmed exposure and ROI height.
    No margin -- LouisXIV has none here. The port's own hardware-measured
    safety margin is a separate, later floor: hardware/camera.py
    ``Camera.trigger_period_ms()`` (docs/known_issues.md; spikes/19) --
    that one stays; it protects against a different, real failure mode
    (retriggering during the camera's own readout) that this formula does
    not know about.
    """
    half = int(vsize) // 2
    if sync_readout:
        return min(MAX_CAMERA_CYCLE_S, max(exposure_s, (half + SYNCREADOUT_EXTRA_LINES) * line_time_s))
    return exposure_s + (half + EDGE_EXTRA_LINES) * line_time_s


def engine_times(exposure_s: float, camera_cycle: float, typed_cycle_s: float,
                  custom_cycle_time: bool) -> tuple[float, float]:
    """``HHMI - Camera times to waveform times.vi`` (the "Normal Scan","Split
    View" case, which always runs on this rig -- the selector is an enum
    CONSTANT, the Light Sheet branch is dead code), as called by SPIM MAIN's
    "Set Camera" state: returns (Cam exp (s), Cycle time (s)).

    Custom Cycle Time OFF: Cycle time tracks the camera exactly, floor =
    ``max(exposure, camera_cycle - CYCLE_GAP_S)``. ON: the typed value wins
    as long as it is at or above that floor; a typed value below the floor
    is raised to it, never lowered further. No 1.27 factor, no AO-point
    rounding, no maximum, in either mode
    (docs/louisxiv_cycle_time_semantics.md, Q2 -- established).
    """
    floor = max(exposure_s, camera_cycle - CYCLE_GAP_S)
    cycle = max(floor, typed_cycle_s) if custom_cycle_time else floor
    return exposure_s, cycle


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
    #: Control element of the cluster -- typeable, never an indicator, in
    #: LouisXIV -- but the engine overwrites it with the camera's own Exp(s)
    #: every time it runs "Set Camera" (camera connect, any Camera-tab
    #: change, ROI, Configure Stack, Acquire set-up): a typed value never
    #: reaches the camera and survives only until the next Set Camera. Used
    #: only for the PSF-mode AO clock (1 exp per = "Point") and the X Wvfrm
    #: cursor; it does NOT enter the AO rate for the "Z plane" mode this rig
    #: uses (docs/louisxiv_cycle_time_semantics.md, Q1 -- established).
    cam_exp_s: float = 2.0
    #: Control element (typeable in both Custom modes). Overwritten by
    #: ``engine_times()`` every "Set Camera": tracks the camera exactly when
    #: Custom Cycle Time is off, or is floored by it when on. The FPGA
    #: trigger period is max(this, the camera's own cycle) -- see
    #: ``camera_cycle_s()`` above and docs/louisxiv_cycle_time_semantics.md.
    #: On THIS rig the user runs Custom Cycle Time ON with 0.127 s typed (27
    #: ms of X-galvo flyback over a 100 ms exposure -- MEASURED by the user
    #: in LouisXIV, not derived from any formula here or there).
    cycle_time_s: float = 0.0
    z_motion: str = "Z galvo & piezo"
    n_doe_beams: int = 1
    x_wave: str = "Sawtooth"
    z_bidirectional: bool = False
    virtual_confocal: bool = False          # LED
    #: Tested in exactly one place in the whole LouisXIV source (Camera times
    #: to waveform times): keeps a typed Cycle time that is >= the
    #: camera-derived floor. Typing Cycle time does NOT tick this for you --
    #: LouisXIV has no such coupling (Q3 -- established) -- and it does not
    #: gate whether the field is typeable, only whether a typed value
    #: survives the next "Set Camera".
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

    def save(self, path: str | Path | None = None) -> Path:
        p = Path(path) if path is not None else default_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path | None = None) -> "WaveformConfig":
        p = Path(path) if path is not None else default_path()
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
