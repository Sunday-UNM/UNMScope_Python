"""
Multi-trigger burst, take 3 -- using FpgaTriggerController's background
refill thread (matching the real LabVIEW architecture: HHMI - AO Host
generation loop.vi) instead of a one-shot FIFO write. See
docs/fpga_io_map.md "Multi-trigger / continuous mode: the real
architecture" for the full story of how we got here.
"""
import sys
import time

sys.path.insert(0, r"H:\UNM_Lightsheet\UNMScope_Python\src")

from unmscope.hardware.fpga_trigger import FpgaTriggerController

N_TRIGGERS = 5
REPEAT_COUNT = 3
REPEAT_PAUSE_S = 4.0


def main():
    print(f"=== Multi-trigger burst (refill thread): N={N_TRIGGERS} ===\n")
    ctrl = FpgaTriggerController()
    print("Connecting (with reset+run for a clean state)...")
    ctrl.connect()
    print("Connected and in safe state.\n")

    for rep in range(1, REPEAT_COUNT + 1):
        for c in (3, 2, 1):
            print(f"  firing in {c}...")
            time.sleep(1.0)
        print(f">>> FIRING BURST {rep}/{REPEAT_COUNT} (N={N_TRIGGERS}) NOW -- watch/count on the scope <<<")
        ok = ctrl.fire_burst(N_TRIGGERS)
        print(f"  fire_burst() returned ok={ok}")
        # Diagnostic: read live FPGA indicators to see WHERE the sequence
        # actually stopped, instead of guessing further from diagrams.
        try:
            regs = ctrl._session.registers
            print(f"    # of triggers read: {regs['# of triggers read'].read()}")
            print(f"    # of triggers ignored: {regs['# of triggers ignored'].read()}")
            print(f"    # AO generated: {regs['# AO generated'].read()}")
            print(f"    AO Waveform State: {regs['AO Waveform State'].read()}")
            print(f"    Int Cycle Trigger: {regs['Int Cycle Trigger'].read()}")
            print(f"    Int Cycle+Added Trigger: {regs['Int Cycle+Added Trigger'].read()}")
        except Exception as e:
            print(f"    (diagnostic register read failed: {e})")
        if rep < REPEAT_COUNT:
            print(f"  Pausing {REPEAT_PAUSE_S:.0f}s before next repeat...")
            time.sleep(REPEAT_PAUSE_S)

    print("\nClosing (returns to safe state)...")
    ctrl.close()
    print("Done. How many pulses appeared per burst -- exactly 5 each time?")


if __name__ == "__main__":
    main()
