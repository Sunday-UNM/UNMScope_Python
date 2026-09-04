"""AOTF on the DEPLOYED bitfile: which register / stream slot does what.

FPGA ONLY. The galvo channels are clamped to +-100 mV and every galvo
value streamed is 0 anyway; the AOTF levels are 0 counts for the
gate-slot experiments and --level counts (default 164 = 50 mV = 1 % of
the 5 V AOTF range) for the level experiments, for a few seconds.

What is known from the source (docs/aotf.md): the per-point AO DMA
cluster of the deployed build is {X Galvo, Z Galvo, Z Piezo, Dither
Galvo, Tiling, Filter, AOTF on?} -- the 'AO DMA' indicator shows the
current point including that bool -- so the gate travels in the Wvfrm2
stream, presumably in one of the two "prefix" slots. The levels live in
'AOTF ch (V)'; 'AOTF ch out (V)' is the FPGA's own readback of what it
puts on the AOTF outputs.

Experiments (each prints what moved; nothing is asserted):
  A  gate slot: stream prefix0 / prefix1 / both = 1 for the first half
     of each 20 ms block, poll 'AO DMA'.AOTF on? and watch scope columns
     16-28 (AOTF0-6, Perfusion, Shutter, Channel Shutters).
  B  static paths with a small level: Static AO 'AOTF on?', 'AOTF level
     to set' -> 'AOTF ch out (V)'.
  C  level + gate during a run (the production path): does 'AOTF ch out
     (V)' follow the gate, and which scope columns show it.
  D  (only if C shows nothing) the AOTF clock engine registers.

    python -u spikes/32_aotf_registers_on_scope.py [--level 164]
"""
from __future__ import annotations

import argparse
import sys
import time

import numpy as np

from unmscope.hardware.fpga_scope import AI_CHANNEL_NAMES, IDX_DIO4, FpgaScope, digital_edges
from unmscope.hardware.fpga_trigger import AOTF_LEVEL_KEYS, FpgaTriggerController
from unmscope.hardware.waveform import pack_points

CLAMP = 328                 # +-100 mV on the galvo channels (content is 0 anyway)
POINTS = 200                # 20 ms block at 100 us / point
TICKS = 4000
PERIOD, EXPOSURE = 0.100, 0.080
GATE_ON_POINTS = 100        # gate high for the first 10 ms of each block


def runs(seq):
    out = []
    for v in seq:
        if out and out[-1][0] == v:
            out[-1][1] += 1
        else:
            out.append([int(v), 1])
    return [(v, n) for v, n in out]


def gate_words(slot: str):
    gate = np.array([1] * GATE_ON_POINTS + [0] * (POINTS - GATE_ON_POINTS))
    p0 = gate if slot in ("prefix0", "both") else 0
    p1 = gate if slot in ("prefix1", "both") else 0
    return pack_points(x_galvo=np.zeros(POINTS, dtype=int), z_galvo=0, prefix0=p0, prefix1=p1)


def poll(ctrl, seconds):
    regs = ctrl._session.registers
    out = []
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        dma = regs["AO DMA"].read()
        o = regs["AOTF ch out (V)"].read()
        out.append((time.perf_counter() - t0, bool(dma["AOTF on?"]), tuple(int(o[k]) for k in AOTF_LEVEL_KEYS)))
    return out


def describe_poll(samples):
    n = len(samples)
    on = sum(1 for s in samples if s[1])
    outs = {}
    for s in samples:
        outs[s[2]] = outs.get(s[2], 0) + 1
    outs_txt = ", ".join(f"{k}x{v}" for k, v in sorted(outs.items(), key=lambda kv: -kv[1])[:4])
    # out while gate on / off
    on_out = {s[2] for s in samples if s[1]}
    off_out = {s[2] for s in samples if not s[1]}
    return (f"{n} polls: 'AO DMA'.AOTF on? True in {on / max(1, n) * 100:.1f} %; "
            f"'AOTF ch out (V)' values {outs_txt}; out while gate on {sorted(on_out)[:3]}, "
            f"while off {sorted(off_out)[:3]}")


def describe_scope(snap, label):
    frames, fs = snap.frames, snap.fs_hz
    rising, _ = digital_edges(frames[:, IDX_DIO4])
    active = []
    for col in range(14, 29):
        frac = float(np.mean(frames[:, col] > 2048))
        if frac > 0:
            active.append(f"{col}:{AI_CHANNEL_NAMES[col]}={frac * 100:.1f}%")
    print(f"  scope [{label}]: {len(rising)} DIO4 edges; digital columns high: {active or 'none besides 0'}")
    if len(rising):
        e = rising[min(1, len(rising) - 1)]
        seg = frames[e - 5: e + int(0.025 * fs)]
        for col in range(14, 29):
            rl = runs(seg[:, col] > 2048)
            if len(rl) > 1 or rl[0][0]:
                print(f"    col {col:2d} {AI_CHANNEL_NAMES[col]:<28} " + ", ".join(f"{int(v)}x{n}" for v, n in rl[:10])
                      + "   (samples of 10 us from 50 us before edge #2)")
    ao_cols = [(c, int(frames[:, c].min()), int(frames[:, c].max())) for c in range(8, 14)]
    print(f"  scope AO columns min/max: {ao_cols}")


def run_stream(ctrl, scope, words, label, level=None, poll_s=0.7, **modes):
    regs = ctrl._session.registers
    for k, v in modes.items():
        regs[k].write(v)
    if modes:
        regs["Set F.P. (T)"].write(True)
    scope.clear()
    ok = ctrl.start_free_run(PERIOD, EXPOSURE, n_triggers=None, ao_points_per_trigger=POINTS,
                             ao_ticks_between_points=TICKS, clamp_ao=True, clamp_counts=CLAMP,
                             allow_aotf_gate=True, ao_words=words, trigger_up_ticks=POINTS * TICKS + 40_000)
    if not ok:
        print(f"  [{label}] ARM FAILED: {ctrl.last_error}")
        return None
    if level is not None:
        # a clamped arm writes 0 levels; set the test level explicitly AFTER arming
        got = ctrl.set_aotf_levels(level)
        print(f"  [{label}] levels set: {got}")
    samples = poll(ctrl, poll_s)
    snap = scope.snapshot(seconds=poll_s)
    final = ctrl.stop_free_run()
    print(f"  [{label}] triggers {final}; {describe_poll(samples)}")
    describe_scope(snap, label)
    for k in modes:
        regs[k].write(2 if "Mode" in k else 0)
    if modes:
        regs["Set F.P. (T)"].write(True)
    return samples


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", type=int, default=164, help="AOTF test level in counts (164 = 50 mV)")
    args = ap.parse_args()
    LEVEL = {0: args.level, 2: args.level // 2}

    ctrl = FpgaTriggerController()
    ctrl.connect()
    regs = ctrl._session.registers
    scope = FpgaScope(ctrl, channels=29, period_ticks=400, buffer_seconds=4.0)
    try:
        scope.start()
        print("initial:", {n: regs[n].read() for n in ("AOTF ch out (V)", "AOTF Mode", "AOTF ch Mode", "AOTF sweep mode",
                                                         "AOTF level to set", "AO DMA")})

        print("\n=== A. which stream slot is the per-point 'AOTF on?' gate (levels 0) ===")
        found = None
        for slot in ("prefix0", "prefix1", "both"):
            s = run_stream(ctrl, scope, gate_words(slot), f"A gate in {slot}")
            if s and any(x[1] for x in s) and found is None:
                found = slot
        print(f"  -> gate slot: {found}")

        print("\n=== B. static paths with a small level ===")
        got = ctrl.set_aotf_levels(LEVEL)
        print("  levels written:", got, "| out:", ctrl.read_aotf_out())
        time.sleep(0.2)
        print("  after 0.2 s, nothing else set: out =", ctrl.read_aotf_out())
        static_on = {"X Galvo": 0, "Z Galvo": 0, "Z Piezo": 0, "Dither Galvo": 0, "Tiling": 0, "Filter": 0, "AOTF on?": True}
        regs["Static AO to set"].write(static_on)
        regs["Set F.P. (T)"].write(True)
        time.sleep(0.2)
        snap = scope.snapshot(seconds=0.2)
        print("  Static AO 'AOTF on?'=True (AO Mode = Set AO): out =", ctrl.read_aotf_out(), "| AO DMA =", regs["AO DMA"].read())
        describe_scope(snap, "B static AOTF on?")
        static_on["AOTF on?"] = False
        regs["Static AO to set"].write(static_on)
        regs["Set F.P. (T)"].write(True)
        time.sleep(0.1)
        print("  Static AO 'AOTF on?'=False: out =", ctrl.read_aotf_out())
        regs["AOTF level to set"].write(True)
        regs["Set F.P. (T)"].write(True)
        time.sleep(0.2)
        snap = scope.snapshot(seconds=0.2)
        print("  'AOTF level to set'=True (AOTF Mode = Set AOTF): out =", ctrl.read_aotf_out())
        describe_scope(snap, "B AOTF level to set")
        regs["AOTF level to set"].write(False)
        regs["Set F.P. (T)"].write(True)
        time.sleep(0.1)
        print("  'AOTF level to set'=False: out =", ctrl.read_aotf_out())
        ctrl.safe_state()
        print("  safe_state -> levels", regs["AOTF ch (V)"].read(), "| out", ctrl.read_aotf_out())

        print("\n=== C. level + gate during a run (production path) ===")
        slot = found or "both"
        run_stream(ctrl, scope, gate_words(slot), f"C default modes, gate in {slot}", level=LEVEL)
        run_stream(ctrl, scope, gate_words(slot), "C AOTF ch Mode=0 (Start/Run), sweep Sync", level=LEVEL,
                   **{"AOTF ch Mode": 0, "AOTF sweep mode": 2})
        run_stream(ctrl, scope, gate_words(slot), "C AOTF Mode=0 + ch Mode=0, sweep Sync", level=LEVEL,
                   **{"AOTF Mode": 0, "AOTF ch Mode": 0, "AOTF sweep mode": 2})

        print("\n=== D. AOTF clock engine (Delay 1 ms, pulse 100 us, 1 point/trigger, Step) ===")
        regs["AOTF # of points per trigger"].write(1)
        regs["AOTF # of points per trigger PB"].write(1)
        regs["AOTF Delay (Ticks)"].write(40_000)
        regs["AOTF pulse width (ticks)"].write(4_000)
        regs["AOTF pulse width (ticks) PB"].write(4_000)
        run_stream(ctrl, scope, gate_words("both"), "D engine, Mode=0/ch Mode=0/Step", level=LEVEL,
                   **{"AOTF Mode": 0, "AOTF ch Mode": 0, "AOTF sweep mode": 0})
        for k in ("AOTF # of points per trigger", "AOTF # of points per trigger PB", "AOTF Delay (Ticks)",
                  "AOTF pulse width (ticks)", "AOTF pulse width (ticks) PB"):
            regs[k].write(0)
        regs["Set F.P. (T)"].write(True)
        print("\nfinal:", {n: regs[n].read() for n in ("AOTF ch out (V)", "AOTF ch (V)", "AOTF Mode", "AOTF ch Mode",
                                                       "AOTF sweep mode", "AOTF level to set")})
    finally:
        try:
            ctrl.stop_free_run()
        except Exception:
            pass
        scope.stop()
        try:
            ctrl.set_ao_clamp(True, 0)
        except Exception:
            pass
        ctrl.close()
        print("FPGA safe (clamp 0, AOTF levels 0), closed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
