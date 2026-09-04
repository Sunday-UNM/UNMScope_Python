"""Record every FPGA register's value right after reset()+run(), BEFORE
writing anything. This is the mandatory first step from
docs/trigger_free_run_plan.md ("landmine 2").

WHY
---
The deployed bitfile (bin\data\...lvbitx) exposes controls our code has
NEVER written -- `Added time (Ticks)`, `Frame index to add time to`,
`Trigger skips`, `Trigger Look for Arm?`, `Trigger Check Filter?`, plus a
handful of AO/AOTF/shutter/perfusion mode registers. Their compile-time
defaults decide whether a free-running trigger train behaves (a non-zero
Added time stretches one cycle per stack; a True Look-for-Arm gates every
trigger on the DIO1 pin). Nobody has ever looked. This looks, and records
the answer to a JSON file so it is documented once and for all.

WHAT IT DOES TO THE HARDWARE
----------------------------
reset() + run() -- exactly what FpgaTriggerController.connect() already
does on every connect -- then READS. No trigger is armed, no FIFO is
started, no AO channel leaves its default. The only write is the usual
safe state at the very end (AO Mode = Set AO, all static AO = 0), which
every session does anyway. Nothing else may hold RIO0 while this runs.

USAGE
-----
    python spikes/17_read_reset_defaults.py                # prints + writes docs/fpga_reset_defaults.json
    python spikes/17_read_reset_defaults.py --out foo.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time

import nifpga

from unmscope.hardware.fpga_trigger import AO_MODE_SET_AO, BITFILE, RESOURCE, STATIC_ZERO

# NI framework registers -- not part of the LabVIEW front panel, skip.
NI_INTERNAL = {"InterruptEnable", "InterruptMask", "InterruptStatus",
               "DiagramReset", "ViControl", "ViSignature"}

# Controls that fire_single_trigger() never writes. These are the ones
# whose defaults we are actually here to learn.
NEVER_WRITTEN_BY_US = [
    "Added time (Ticks)", "Frame index to add time to", "Trigger skips",
    "Trigger Look for Arm?", "Trigger Check Filter?",
    "AO DMA Timeout (ticks per read)", "AO Limit Max (counts)", "AO Limit Min (counts)",
    "Perfusion stack #s", "Perfusion level?", "Perfusion manual set?",
    "Shutter Mode", "Shutter level to set", "Shutter PBlast (Ticks)",
    "AOTF Mode", "AOTF ch Mode", "AOTF sweep mode", "AOTF level to set",
    "AOTF ch (V)", "AOTF Sweep settings", "AOTF Delay (Ticks)",
    "AOTF pulse width (ticks)", "AOTF # of points per trigger",
    "AO # of points per trigger PB", "AO ticks between points PB",
    "AOTF # of points per trigger PB", "AOTF pulse width (ticks) PB",
    "X Galvo Real Time Mods (counts)", "Z Galvo Real Time Mods (counts)",
    "# of steps", "Capillary V", "Filter position set", "Stop", "Tab Control",
]


def read_all(session, names):
    out = {}
    for name in names:
        try:
            out[name] = session.registers[name].read()
        except Exception as e:  # keep going; record the failure
            out[name] = f"<read error: {type(e).__name__}: {e}>"
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="docs/fpga_reset_defaults.json")
    args = ap.parse_args()

    bf = nifpga.Bitfile(BITFILE)
    print(f"bitfile   : {BITFILE}")
    print(f"signature : {bf.signature}")
    controls = sorted(n for n, r in bf.registers.items() if not r.is_indicator and n not in NI_INTERNAL)
    indicators = sorted(n for n, r in bf.registers.items() if r.is_indicator and n not in NI_INTERNAL)
    print(f"{len(controls)} controls, {len(indicators)} indicators\n")

    print(f"Opening {RESOURCE}, reset() + run() ...")
    session = nifpga.Session(bitfile=BITFILE, resource=RESOURCE)
    record = {
        "recorded_at": dt.datetime.now().isoformat(timespec="seconds"),
        "bitfile": BITFILE,
        "signature": bf.signature,
        "note": "Values read immediately after session.reset()+run(), before ANY write. "
                "indicators_after_500ms is a second read to show what moves on its own.",
    }
    try:
        session.reset()
        time.sleep(0.3)
        session.run()
        time.sleep(0.3)

        # ---- THE READ. Nothing has been written yet. ----
        t0 = time.perf_counter()
        ctl = read_all(session, controls)
        ind = read_all(session, indicators)
        t_read = time.perf_counter() - t0
        time.sleep(0.5)
        ind2 = read_all(session, indicators)

        record["controls"] = ctl
        record["indicators"] = ind
        record["indicators_after_500ms"] = ind2

        print(f"Read {len(ctl)+len(ind)} registers in {t_read*1000:.1f} ms.\n")
        print("=" * 72)
        print("CONTROLS our code has NEVER written  <-- the point of this script")
        print("=" * 72)
        for n in NEVER_WRITTEN_BY_US:
            if n in ctl:
                print(f"  {n:<36} = {ctl[n]!r}")
        print()
        print("=" * 72)
        print("All other controls (the ones fire_single_trigger() overwrites)")
        print("=" * 72)
        for n in controls:
            if n not in NEVER_WRITTEN_BY_US:
                print(f"  {n:<36} = {ctl[n]!r}")
        print()
        print("=" * 72)
        print("INDICATORS at reset (and 500 ms later, if changed)")
        print("=" * 72)
        for n in indicators:
            changed = "" if ind[n] == ind2[n] else f"   -> {ind2[n]!r} after 500 ms  ** MOVING **"
            print(f"  {n:<36} = {ind[n]!r}{changed}")

    finally:
        try:
            regs = session.registers
            regs["AO Mode"].write(AO_MODE_SET_AO)
            regs["Static AO to set"].write(STATIC_ZERO)
            regs["Set F.P. (T)"].write(True)
        except Exception as e:
            print(f"  (safe-state write raised: {e})")
        session.close()
        print("\nSafe state written, session closed.")

    with open(args.out, "w") as fh:
        json.dump(record, fh, indent=2, default=str)  # Decimal fields in AOTF Sweep settings
    print(f"Recorded to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
