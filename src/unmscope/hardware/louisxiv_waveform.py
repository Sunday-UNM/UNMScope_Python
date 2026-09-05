"""LouisXIV's "Calculate Waveforms" for the Linear (ramp) waveform, ported
VI-for-VI from `SPIM/SPIM LV8.6 VIs/DAQ/Waveform/` and `Waveform/` (block
diagrams incl. hidden case frames in H:\\UNM_Lightsheet\\VI_Diagrams). Nothing
here is derived; every formula names the VI it comes from.

Fast axis, one line (``HHMI - Generate Fast axis ramp for DOE scan.vi`` with
``HHMI - Fast Axis Cubic Ramp Coeffs.vi`` and ``HHMI - Calc Cubic portion of
fast axis Ramp.vi``), positions relative to the axis offset, ``xrange`` in
position units, ``tau`` = Fractional Smoothing, ``flyback`` = Fractional
Flyback, ``update_rate`` = Updates / Pixel, ``xpix`` = Fast Axis.Pixels::

    a3 = -tau * xrange / 3;  a2 = tau * xrange;  tau_elem = update_rate * ceil(tau * xpix)
    overshoot = 2 * tau * xrange / 3            (= a2 + a3, the cubic at x = 1)
    cubic(x)  = a3 * x^3 + a2 * x^2, x = k / tau_elem for k = 1 .. tau_elem
    accel     = -xrange/2 - overshoot + cubic(x)                    (tau_elem pts, ends at -xrange/2)
    linear    = (i+1)/N * xrange - xrange/2, N = xpix*update_rate   (ends at +xrange/2)
    decel     = xrange/2 + overshoot - cubic(x reversed)            (tau_elem pts, ends at rest)
    -- flyback ("Make Fast axis flyback", X Bidirectional = False):
    tau2_elem = update_rate * ceil(flyback * tau * xpix)
    ret_elem  = update_rate * ceil(flyback * xpix)
    return    = cubic from +xrange/2+overshoot down to +xrange/2 (tau2_elem pts, x = i/(N-1)),
                linear xrange/2 - (i+1)/ret_elem * xrange (ret_elem pts),
                cubic from -xrange/2 down to -xrange/2-overshoot (tau2_elem pts)
    -- X Bidirectional = True: return = the forward line reversed, tau2 = ret = 0
    points used for return = 2 * tau2_elem + ret_elem;  Tau (AO counts during cubic) = tau_elem

``HHMI - Center fast waveform at fast axis offset.vi``: its correction is in a
disabled diagram frame -> no-op.

Rate (``HHMI - SPIM number of points and points per second that Ramp
needs.vi``, ``HHMI - SPIM compute Min Rate needed for Flyback.vi``,
``HHMI - Compute AO rate from Cycle Time.vi``, ``HHMI - Check AO rate for
ramp wave.vi``, ``HHMI - Max AO Rate.vi`` = 1000 kHz)::

    min points per trigger = line points (IQ 2 if bidirectional)
    ON points = min points - points used for return          (AOTF on, camera exposing)
    rate needed for exposure = ON points / exposure
    Option A = min points / cycle time                       (no extra points)
    Option B = ON points / (cycle time - Z settle time)      (always faster than A)
    X flyback time (B) = return points / Option B;  if it is >= the Z settle time,
    or Wait for Zsettle = "Skip imgs": use Option A, no extra AO counts
    AO rate = max(rate needed for exposure, rate to complete the Z flyback)
    check: AO_Rate_kHz = pixrate * update_rate; if > 1000: update_rate = 2*floor(floor(1000/pixrate)/2)
    Pix/ms = AO rate (kHz) / Updates per Pixel
    ``HHMI - Calculate DMA Settings.vi``: ticks between points = round(1/rate in 25 ns ticks),
    real AO clock = 1 / (ticks * 25 ns)

Slow axes (``HHMI - SPIM Generate S curve flyback for slow axes and tile
array.vi``, ``HHMI - Compute S curve number of points.vi`` = return points
IQ 3): between Z steps the slow axis moves along an S-curve of n points --
accel half ``((i+1)/Na)^3 * half + start`` (Na = (n+1) IQ 2), decel half
``(1 - (reversed i/Nd)^3) * half + mid`` (Nd = n+1-Na), start point removed.

Not ported (LouisXIV features our FPGA path does not use yet): AOTF arrays
per line (our AOTF is a DC level for the run), Dual View, DOE, Z lookup
linking, XZ coupling, galvo inertia shift (all delays are 0 / 0.02 us ->
"No shift"), Sine waveform.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from unmscope.hardware.fpga_trigger import TICKS_PER_S
from unmscope.hardware.waveform import ScanWaveform, assemble_scan, dither_block

MAX_AO_RATE_KHZ = 1000.0        # HHMI - Max AO Rate.vi


# -- fast axis ---------------------------------------------------------------------
def cubic_ramp_coeffs(xrange: float, tau: float, update_rate: int, xpix: int) -> tuple[float, float, int]:
    """``HHMI - Fast Axis Cubic Ramp Coeffs``: (a2, a3, tau_elem)."""
    a3 = -tau * xrange / 3.0
    a2 = tau * xrange
    tau_elem = int(update_rate) * int(math.ceil(tau * xpix))
    return a2, a3, tau_elem


def cubic_portion(a2: float, a3: float, tau_elem: int) -> np.ndarray:
    """``HHMI - Calc Cubic portion of fast axis Ramp``: the cubic over
    tau_elem + 1 samples of x in [0, 1], first element removed."""
    if tau_elem <= 0:
        return np.zeros(0)
    x = np.linspace(0.0, 1.0, tau_elem + 1)[1:]
    return a3 * x ** 3 + a2 * x ** 2


@dataclass
class FastAxisLine:
    positions: np.ndarray          # one line, relative to the axis offset (offset already added)
    tau_elem: int                  # Tau (AO counts during cubic)
    tau2_elem: int
    ret_elem: int
    linear_points: int
    overshoot: float
    bidirectional: bool = False

    @property
    def total_points(self) -> int:
        return int(self.positions.size)

    @property
    def return_points(self) -> int:
        """'points used for return'."""
        return 0 if self.bidirectional else 2 * self.tau2_elem + self.ret_elem

    @property
    def on_points(self) -> int:
        """'Total ON points' = min points - return points (AOTF on, camera exposing)."""
        return self.min_points - self.return_points

    @property
    def min_points(self) -> int:
        """'Min # of points needed for ramp per trigger (bidirectional corrected)'."""
        return self.total_points // 2 if self.bidirectional else self.total_points


def fast_axis_line(xrange: float, offset: float, xpix: int, update_rate: int, tau: float,
                   flyback: float, bidirectional: bool = False) -> FastAxisLine:
    """``HHMI - Generate Fast axis ramp for DOE scan`` (DOE? = False)."""
    xpix, update_rate = max(1, int(xpix)), max(1, int(update_rate))
    a2, a3, tau_elem = cubic_ramp_coeffs(xrange, tau, update_rate, xpix)
    overshoot = 2.0 * tau * xrange / 3.0
    # the cubic over tau_elem + 1 samples of x in [0, 1]: the accel drops the
    # first sample (x = 0), the decel the last (x = 1) and runs it backwards,
    # so each cubic meets the linear part once and the decel ends at rest
    x_full = np.linspace(0.0, 1.0, tau_elem + 1) if tau_elem > 0 else np.zeros(1)
    cubic_full = a3 * x_full ** 3 + a2 * x_full ** 2
    n_lin = xpix * update_rate
    accel = -xrange / 2.0 - overshoot + cubic_full[1:]
    linear = (np.arange(n_lin) + 1) / n_lin * xrange - xrange / 2.0
    decel = xrange / 2.0 + overshoot - cubic_full[:-1][::-1]
    forward = np.concatenate((accel, linear, decel))
    if bidirectional:
        ret = forward[::-1]
        tau2 = retn = 0
    else:
        tau2 = update_rate * int(math.ceil(flyback * tau * xpix))
        retn = update_rate * int(math.ceil(flyback * xpix))
        if tau2 > 1:
            xr = np.arange(tau2) / (tau2 - 1)
        else:
            xr = np.ones(tau2)
        cubic_r = a3 * xr ** 3 + a2 * xr ** 2
        ret_c1 = xrange / 2.0 + overshoot - cubic_r
        ret_lin = xrange / 2.0 - (np.arange(retn) + 1) / max(1, retn) * xrange
        ret_c2 = -xrange / 2.0 - cubic_r
        ret = np.concatenate((ret_c1, ret_lin, ret_c2))
    return FastAxisLine(positions=np.concatenate((forward, ret)) + offset, tau_elem=tau_elem,
                        tau2_elem=tau2, ret_elem=retn, linear_points=n_lin, overshoot=overshoot,
                        bidirectional=bidirectional)


# -- slow axes -----------------------------------------------------------------------
def s_curve_points(return_points: int) -> int:
    """``HHMI - Compute S curve number of points``: return points IQ 3."""
    return int(return_points) // 3


def s_curve(start: float, finish: float, n: int) -> np.ndarray:
    """``HHMI - SPIM Generate S curve flyback for slow axes and tile array``
    (n > 0): n points from just after ``start`` to exactly ``finish``."""
    n = int(n)
    if n <= 0:
        return np.zeros(0)
    half = (finish - start) / 2.0
    mid = start + half
    na = (n + 1) // 2
    nd = n + 1 - na
    accel = ((np.arange(na) + 1) / na) ** 3 * half + start
    decel = (1.0 - (np.arange(nd)[::-1] / nd) ** 3) * half + mid
    return np.concatenate((accel, decel))[1:]


# -- rate ----------------------------------------------------------------------------
@dataclass
class RateInfo:
    ao_rate_hz: float                 # after the max-rate check and the DMA tick rounding
    ticks_between_points: int
    update_rate: int
    pixel_per_ms: float               # AO rate (kHz) / Updates per Pixel
    rate_for_exposure_hz: float
    rate_for_flyback_hz: float
    option: str                       # "A" or "B"
    extra_ao_counts: int = 0
    extra_images: int = 0


def min_rate_for_exposure(on_points: int, exposure_s: float) -> float:
    """``number of points and points per second that Ramp needs``."""
    return on_points / exposure_s if exposure_s > 0 else float("inf")


def min_rate_for_flyback(min_points: int, return_points: int, cycle_s: float,
                         z_settle_ms: float = 0.0, skip_images: bool = False) -> tuple[float, str, int, int]:
    """``compute Min Rate needed for Flyback``: (rate pts/s, option, extra AO
    counts, extra images)."""
    if cycle_s <= 0:
        return float("inf"), "A", 0, 0
    option_a = min_points / cycle_s
    z_fly_s = z_settle_ms / 1000.0
    denom = cycle_s - z_fly_s
    option_b = (min_points - return_points) / denom if denom > 0 else float("inf")
    x_fly_s = return_points / option_b if option_b > 0 and math.isfinite(option_b) else 0.0
    if x_fly_s >= z_fly_s or skip_images:
        if skip_images and x_fly_s < z_fly_s:
            # "Don't use extra counts. Use extra images."
            extra_imgs = int(math.ceil((z_fly_s - x_fly_s) / cycle_s)) if cycle_s > 0 else 0
            return option_a, "A", 0, extra_imgs
        return option_a, "A", 0, 0
    # "Need faster rate to have Z flyback complete before cycle is done."
    extra = int(math.ceil((z_fly_s - x_fly_s) * option_b))
    return option_b, "B", max(0, extra), 0


def check_ao_rate(pixrate_khz: float, update_rate: int) -> tuple[float, int]:
    """``HHMI - Check AO rate for ramp wave``: (AO_Rate_kHz, Updates/Pixel)."""
    ur = int(update_rate)
    rate = pixrate_khz * ur
    if rate > MAX_AO_RATE_KHZ:
        ur = int(math.floor(MAX_AO_RATE_KHZ / pixrate_khz))
        ur = 2 * (ur // 2)                        # "update_rate must be even"
        if ur < 1:
            raise ValueError(f"Pixel rate {pixrate_khz:g} kHz exceeds the {MAX_AO_RATE_KHZ:g} kHz AO limit "
                             "even at 1 update per pixel (LouisXIV would compute a zero update rate)")
        rate = pixrate_khz * ur
    return rate, ur


def dma_ticks(ao_rate_hz: float) -> tuple[int, float]:
    """``HHMI - Calculate DMA Settings``: 'Round timing to nearest tick' ->
    (AO ticks between points, real AO clock rate Hz)."""
    ticks = max(1, int(round(TICKS_PER_S / ao_rate_hz)))
    return ticks, TICKS_PER_S / ticks


def ao_rate_for_line(line: FastAxisLine, exposure_s: float, cycle_s: float, update_rate: int,
                     z_settle_ms: float = 0.0, skip_images: bool = False) -> RateInfo:
    """``Compute AO rate from Cycle Time``: the larger of the two rules,
    checked against the max AO rate, rounded to the FPGA tick."""
    r_exp = min_rate_for_exposure(line.on_points, exposure_s)
    r_fly, option, extra_counts, extra_imgs = min_rate_for_flyback(
        line.min_points, line.return_points, cycle_s, z_settle_ms, skip_images)
    rate_hz = max(r_exp, r_fly)
    if not math.isfinite(rate_hz) or rate_hz <= 0:
        raise ValueError("exposure and cycle time must be > 0")
    pixrate_khz = rate_hz / 1000.0 / max(1, update_rate)
    rate_khz, ur = check_ao_rate(pixrate_khz, update_rate)
    ticks, real_hz = dma_ticks(rate_khz * 1000.0)
    return RateInfo(ao_rate_hz=real_hz, ticks_between_points=ticks, update_rate=ur,
                    pixel_per_ms=real_hz / 1000.0 / ur, rate_for_exposure_hz=r_exp,
                    rate_for_flyback_hz=r_fly, option=option, extra_ao_counts=extra_counts,
                    extra_images=extra_imgs)


# -- the whole waveform ---------------------------------------------------------------
@dataclass
class LouisXivWaveform:
    scan: ScanWaveform
    line: FastAxisLine
    rate: RateInfo
    s_curve_points: int = 0
    notes: list[str] = field(default_factory=list)


def build_louisxiv_waveform(*, exposure_s: float, cycle_s: float, x_range_v: float, x_offset_v: float,
                            x_pixels: int, update_rate: int = 1, fractional_smoothing: float = 1.0,
                            fractional_flyback: float = 0.1, x_bidirectional: bool = False,
                            n_slices: int = 1, z_galvo_start_v: float = 0.0, z_galvo_step_v: float = 0.0,
                            z_piezo_start_v: float = 0.0, z_piezo_step_v: float = 0.0,
                            dither_range_v: float = 0.0, dither_pulses: float = 0.0,
                            dither_flyback_fraction: float = 0.1, z_settle_ms: float = 0.0,
                            skip_images: bool = False, cycle_margin_s: float = 0.0) -> LouisXivWaveform:
    """One fast-axis line per trigger at LouisXIV's computed AO rate; slow
    axes constant per slice with an S-curve (return points IQ 3) into the
    next slice; the dither galvo's triangle across the line.

    ``cycle_margin_s`` (default 0 = LouisXIV's rule, the block fills the
    cycle) lets the rate rules see ``cycle_s - cycle_margin_s``; the earlier
    fixed-rate builder kept 2 ms. Measured on the PCIe-7852R 2026-09-05
    (simulate-on-FPGA, 50 triggers at 100 ms, 198-point lines): margin 0 and
    2 ms both gave every AO point, 0 internal cycle mismatches and the same
    host-observed 100.6-100.8 ms interval (the ~0.6 ms is status-poll bias),
    so the literal rule is used."""
    line = fast_axis_line(x_range_v, x_offset_v, x_pixels, update_rate, fractional_smoothing,
                          fractional_flyback, x_bidirectional)
    rate = ao_rate_for_line(line, exposure_s, max(1e-6, cycle_s - cycle_margin_s), update_rate,
                            z_settle_ms, skip_images)
    n_pts = line.total_points
    n_slices = max(1, int(n_slices))
    n_s = min(s_curve_points(line.return_points), n_pts)
    d_block = dither_block(n_pts, dither_range_v, dither_pulses, dither_flyback_fraction)
    slow_v = []
    for k in range(n_slices):
        zg = np.full(n_pts, z_galvo_start_v + k * z_galvo_step_v)
        zp = np.full(n_pts, z_piezo_start_v + k * z_piezo_step_v)
        if k < n_slices - 1 and n_s > 0:
            zg[n_pts - n_s:] = s_curve(zg[0], z_galvo_start_v + (k + 1) * z_galvo_step_v, n_s)
            zp[n_pts - n_s:] = s_curve(zp[0], z_piezo_start_v + (k + 1) * z_piezo_step_v, n_s)
        slow_v.append((zg, zp))
    scan = assemble_scan(line.positions, slow_v, d_block, rate.ticks_between_points)
    notes = []
    if rate.update_rate != update_rate:
        notes.append(f"Updates/Pixel reduced {update_rate} -> {rate.update_rate} by the {MAX_AO_RATE_KHZ:g} kHz AO limit")
    if rate.extra_ao_counts or rate.extra_images:
        notes.append(f"Z settle needs {rate.extra_ao_counts} extra AO counts / {rate.extra_images} extra images "
                     "(not added: our FPGA run has no Z settle)")
    return LouisXivWaveform(scan=scan, line=line, rate=rate, s_curve_points=n_s, notes=notes)
