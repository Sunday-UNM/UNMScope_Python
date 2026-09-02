"""
Two-phase combined scope check:
  Phase A: 5 clean square pulses on X Galvo (AO1) -- static voltage
           toggled 0V/+1V, 5 times, each level held long enough to see
           clearly. No FPGA triggering involved in this phase.
  Phase B: 10 Cam Ext Trigger (DIO4) pulses, via the proven looped
           single-trigger primitive.
Kept as two separate phases (not interleaved) because while actually
firing a trigger, X Galvo is driven by the (all-zero) waveform stream,
not the static-voltage register -- interleaving would make the square
wave and the trigger pulses fight each other on the same channel.
"""
import sys
import time

sys.path.insert(0, r"H:\UNM_Lightsheet\UNMScope_Python\src")

import nifpga
from unmscope.hardware.fpga_trigger import (
    BITFILE, RESOURCE, AO_MODE_SET_AO, STATIC_ZERO, FpgaTriggerController,
)

N_SQUARE_PULSES = 5
SQUARE_LEVEL_HOLD_S = 0.5   # each high/low level held this long
N_TRIGGERS = 10
INTER_TRIGGER_DELAY_S = 0.3

AO_VOLT_RANGE = 10.0
AO_COUNTS_FULL_SCALE = 32767


def volts_to_counts(v):
    return max(-32767, min(32767, round((v / AO_VOLT_RANGE) * AO_COUNTS_FULL_SCALE)))


def phase_a_square_wave():
    print(f"=== Phase A: {N_SQUARE_PULSES} square pulses on X Galvo (AO1) ===")
    with nifpga.Session(bitfile=BITFILE, resource=RESOURCE) as session:
        session.reset()
        time.sleep(0.3)
        session.run()
        time.sleep(0.3)
        regs = session.registers

        regs["AO Mode"].write(AO_MODE_SET_AO)
        regs["Static AO to set"].write(STATIC_ZERO)
        regs["Set F.P. (T)"].write(True)

        high = dict(STATIC_ZERO)
        high["X Galvo"] = volts_to_counts(1.0)
        low = dict(STATIC_ZERO)

        for i in range(1, N_SQUARE_PULSES + 1):
            print(f"  square pulse {i}/{N_SQUARE_PULSES}: HIGH (~1V)")
            regs["Static AO to set"].write(high)
            regs["Set F.P. (T)"].write(True)
            time.sleep(SQUARE_LEVEL_HOLD_S)
            print(f"  square pulse {i}/{N_SQUARE_PULSES}: LOW (0V)")
            regs["Static AO to set"].write(low)
            regs["Set F.P. (T)"].write(True)
            time.sleep(SQUARE_LEVEL_HOLD_S)

        regs["Static AO to set"].write(STATIC_ZERO)
        regs["Set F.P. (T)"].write(True)
    print("Phase A done.\n")


def phase_b_trigger_burst():
    print(f"=== Phase B: {N_TRIGGERS} Cam Trigger pulses (DIO4) ===")
    ctrl = FpgaTriggerController()
    ctrl.connect()

    def progress(i, n):
        print(f"  trigger {i}/{n} fired at {time.strftime('%H:%M:%S')}")

    # Now a true trigger-to-trigger period, not a post-fire gap.
    fired = ctrl.fire_burst(N_TRIGGERS, period_s=INTER_TRIGGER_DELAY_S, on_progress=progress)
    ctrl.close()
    print(f"Phase B done. {fired}/{N_TRIGGERS} fired.\n")


def main():
    for c in (3, 2, 1):
        print(f"starting in {c}...")
        time.sleep(1.0)
    phase_a_square_wave()
    print("Pausing 2s between phases...")
    time.sleep(2.0)
    phase_b_trigger_burst()
    print("All done. Recap:")
    print(f"  Phase A: {N_SQUARE_PULSES} square pulses on X Galvo (ch with AO1)")
    print(f"  Phase B: {N_TRIGGERS} pulses on Cam Trigger (ch with DIO4)")
    print("How many did you actually see in each?")


if __name__ == "__main__":
    main()
