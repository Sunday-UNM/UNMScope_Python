"""AO waveform content for the FPGA's ``Wvfrm2`` DMA stream.

PACKING -- decoded on the deployed bitfile with the FPGA Scope
(spikes/28_wvfrm2_packing_on_scope.py, 2026-09-03): the AO engine consumes
TWO I64 words per point (one point every ``AO ticks between points``,
``AO # of points per trigger`` points after each trigger, holding the last
point until the next trigger; the first point lands ~60 us after the DIO4
edge). Splitting each word into four I16 slots, high slot first, gives
the 8-channel point

    word 0: [prefix0, prefix1, X Galvo, Z Galvo]      (bits 63..48, 47..32, 31..16, 15..0)
    word 1: [Z Piezo, Dither Galvo, Tiling, Filter]   (same order)

i.e. the deployed ``AO DMA`` cluster order preceded by LabVIEW's two
"prefix columns" (Setup AO DMA buffer.vi: "# of prefix columns"). Every
channel is in DAC counts: +-10 V over 16 bits, 3276.7 counts/V, and the
FPGA range-checks each value against ``AO Limit Max/Min (counts)``.

This module only BUILDS word lists. Nothing here touches hardware; feed
the result to ``FpgaTriggerController.start_free_run(ao_words=...)``,
which streams it as a repeating pattern.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from unmscope.hardware.fpga_trigger import TICKS_PER_S, pack_ao_word

COUNTS_PER_VOLT = 32767 / 10.0
CHANNELS = ("prefix0", "prefix1", "X Galvo", "Z Galvo", "Z Piezo", "Dither Galvo", "Tiling", "Filter")
WORDS_PER_POINT = 2


def volts_to_counts(v) -> np.ndarray:
    return np.clip(np.rint(np.asarray(v, dtype=np.float64) * COUNTS_PER_VOLT), -32767, 32767).astype(np.int64)


def counts_to_volts(c) -> np.ndarray:
    return np.asarray(c, dtype=np.float64) / COUNTS_PER_VOLT


def pack_points(x_galvo, z_galvo, z_piezo=0, dither=0, tiling=0, filt=0, prefix0=0, prefix1=0) -> list[int]:
    """Pack per-point channel arrays (DAC counts, int16 range) into the
    Wvfrm2 word list: 2 words per point. Scalars broadcast."""
    cols = np.broadcast_arrays(*[np.asarray(a, dtype=np.int64) for a in
                                 (prefix0, prefix1, x_galvo, z_galvo, z_piezo, dither, tiling, filt)])
    n = cols[0].shape[0] if cols[0].ndim else 1
    cols = [np.broadcast_to(c, (n,)) for c in cols]
    p0, p1, x, z, zp, di, ti, fi = cols
    words = []
    for i in range(n):
        words.append(pack_ao_word(z[i], x[i], p1[i], p0[i]))      # slot0=Z Galvo .. slot3=prefix0
        words.append(pack_ao_word(fi[i], ti[i], di[i], zp[i]))    # slot0=Filter .. slot3=Z Piezo
    return words


def unpack_words(words) -> dict[str, np.ndarray]:
    """Inverse of pack_points (for tests and for showing what was sent)."""
    w = np.asarray(words, dtype=np.int64).reshape(-1, WORDS_PER_POINT)

    def slot(word, k):
        v = (word >> (16 * k)) & 0xFFFF
        return np.where(v >= 0x8000, v - 0x10000, v)

    return {
        "prefix0": slot(w[:, 0], 3), "prefix1": slot(w[:, 0], 2),
        "X Galvo": slot(w[:, 0], 1), "Z Galvo": slot(w[:, 0], 0),
        "Z Piezo": slot(w[:, 1], 3), "Dither Galvo": slot(w[:, 1], 2),
        "Tiling": slot(w[:, 1], 1), "Filter": slot(w[:, 1], 0),
    }


def triangle_points(n_points: int, pulses: float, range_v: float, offset_v: float = 0.0) -> np.ndarray:
    """LabVIEW's ``Generate Triangular Waveform.vi``: ``pulses`` triangle
    periods spread over ``n_points``, built as ``round(2*pulses)`` linear
    segments of equal length that alternate direction. Fractional pulses
    are allowed (5.5 -> 11 segments, ending at the far extreme), which is
    why the Dither box's "# Sweeps" accepts 5.5.

    ``range_v`` is peak-to-peak (the GUI's "Range"), centred on offset_v.
    """
    n = max(1, int(n_points))
    segments = max(1, int(round(2 * float(pulses))))
    per = n / segments
    idx = np.arange(n)
    seg = np.minimum((idx / per).astype(np.int64), segments - 1)
    frac = idx / per - seg
    up = (seg % 2) == 0
    return np.where(up, -0.5 + frac, 0.5 - frac) * float(range_v) + float(offset_v)


def smooth_turnarounds(v: np.ndarray, pulses: float, flyback_fraction: float) -> np.ndarray:
    """Round off each triangle turnaround over ``flyback_fraction`` of a
    half-period (LabVIEW's "Fract. Flyback" / fractional smoothing), so the
    galvo is not asked for an instantaneous reversal. A moving average of
    that width is the cheap equivalent of the cubic overshoot LabVIEW fits.
    """
    if flyback_fraction <= 0 or len(v) < 3:
        return v
    segments = max(1, int(round(2 * float(pulses))))
    half = len(v) / segments
    w = int(max(1, round(flyback_fraction * half)))
    if w <= 1:
        return v
    pad = np.concatenate((np.full(w, v[0]), v, np.full(w, v[-1])))
    kernel = np.ones(w) / w
    return np.convolve(pad, kernel, mode="same")[w: w + len(v)]


@dataclass
class ScanWaveform:
    """One trigger period's worth of AO points for each slice of a scan,
    plus the FPGA settings that go with it."""
    words: list[int]
    points_per_trigger: int
    ticks_between_points: int
    n_slices: int
    channels: dict[str, np.ndarray] = field(default_factory=dict)   # (n_slices*points_per_trigger,) counts

    @property
    def point_period_s(self) -> float:
        return self.ticks_between_points / TICKS_PER_S


def dither_block(n_pts: int, dither_range_v: float, dither_pulses: float,
                 dither_flyback_fraction: float) -> np.ndarray:
    """The dither galvo's triangle across one block (smeared at the
    turnarounds), or a flat block at zero when the dither is disabled --
    shared by ``build_scan_waveform`` and ``louisxiv_waveform.build_louisxiv_waveform``."""
    if dither_range_v and dither_pulses:
        return smooth_turnarounds(
            triangle_points(n_pts, dither_pulses, dither_range_v), dither_pulses, dither_flyback_fraction)
    return np.zeros(n_pts)


def assemble_scan(x_block_v: np.ndarray, slow_v: list[tuple[np.ndarray, np.ndarray]],
                  d_block_v: np.ndarray, ticks_between_points: int) -> ScanWaveform:
    """Repeat the fast-axis block once per slice (or use each slice's own
    pre-shifted copy, e.g. the S-curve slow-axis tail), pair each with that
    slice's (Z galvo, Z piezo) volts and the dither block, and pack into
    words. ``x_block_v`` may be a single (n_pts,) array (repeated for every
    slice) or already one row per slice with the same shape as each entry
    of ``slow_v`` -- both builders that use this pass the former today."""
    n_pts = len(d_block_v)
    n_slices = len(slow_v)
    x_rows = [x_block_v] * n_slices if x_block_v.ndim == 1 else list(x_block_v)
    x_c = volts_to_counts(np.concatenate(x_rows))
    zg_c = volts_to_counts(np.concatenate([zg for zg, _ in slow_v]))
    zp_c = volts_to_counts(np.concatenate([zp for _, zp in slow_v]))
    d_c = volts_to_counts(np.concatenate([d_block_v] * n_slices))
    words = pack_points(x_c, zg_c, zp_c, dither=d_c)
    return ScanWaveform(words=words, points_per_trigger=n_pts, ticks_between_points=ticks_between_points,
                        n_slices=n_slices,
                        channels={"X Galvo": x_c, "Z Galvo": zg_c, "Z Piezo": zp_c, "Dither Galvo": d_c})


def build_scan_waveform(exposure_s: float, x_range_v: float, n_slices: int = 1,
                        z_galvo_start_v: float = 0.0, z_galvo_step_v: float = 0.0,
                        z_piezo_start_v: float = 0.0, z_piezo_step_v: float = 0.0,
                        ticks_between_points: int = 4000, x_offset_v: float = 0.0,
                        flyback_fraction: float = 0.1, period_s: float | None = None,
                        period_margin_s: float = 0.002, dither_range_v: float = 0.0,
                        dither_pulses: float = 0.0, dither_flyback_fraction: float = 0.1) -> ScanWaveform:
    """The LouisXIV-style light-sheet waveform, one block per trigger:

    - X Galvo: a linear sweep of +-x_range_v/2 around x_offset_v over the
      exposure (the sheet is swept across the field once per frame),
      followed by a short flyback to the start value.
    - Z Galvo / Z Piezo: constant within a slice, stepping by *_step_v per
      slice (a Z stack is n_slices consecutive trigger blocks).
    - everything else 0.

    points_per_trigger = ceil(exposure / point period) + flyback points.
    If period_s is given the block is shortened to fit the trigger period
    minus period_margin_s (SYNCREADOUT makes period == exposure, so the
    sweep then covers most of the period and the flyback the rest).
    """
    dt = ticks_between_points / TICKS_PER_S
    n_sweep = max(2, int(np.ceil(exposure_s / dt)))
    n_fly = max(1, int(np.ceil(flyback_fraction * n_sweep)))
    if period_s is not None:
        n_max = int(np.floor((period_s - period_margin_s) / dt))
        if n_sweep + n_fly > n_max:
            n_fly = max(1, int(np.ceil(flyback_fraction * n_max)))
            n_sweep = max(2, n_max - n_fly)
    n_pts = n_sweep + n_fly
    x_sweep = np.linspace(x_offset_v - x_range_v / 2, x_offset_v + x_range_v / 2, n_sweep)
    x_fly = np.linspace(x_offset_v + x_range_v / 2, x_offset_v - x_range_v / 2, n_fly + 2)[1:-1]
    x_block_v = np.concatenate((x_sweep, x_fly))
    # Dither galvo: a triangle across the whole block (the dither runs for
    # the exposure to smear out stripe artefacts), held at its start value
    # when disabled.
    d_block_v = dither_block(n_pts, dither_range_v, dither_pulses, dither_flyback_fraction)
    slow_v = [(np.full(n_pts, z_galvo_start_v + k * z_galvo_step_v),
              np.full(n_pts, z_piezo_start_v + k * z_piezo_step_v)) for k in range(max(1, n_slices))]
    return assemble_scan(x_block_v, slow_v, d_block_v, ticks_between_points)


def block_fits_period(wf: ScanWaveform, period_s: float, margin_s: float = 0.0005) -> bool:
    """A trigger block must finish (plus margin) before the next edge."""
    return wf.points_per_trigger * wf.point_period_s + margin_s <= period_s
