"""
Sanity check: does the REWRITTEN FpgaTriggerController.fire_single_trigger()
still produce one clean pulse in isolation (a single call, not a loop)?
This decouples "did the rewrite break something" from "is the burst loop
or the oscilloscope setup the problem".
"""
import sys
import time

sys.path.insert(0, r"H:\UNM_Lightsheet\UNMScope_Python\src")

from unmscope.hardware.fpga_trigger import FpgaTriggerController


def main():
    print("=== Single fire_single_trigger() sanity check ===\n")
    ctrl = FpgaTriggerController()
    print("Connecting...")
    ctrl.connect()
    print("Connected and in safe state.\n")

    for c in (3, 2, 1):
        print(f"  firing in {c}...")
        time.sleep(1.0)

    print(">>> FIRING ONE pulse NOW -- watch/count on the scope <<<")
    ok = ctrl.fire_single_trigger()
    print(f"fire_single_trigger() returned {ok}")

    print("\nClosing (returns to safe state)...")
    ctrl.close()
    print("Done. Did you see ONE pulse?")


if __name__ == "__main__":
    main()
