"""
Combined scope check: watch X Galvo (AO1) and Cam Ext Trigger (DIO4)
simultaneously on two channels. Sequence, in one continuous window:
  1. Hold X Galvo at a steady +1V for a few seconds (easy, slow, confirms
     that channel is still good).
  2. Transition into the normal trigger arm sequence (X Galvo will drop
     toward 0V as AO Mode switches from "Set AO" to "Start/Run Wvfrm" and
     starts outputting the all-zero waveform stream).
  3. Fire one trigger pulse on DIO4 shortly after.
Both channels should show something in the same observation window,
without needing to catch two simultaneous brief events.
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

AO_VOLT_RANGE = 10.0
AO_COUNTS_FULL_SCALE = 32767


def volts_to_counts(v):
    return max(-32767, min(32767, round((v / AO_VOLT_RANGE) * AO_COUNTS_FULL_SCALE)))


def main():
    print("=== Combined check: X Galvo (AO1) steady 1V, then arm+fire Cam Trigger (DIO4) ===\n")
    with nifpga.Session(bitfile=BITFILE, resource=RESOURCE) as session:
        session.reset()
        time.sleep(0.3)
        session.run()
        time.sleep(0.3)
        regs = session.registers

        regs["AO Mode"].write(AO_MODE_SET_AO)
        regs["Static AO to set"].write(STATIC_ZERO)
        regs["Set F.P. (T)"].write(True)
        print("Safe (zero) state confirmed.")

        cluster = dict(STATIC_ZERO)
        cluster["X Galvo"] = volts_to_counts(1.0)
        regs["Static AO to set"].write(cluster)
        regs["Set F.P. (T)"].write(True)
        print("\nX Galvo -> ~1V. Holding for 5s (watch channel 1: should be a steady step).")
        for remaining in range(5, 0, -1):
            print(f"  holding... {remaining}s left", end="\r")
            time.sleep(1.0)
        print("\n")

        print("Now arming for trigger fire -- watch X Galvo drop toward 0V as this happens.")
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
        print(f"Armed (AO wvfrm ready={ready}). X Galvo should now read ~0V.")

        time.sleep(2.0)  # give time to see the drop-to-zero clearly before firing

        if ready:
            print("\n>>> FIRING Cam Trigger pulse NOW -- watch channel 2 <<<")
            regs["Trigger Enable?"].write(True)
            regs["Set F.P. (T)"].write(True)
            time.sleep(ENABLE_HOLD_S)
            regs["Trigger Enable?"].write(False)
            regs["Set F.P. (T)"].write(True)
        else:
            print("Never became ready -- not firing.")

        fifo.stop()
        regs["AO Mode"].write(AO_MODE_SET_AO)
        regs["Static AO to set"].write(STATIC_ZERO)
        regs["Set F.P. (T)"].write(True)

    print("\nSession closed. Recap of what should have happened:")
    print("  1. X Galvo (ch1): steady ~1V for 5s")
    print("  2. X Galvo (ch1): dropped to ~0V when arming started")
    print("  3. Cam Trigger (ch2): one ~100us pulse, ~2s after the drop")
    print("Did you see that sequence?")


if __name__ == "__main__":
    main()
