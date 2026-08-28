"""
Stage C -- repeating trigger pulse on DIO4, for oscilloscope hunting/
verification. Fires one trigger pulse per second, indefinitely, until you
Ctrl+C. Probe candidate terminals and look for a pulse blinking once per
second -- that's DIO4 ("Cam Ext Trigger Out DO").

No camera involved. Safe: this is a low-voltage digital pulse, not an
analog output.
"""
import time

import nifpga

BITFILE = r"H:\UNM_Lightsheet\UNMScope_Source\bin\data\SPIMFPGAProject_SPIM_MAIN_VI.lvbitx"
RESOURCE = "RIO0"

TRIGGER_UP_TICKS = 4_000_000  # ~100ms @ 40MHz -- wide and easy to see on a scope
CAM_TRIGGER_DELAY_TICKS = 0
PERIOD_S = 1.0


def main():
    print(f"Opening FPGA session ({RESOURCE})...")
    with nifpga.Session(bitfile=BITFILE, resource=RESOURCE) as session:
        print("Connected. SW Version:", session.registers["SW Version"].read())

        session.registers["# of triggers"].write(1)
        session.registers["Continuous Mode"].write(False)
        session.registers["Free run"].write(False)
        session.registers["Trigger up (ticks)"].write(TRIGGER_UP_TICKS)
        session.registers["Cam Trigger delay (ticks)"].write(CAM_TRIGGER_DELAY_TICKS)
        print(f"Trigger configured: 1 pulse, ~{TRIGGER_UP_TICKS/40e6*1000:.0f}ms wide.")

        print(f"\nFiring one pulse every {PERIOD_S:.1f}s on DIO4. Ctrl+C to stop.\n")
        n = 0
        try:
            while True:
                n += 1
                session.registers["Trigger Enable?"].write(True)
                time.sleep(0.15)
                session.registers["Trigger Enable?"].write(False)
                print(f"  [{n}] pulse fired at {time.strftime('%H:%M:%S')}")
                time.sleep(PERIOD_S - 0.15)
        except KeyboardInterrupt:
            print("\nStopped.")

    print("Session closed.")


if __name__ == "__main__":
    main()
