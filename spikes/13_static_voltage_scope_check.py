"""
Scope sanity check: hold a SUSTAINED static voltage (not a brief pulse) on
X Galvo (AO1) for several seconds. This removes all timing-sensitivity
from the equation -- any working oscilloscope/probe/cable chain should
trivially show a steady DC-like step, in any trigger mode, at any
timebase. If this ISN'T visible, the issue is the scope/probe/connection,
not pulse timing. Also completes the long-deferred "Stage B" voltage
verification from the original plan.

Safe: X Galvo, +1.000V, well within a light-sheet galvo's normal range.
"""
import sys
import time

sys.path.insert(0, r"H:\UNM_Lightsheet\UNMScope_Python\src")

import nifpga
from unmscope.hardware.fpga_trigger import BITFILE, RESOURCE, AO_MODE_SET_AO, STATIC_ZERO

TEST_VOLTS = 1.000
HOLD_SECONDS = 15

# PCIe-7852R AO range assumption: +/-10V mapped linearly to +/-32767 counts.
AO_VOLT_RANGE = 10.0
AO_COUNTS_FULL_SCALE = 32767


def volts_to_counts(v):
    return max(-32767, min(32767, round((v / AO_VOLT_RANGE) * AO_COUNTS_FULL_SCALE)))


def main():
    print(f"=== Static voltage scope check: X Galvo (AO1) = +{TEST_VOLTS:.3f}V for {HOLD_SECONDS}s ===\n")
    with nifpga.Session(bitfile=BITFILE, resource=RESOURCE) as session:
        session.reset()
        time.sleep(0.3)
        session.run()
        time.sleep(0.3)
        regs = session.registers

        regs["AO Mode"].write(AO_MODE_SET_AO)
        regs["Static AO to set"].write(STATIC_ZERO)
        regs["Set F.P. (T)"].write(True)
        print("Safe (zero) state confirmed first.")

        counts = volts_to_counts(TEST_VOLTS)
        cluster = dict(STATIC_ZERO)
        cluster["X Galvo"] = counts
        regs["Static AO to set"].write(cluster)
        regs["Set F.P. (T)"].write(True)
        print(f"\nX Galvo set to {counts} counts (~{TEST_VOLTS:.3f}V). Holding for {HOLD_SECONDS}s.")
        print("Probe AO1 (X Galvo) now -- any coupling/trigger mode, DC coupling recommended.")
        print("You should see a steady step to ~1V and back to 0V, no fast timing needed.\n")

        for remaining in range(HOLD_SECONDS, 0, -1):
            print(f"  holding... {remaining}s left", end="\r")
            time.sleep(1.0)
        print("\n\nReturning to zero.")

        regs["Static AO to set"].write(STATIC_ZERO)
        regs["Set F.P. (T)"].write(True)
        regs["AO Mode"].write(AO_MODE_SET_AO)
        regs["Set F.P. (T)"].write(True)

    print("Done. Did you see a steady ~1V level on AO1 for those 15 seconds?")


if __name__ == "__main__":
    main()
