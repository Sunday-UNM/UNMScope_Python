"""Make the AOTF output a MODULATING square wave, not a DC level.

FPGA ONLY -- no camera. The galvo/piezo channels are CLAMPED (mirrors and
the piezo stay put); only the AOTF channels are allowed to move, which is
the separation established in docs/aotf.md. The user powered the laser box
down for this, so the AOTF control voltages drive nothing.

WHAT WAS ALREADY RULED OUT
--------------------------
`spikes/32`: in the default modes ('AOTF Mode' = Set AOTF, 'AOTF ch Mode'
= Set AOTF ch) 'AOTF ch (V)' comes out as a DC level that ignores the
per-point 'AOTF on?' gate in the Wvfrm2 stream; the clock registers alone
('AOTF Delay (Ticks)', 'AOTF pulse width (ticks)',
'AOTF # of points per trigger', BOTH the plain and PB copies) give 0 V.

Run 1 of this spike (2026-09-08) added the 'AOTF Sweep settings' cluster
that spike 32 never wrote -- phases / ticks-per-phase / "on" / "off"
ticks -- and tried 'AOTF sweep mode' Step / Sweep / Sync, with and without
a "Stop/Config Wvfrm" pass, with and without the clock registers. Result:
**every configuration with 'AOTF Mode' = 0 output 0 V on the target
channel.** Only the baseline (both modes = 2, "Set AOTF") put anything on
the pin at all, and that is the DC level we already have.

So the open question is no longer "what extra registers does the engine
need" but "which of the two loops has to be in waveform mode". There are
two independent mode registers driving two independent FPGA loops:

    'AOTF Mode'     -> HHMI - AOTF Clock Control Loop.vi   (the timing)
    'AOTF ch Mode'  -> HHMI - AOTF Channel Output Loop.vi  (the level)

Every experiment so far moved them TOGETHER (both 0, or both 2). The
interesting cell is the mixed one: let the CLOCK run the waveform while
the CHANNEL stays in "Set AOTF ch" so the level register still drives the
output -- i.e. clock gates, channel supplies. This spike sweeps the full
3x3 matrix with the clock and sweep registers populated.

    python -u spikes/38_aotf_modulation_engine.py [--level 6308] [--ch 2]

--level 6308 counts = 1.925 V = the rig's own 488 setting (38.5 % of 5 V),
so a working configuration should be directly comparable to what was
measured flat on the pin.
"""
from __future__ import annotations

import argparse
import sys
import time

import numpy as np

from unmscope.hardware.fpga_scope import AI_CHANNEL_NAMES, FpgaScope
from unmscope.hardware.fpga_trigger import AOTF_LEVEL_KEYS, FpgaTriggerController
from unmscope.hardware.waveform import pack_points

CLAMP = 328                 # +-100 mV on the galvo channels (content is 0 anyway)
POINTS = 200                # 20 ms block at 100 us / point
TICKS = 4000
PERIOD, EXPOSURE = 0.100, 0.080
TICKS_PER_MS = 40_000

#: Deliberately slow so a few-kHz poll loop cannot alias it away.
SWEEP_ON_TICKS = 10 * TICKS_PER_MS      # 10 ms high
SWEEP_OFF_TICKS = 10 * TICKS_PER_MS     # 10 ms low

MODE_NAMES = {0: "Start/Run", 1: "Stop/Config", 2: "Set"}

SWEEP_ZERO = {
    "AOTF sweep phases": 0,
    "AOTF sweep ticks/phase": 0,
    "AOTF sweep ticks": 0,
    'AOTF sweep "on" (Ticks)': 0,
    'AOTF sweep "off" (Ticks)': 0,
    "AOTF sweep freq (1/tick)": 0.0,
    "AOTF sweep periods/phase": 0.0,
}
CLOCK_ZERO = {
    "AOTF # of points per trigger": 0,
    "AOTF # of points per trigger PB": 0,
    "AOTF Delay (Ticks)": 0,
    "AOTF pulse width (ticks)": 0,
    "AOTF pulse width (ticks) PB": 0,
}


def sweep_settings(on_ticks=SWEEP_ON_TICKS, off_ticks=SWEEP_OFF_TICKS, phases=200):
    period = on_ticks + off_ticks
    return {
        "AOTF sweep phases": int(phases),
        "AOTF sweep ticks/phase": int(period),
        "AOTF sweep ticks": int(period),
        'AOTF sweep "on" (Ticks)': int(on_ticks),
        'AOTF sweep "off" (Ticks)': int(off_ticks),
        "AOTF sweep freq (1/tick)": 0.0,
        "AOTF sweep periods/phase": 0.0,
    }


def clock_settings():
    return {
        "AOTF # of points per trigger": POINTS,
        "AOTF # of points per trigger PB": POINTS,
        "AOTF Delay (Ticks)": 4_000,             # 100 us lead
        "AOTF pulse width (ticks)": SWEEP_ON_TICKS,
        "AOTF pulse width (ticks) PB": SWEEP_ON_TICKS,
    }


def gate_words():
    """Per-point 'AOTF on?' gate, high for the first half of each block,
    streamed in both prefix slots (spike 32 found the gate reaches
    'AO DMA'.AOTF on? from there)."""
    gate = np.array([1] * (POINTS // 2) + [0] * (POINTS - POINTS // 2))
    return pack_points(x_galvo=np.zeros(POINTS, dtype=int), z_galvo=0, prefix0=gate, prefix1=gate)


def poll_out(ctrl, seconds):
    regs = ctrl._session.registers
    seen = []
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        o = regs["AOTF ch out (V)"].read()
        seen.append(tuple(int(o[k]) for k in AOTF_LEVEL_KEYS))
    return seen


def verdict_for(seen, ch, level):
    """Judge the TARGET channel only, and only against the level asked
    for -- a 1-count flicker on a neighbouring channel is not modulation."""
    vals = [s[ch] for s in seen]
    lo, hi = min(vals), max(vals)
    at_level = sum(1 for v in vals if abs(v - level) <= max(2, level // 100))
    at_zero = sum(1 for v in vals if v == 0)
    frac_hi = at_level / max(1, len(vals))
    if at_level and at_zero:
        return "MODULATING", f"ch{ch} toggles 0 <-> {hi} (high {frac_hi * 100:.0f} % of polls)"
    if at_level:
        return "DC", f"ch{ch} flat at {hi}"
    if hi == 0:
        return "ZERO", f"ch{ch} flat 0"
    return "OTHER", f"ch{ch} range {lo}..{hi}, never at the requested {level}"


def scope_aotf(snap, ch):
    """AOTF columns of the AI stream: 16/17 are AOTF 0/1, 19..23 are AOTF 2..6."""
    frames = snap.frames
    if frames is None or not len(frames):
        return "scope: no frames"
    parts = []
    for c in (16, 17, 19, 20, 21, 22, 23):
        if c >= frames.shape[1]:
            continue
        col = frames[:, c]
        lo, hi = int(col.min()), int(col.max())
        if lo != hi:
            parts.append(f"{c}:{AI_CHANNEL_NAMES[c]} {lo}..{hi} MOVES")
        elif hi:
            parts.append(f"{c}:{AI_CHANNEL_NAMES[c]} flat {hi}")
    return "scope: " + ("; ".join(parts) if parts else "all AOTF columns flat 0")


def trial(ctrl, scope, label, *, level, ch, aotf_mode, ch_mode,
          sweep=None, clock=None, poll_s=0.6):
    regs = ctrl._session.registers
    regs["AOTF Sweep settings"].write(sweep if sweep is not None else dict(SWEEP_ZERO))
    for k, v in (clock if clock is not None else CLOCK_ZERO).items():
        regs[k].write(v)
    regs["AOTF Mode"].write(aotf_mode)
    regs["AOTF ch Mode"].write(ch_mode)
    regs["AOTF sweep mode"].write(0)          # Step
    regs["Set F.P. (T)"].write(True)
    time.sleep(0.02)

    scope.clear()
    ok = ctrl.start_free_run(PERIOD, EXPOSURE, n_triggers=None, ao_points_per_trigger=POINTS,
                             ao_ticks_between_points=TICKS, clamp_ao=True, clamp_counts=CLAMP,
                             allow_aotf_gate=True, ao_words=gate_words(),
                             trigger_up_ticks=POINTS * TICKS + 40_000)
    if not ok:
        print(f"  {label:34s} ARM FAILED: {ctrl.last_error}")
        return "ARM-FAIL"
    ctrl.set_aotf_levels({ch: level})
    seen = poll_out(ctrl, poll_s)
    snap = scope.snapshot(seconds=poll_s)
    ctrl.stop_free_run()
    v, detail = verdict_for(seen, ch, level)
    print(f"  {label:34s} {v:11s} {detail}")
    print(f"  {'':34s} {scope_aotf(snap, ch)}")
    return v


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", type=int, default=6308,
                    help="AOTF level in counts (6308 = 1.925 V = the rig's 488 at 38.5 %%)")
    ap.add_argument("--ch", type=int, default=2, help="AOTF channel (2 = 488 = AO7)")
    args = ap.parse_args()

    ctrl = FpgaTriggerController()
    ctrl.connect()
    regs = ctrl._session.registers
    scope = FpgaScope(ctrl, channels=24, period_ticks=400, buffer_seconds=4.0)
    scope.start()
    results = {}
    try:
        print(f"level {args.level} counts = {args.level / 3276.7:.3f} V on AOTF ch {args.ch}")
        print(f"sweep {SWEEP_ON_TICKS / TICKS_PER_MS:g} ms on / {SWEEP_OFF_TICKS / TICKS_PER_MS:g} ms off; "
              f"clock delay 100 us, {POINTS} points/trigger\n")

        print("=== the 3x3 mode matrix, clock + sweep registers populated ===")
        print("    ('AOTF Mode' drives the Clock loop, 'AOTF ch Mode' the Channel Output loop)\n")
        for am in (2, 0, 1):
            for cm in (2, 0, 1):
                key = f"Mode={MODE_NAMES[am]} / chMode={MODE_NAMES[cm]}"
                results[key] = trial(ctrl, scope, key, level=args.level, ch=args.ch,
                                     aotf_mode=am, ch_mode=cm,
                                     sweep=sweep_settings(), clock=clock_settings())

        print("\n=== control: the same matrix cells that worked, with NO extra registers ===")
        for am, cm in ((2, 2),):
            key = f"bare Mode={MODE_NAMES[am]} / chMode={MODE_NAMES[cm]}"
            results[key] = trial(ctrl, scope, key, level=args.level, ch=args.ch,
                                 aotf_mode=am, ch_mode=cm)

    finally:
        print("\nrestoring defaults ...")
        try:
            regs["AOTF Sweep settings"].write(dict(SWEEP_ZERO))
            for k, v in CLOCK_ZERO.items():
                regs[k].write(v)
            regs["AOTF Mode"].write(2)
            regs["AOTF ch Mode"].write(2)
            regs["AOTF sweep mode"].write(0)
            regs["Set F.P. (T)"].write(True)
            ctrl.safe_state()
        except Exception as e:                                  # noqa: BLE001
            print("  restore failed:", type(e).__name__, e)
        ctrl.close()

    print("\n=== SUMMARY ===")
    for k, v in results.items():
        print(f"  {k:40s} {v}")
    winners = [k for k, v in results.items() if v == "MODULATING"]
    print("\nMODULATING configurations:", winners or "NONE")
    return 0 if winners else 1


if __name__ == "__main__":
    sys.exit(main())
