"""
Use the FPGA's OWN internal diagnostic registers as a "scope" into its
trigger-processing state, instead of relying on external oscilloscope
probing (which has become inconsistent/hard to diagnose remotely).
Reads a full snapshot of every relevant indicator BEFORE arming, right
after arming (ready), and after firing -- so we can see exactly what
changes internally, decoupled from any physical probe/cable/scope-setup
uncertainty.
"""
import sys
import time

sys.path.insert(0, r"H:\UNM_Lightsheet\UNMScope_Python\src")

import nifpga
from unmscope.hardware.fpga_trigger import (
    BITFILE, RESOURCE, AO_MODE_START_RUN_WVFRM, AO_MODE_SET_AO,
    CYCLE_TICKS, HOLDOFF_TICKS, AO_TICKS_BETWEEN_POINTS, AO_POINTS_PER_TRIGGER,
    ENABLE_HOLD_S, WVFRM_SEED_WORDS, STATIC_ZERO,
)

DIAGNOSTIC_REGS = [
    "# of triggers read", "# of triggers ignored", "# AO generated",
    "AO Waveform State", "Int Cycle Trigger", "Int Cycle+Added Trigger",
    "AO wvfrm ready", "AO DMA Error", "AI Error", "DMA Get Exp error",
    "Trigger Enable?", "AO Mode", "Running" if False else "SW Version",
]


def snapshot(regs, label):
    print(f"--- {label} ---")
    for name in DIAGNOSTIC_REGS:
        try:
            print(f"  {name}: {regs[name].read()}")
        except Exception as e:
            print(f"  {name}: <error: {e}>")


def main():
    print("Opening FPGA session (with reset+run for a clean state)...")
    with nifpga.Session(bitfile=BITFILE, resource=RESOURCE) as session:
        session.reset()
        time.sleep(0.3)
        session.run()
        time.sleep(0.3)
        regs = session.registers

        regs["AO Mode"].write(AO_MODE_SET_AO)
        regs["Static AO to set"].write(STATIC_ZERO)
        regs["Set F.P. (T)"].write(True)

        snapshot(regs, "BEFORE arming")

        regs["Cam Trigger delay (ticks)"].write(0)
        regs["# of triggers"].write(1)
        regs["Continuous Mode"].write(False)
        regs["Cycle(Ticks)"].write(CYCLE_TICKS)
        regs["Trigger up (ticks)"].write(HOLDOFF_TICKS)
        regs["AO Trigger delay (ticks)"].write(0)
        regs["Free run"].write(False)
        regs["AI # of channels"].write(0)
        regs["AI loop period (ticks)"].write(AO_TICKS_BETWEEN_POINTS)
        regs["AO # of points per trigger"].write(AO_POINTS_PER_TRIGGER)
        regs["AO ticks between points"].write(AO_TICKS_BETWEEN_POINTS)
        regs['Shutter Ticks "on" (Ticks)'].write(0)
        on_off_one = {"# on": 1, "# off": 0}
        regs["Trigger #s"].write(on_off_one)
        regs["Trigger stack #s"].write(on_off_one)
        regs["Trigger blast #s"].write(on_off_one)
        regs["Set F.P. (T)"].write(True)

        fifo = session.fifos["Wvfrm2"]
        fifo.stop()
        fifo.start()
        fifo.write(WVFRM_SEED_WORDS, timeout_ms=2000)

        regs["AO Mode"].write(AO_MODE_START_RUN_WVFRM)
        regs["Set F.P. (T)"].write(True)
        t0 = time.time()
        ready = False
        while time.time() - t0 < 3.0:
            ready = regs["AO wvfrm ready"].read()
            if ready:
                break
            time.sleep(0.01)

        print(f"\nAO wvfrm ready = {ready} (waited {time.time()-t0:.2f}s)")
        snapshot(regs, "AFTER arming (before firing)")

        if ready:
            print("\n>>> Firing (no scope needed for this test) <<<")
            regs["Trigger Enable?"].write(True)
            regs["Set F.P. (T)"].write(True)
            time.sleep(ENABLE_HOLD_S)
            regs["Trigger Enable?"].write(False)
            regs["Set F.P. (T)"].write(True)
            time.sleep(0.3)  # let any FPGA-side counters settle/update
            snapshot(regs, "AFTER firing")
        else:
            print("Never became ready -- not firing.")

        fifo.stop()
        regs["AO Mode"].write(AO_MODE_SET_AO)
        regs["Static AO to set"].write(STATIC_ZERO)
        regs["Set F.P. (T)"].write(True)

    print("\nSession closed.")


if __name__ == "__main__":
    main()
