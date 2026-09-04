"""Bisect WHY start_free_run()'s arm failed ('AO wvfrm ready' never True).

Starting from the PROVEN fire_single_trigger() register set + 48-word seed,
each variant adds one of the changes start_free_run() made. Every variant
runs after a fresh session.reset()+run() so a stuck engine from the
previous variant cannot leak in. Nothing is ever fired: Trigger Enable?
stays False throughout. AO channels stay at zero.

    python -u spikes/19b_arm_variants.py
"""
from __future__ import annotations

import sys
import threading
import time

import nifpga

from unmscope.hardware.fpga_trigger import (
    AO_MODE_CLEAR_AO_DMA, AO_MODE_SET_AO, AO_MODE_START_RUN_WVFRM, AO_MODE_STOP_WVFRM,
    AO_POINTS_PER_TRIGGER, AO_TICKS_BETWEEN_POINTS, BITFILE, CYCLE_TICKS, HOLDOFF_TICKS,
    RESOURCE, STATIC_ZERO, U32_MAX,
)

AO_INDICATORS = ["AO wvfrm ready", "AO Waveform State", "AO Purging", "AO Purged", "AO DMA Error",
                 "AO # points left", "# AO generated", "AO DMA Read Time (Ticks)", "# of triggers read"]


def snap(regs, label):
    vals = {n: regs[n].read() for n in AO_INDICATORS}
    err = vals["AO DMA Error"]
    print(f"    [{label:<22}] ready={int(vals['AO wvfrm ready'])} state={vals['AO Waveform State']} "
          f"purging={int(vals['AO Purging'])} purged={int(vals['AO Purged'])} "
          f"err={'/'.join(k[:4] for k, v in err.items() if v) or '-'} left={vals['AO # points left']} "
          f"gen={vals['# AO generated']} rdT={vals['AO DMA Read Time (Ticks)']}", flush=True)
    return vals


def base_registers(regs, *, continuous=False, n_triggers=1, cycle=CYCLE_TICKS, up=HOLDOFF_TICKS,
                   deployed_only=False, enable_false_first=False):
    if enable_false_first:
        regs["Trigger Enable?"].write(False)
    regs["Cam Trigger delay (ticks)"].write(0)
    regs["# of triggers"].write(n_triggers)
    regs["Continuous Mode"].write(continuous)
    regs["Cycle(Ticks)"].write(cycle)
    regs["Trigger up (ticks)"].write(up)
    regs["AO Trigger delay (ticks)"].write(0)
    regs["Free run"].write(False)
    regs["AI # of channels"].write(0)
    regs["AI loop period (ticks)"].write(AO_TICKS_BETWEEN_POINTS)
    regs["AO # of points per trigger"].write(AO_POINTS_PER_TRIGGER)
    regs["AO ticks between points"].write(AO_TICKS_BETWEEN_POINTS)
    regs['Shutter Ticks "on" (Ticks)'].write(0)
    one = {"# on": 1, "# off": 0}
    regs["Trigger #s"].write(one)
    regs["Trigger stack #s"].write(one)
    regs["Trigger blast #s"].write(one)
    if deployed_only:
        regs["Perfusion stack #s"].write({"# on": 0, "# off": 0})
        regs["Trigger skips"].write(0)
        regs["Trigger Look for Arm?"].write(False)
        regs["Trigger Check Filter?"].write(False)
        regs["Added time (Ticks)"].write(0)
        regs["Frame index to add time to"].write(0)
    regs["Set F.P. (T)"].write(True)


def run_variant(session, name, *, clear=False, configure=None, seed_words=48, refill=False,
                regs_first=True, **regkw):
    regs = session.registers
    print(f"\n== {name}", flush=True)
    session.reset(); time.sleep(0.3); session.run(); time.sleep(0.3)
    regs["AO Mode"].write(AO_MODE_SET_AO); regs["Static AO to set"].write(STATIC_ZERO); regs["Set F.P. (T)"].write(True)
    snap(regs, "after reset+run")

    if regs_first:
        base_registers(regs, **regkw)
    if clear:
        regs["AO Mode"].write(AO_MODE_CLEAR_AO_DMA); regs["Set F.P. (T)"].write(True)
        time.sleep(0.05)
        snap(regs, "after Clear AO DMA")
    fifo = session.fifos["Wvfrm2"]
    fifo.stop()
    if configure:
        fifo.configure(configure)
    fifo.start()
    rem = fifo.write([0] * seed_words, timeout_ms=2000)
    print(f"    seeded {seed_words} words, host buffer free after seed = {rem}", flush=True)
    stop = threading.Event()
    words = [0]
    def _refill():
        while not stop.is_set():
            try:
                fifo.write([0] * 256, timeout_ms=100); words[0] += 256
            except nifpga.FifoTimeoutError:
                continue
            except Exception:
                return
    th = None
    if refill:
        th = threading.Thread(target=_refill, daemon=True); th.start()
    if not regs_first:
        base_registers(regs, **regkw)
    snap(regs, "before AO Mode=0")
    regs["AO Mode"].write(AO_MODE_START_RUN_WVFRM); regs["Set F.P. (T)"].write(True)
    t0 = time.perf_counter(); ready = False
    while time.perf_counter() - t0 < 2.0:
        if regs["AO wvfrm ready"].read():
            ready = True; break
        time.sleep(0.001)
    print(f"    -> ready={ready} after {(time.perf_counter()-t0)*1e3:.1f} ms", flush=True)
    snap(regs, "after AO Mode=0")
    if refill:
        time.sleep(0.5)
        snap(regs, "after 0.5 s refill")
        print(f"    refill wrote {words[0]} words", flush=True)
    # disarm (no trigger was ever enabled)
    regs["AO Mode"].write(AO_MODE_STOP_WVFRM); regs["Set F.P. (T)"].write(True); time.sleep(0.05)
    stop.set()
    if th: th.join(timeout=1.0)
    fifo.stop()
    regs["AO Mode"].write(AO_MODE_SET_AO); regs["Static AO to set"].write(STATIC_ZERO); regs["Set F.P. (T)"].write(True)
    return ready


def main() -> int:
    results = {}
    with nifpga.Session(bitfile=BITFILE, resource=RESOURCE) as session:
        V = lambda name, **kw: results.__setitem__(name, run_variant(session, name, **kw))  # noqa: E731
        V("V0 proven single-fire set (Continuous=False, #trig=1, 48 seed)")
        V("V1 + Continuous Mode=True", continuous=True)
        V("V2 + # of triggers=U32_MAX", continuous=True, n_triggers=U32_MAX)
        V("V3 + deployed-only regs + Enable=False first", continuous=True, deployed_only=True, enable_false_first=True)
        V("V4 + Cycle/up = 4388000/194000", continuous=True, cycle=4_388_000, up=194_000)
        V("V5 + Clear AO DMA before FIFO", continuous=True, clear=True)
        V("V6 + configure(16000) + 4096 seed", continuous=True, configure=16000, seed_words=4096)
        V("V7 + refill thread (no configure)", continuous=True, refill=True)
        V("V8 everything (= start_free_run)", continuous=True, n_triggers=U32_MAX, deployed_only=True,
          enable_false_first=True, cycle=4_388_000, up=194_000, clear=True, configure=16000,
          seed_words=4096, refill=True)
        V("V9 everything but regs AFTER clear/FIFO (old refill-code order)", continuous=True, n_triggers=U32_MAX,
          deployed_only=True, enable_false_first=True, cycle=4_388_000, up=194_000, clear=True,
          configure=16000, seed_words=4096, refill=True, regs_first=False)
        # leave in safe state
        regs = session.registers
        regs["Trigger Enable?"].write(False); regs["AO Mode"].write(AO_MODE_SET_AO)
        regs["Static AO to set"].write(STATIC_ZERO); regs["Set F.P. (T)"].write(True)
    print("\n" + "=" * 70)
    for k, v in results.items():
        print(f"  {'READY' if v else 'STUCK':<6} {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
