"""
Validate the pragmatic looped-single-trigger burst approach: fire N
pulses by calling the fully-proven fire_single_trigger() N times in a
row, rather than depending on the FPGA's native (still not fully
understood) multi-trigger burst mechanism. See fpga_trigger.py's module
docstring and docs/fpga_io_map.md for why.
"""
import sys
import time

sys.path.insert(0, r"H:\UNM_Lightsheet\UNMScope_Python\src")

from unmscope.hardware.fpga_trigger import FpgaTriggerController

N_TRIGGERS = 3
INTER_TRIGGER_DELAY_S = 0.5  # slow and easy to count on the scope


def main():
    print(f"=== Looped single-trigger burst test: N={N_TRIGGERS} ===\n")
    ctrl = FpgaTriggerController()
    print("Connecting...")
    ctrl.connect()
    print("Connected and in safe state.\n")

    for c in (3, 2, 1):
        print(f"  firing in {c}...")
        time.sleep(1.0)

    print(f">>> FIRING {N_TRIGGERS} pulses, {INTER_TRIGGER_DELAY_S}s apart -- watch/count on the scope <<<")

    def progress(i, n):
        print(f"  pulse {i}/{n} fired at {time.strftime('%H:%M:%S')}")

    fired = ctrl.fire_burst(N_TRIGGERS, inter_trigger_delay_s=INTER_TRIGGER_DELAY_S, on_progress=progress)
    print(f"\nfire_burst() reports {fired}/{N_TRIGGERS} fired.")

    print("\nClosing (returns to safe state)...")
    ctrl.close()
    print("Done. Did you see exactly", N_TRIGGERS, "clean pulses,", INTER_TRIGGER_DELAY_S, "s apart?")


if __name__ == "__main__":
    main()
