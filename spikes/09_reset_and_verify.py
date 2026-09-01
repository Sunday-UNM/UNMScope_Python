"""
Recovery: the FPGA's AO waveform engine got stuck (AO DMA Error/Buffer
Underflow latched, AO wvfrm ready stopped going True at all) after the
multi-trigger burst experiments in 07. session.reset() + session.run()
mirror LabVIEW's own init sequence (Reset FPGA / Run FPGA). After
resetting, re-run the EXACT known-working single-pulse sequence (matching
03b_fpga_trigger_with_waveform.py) to confirm the FPGA is healthy again
before continuing.
"""
import time

import nifpga

BITFILE = r"H:\UNM_Lightsheet\UNMScope_Source\bin\data\SPIMFPGAProject_SPIM_MAIN_VI.lvbitx"
RESOURCE = "RIO0"
AO_MODE_START_RUN_WVFRM = 0
AO_MODE_SET_AO = 2


def main():
    print(f"Opening FPGA session ({RESOURCE})...")
    with nifpga.Session(bitfile=BITFILE, resource=RESOURCE) as session:
        print("Connected. SW Version:", session.registers["SW Version"].read())

        print("Resetting FPGA (session.reset())...")
        session.reset()
        time.sleep(0.5)
        print("Running FPGA (session.run())...")
        session.run()
        time.sleep(0.5)
        print("SW Version after reset+run:", session.registers["SW Version"].read())
        print("AO DMA Error after reset:", session.registers["AO DMA Error"].read())
        print("AO wvfrm ready after reset:", session.registers["AO wvfrm ready"].read())

        # Safe state.
        session.registers["AO Mode"].write(AO_MODE_SET_AO)
        zero_static = {
            "X Galvo": 0, "Z Galvo": 0, "Z Piezo": 0,
            "Dither Galvo": 0, "Tiling": 0, "Filter": 0, "AOTF on?": False,
        }
        session.registers["Static AO to set"].write(zero_static)
        session.registers["Set F.P. (T)"].write(True)
        print("Safe state written.\n")

        # Now re-verify with the EXACT known-working single-pulse sequence.
        print("=== Re-verifying known-working single-pulse sequence ===")
        session.registers["Cam Trigger delay (ticks)"].write(0)
        session.registers["# of triggers"].write(1)
        session.registers["Continuous Mode"].write(False)
        TRIGGER_UP_TICKS = 4_000_000
        session.registers["Cycle(Ticks)"].write(TRIGGER_UP_TICKS)
        session.registers["Trigger up (ticks)"].write(TRIGGER_UP_TICKS)
        session.registers["AO Trigger delay (ticks)"].write(0)
        session.registers["Free run"].write(False)
        session.registers["AI # of channels"].write(0)
        session.registers["AI loop period (ticks)"].write(4000)
        session.registers["AO # of points per trigger"].write(1)
        session.registers["AO ticks between points"].write(4000)
        session.registers['Shutter Ticks "on" (Ticks)'].write(0)
        on_off_one = {"# on": 1, "# off": 0}
        session.registers["Trigger #s"].write(on_off_one)
        session.registers["Trigger stack #s"].write(on_off_one)
        session.registers["Trigger blast #s"].write(on_off_one)
        session.registers["Set F.P. (T)"].write(True)

        fifo = session.fifos["Wvfrm2"]
        fifo.stop()
        fifo.start()
        fifo.write([0] * 48, timeout_ms=2000)

        session.registers["AO Mode"].write(AO_MODE_START_RUN_WVFRM)
        session.registers["Set F.P. (T)"].write(True)
        t0 = time.time()
        ready = False
        while time.time() - t0 < 3.0:
            ready = session.registers["AO wvfrm ready"].read()
            if ready:
                break
            time.sleep(0.02)
        print(f"AO wvfrm ready = {ready} (waited {time.time()-t0:.2f}s)")
        if not ready:
            print("STILL BROKEN -- waveform engine won't arm even after reset.")
            return

        print(">>> Firing single validated pulse NOW -- watch the scope <<<")
        session.registers["Trigger Enable?"].write(True)
        session.registers["Set F.P. (T)"].write(True)
        time.sleep(0.01)
        session.registers["Trigger Enable?"].write(False)
        session.registers["Set F.P. (T)"].write(True)
        time.sleep(0.2)
        print("AO DMA Error after fire:", session.registers["AO DMA Error"].read())

        fifo.stop()
        session.registers["AO Mode"].write(AO_MODE_SET_AO)
        session.registers["Static AO to set"].write(zero_static)
        session.registers["Set F.P. (T)"].write(True)
        print("\nReturned to safe state. Did you see ONE clean pulse?")

    print("Session closed.")


if __name__ == "__main__":
    main()
