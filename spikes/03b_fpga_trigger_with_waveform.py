"""
Stage C retry -- fire a real trigger pulse the way LabVIEW actually does it:
start the AO waveform engine FIRST, wait for it to report ready, THEN
enable the trigger. Confirmed via the real LabVIEW app + oscilloscope that
this sequence produces a real pulse; our earlier "just toggle Trigger
Enable?" attempt did not (AO Mode was left in static "Set AO" the whole
time).

Sequence, matching FPGA code/Host to FPGA/Front panel/
HHMI - Start FPGA device waveform.vi:
  1. Configure trigger settings (# of triggers, Continuous Mode, Free run,
     Trigger up (ticks), Cam Trigger delay (ticks)).
  2. Push waveform samples into the "Wvfrm2" DMA FIFO -- filled with
     ALL-ZERO words. Safe regardless of the exact channel interleave
     order/count (which may differ from the source block diagrams, same
     as the Static AO cluster did) -- zero data means zero volts on every
     channel no matter how it's unpacked on the FPGA side.
  3. AO Mode = "Start/Run Wvfrm" (0), Set F.P. (T) = True.
  4. Poll "AO wvfrm ready" for up to 3000ms (matching LabVIEW's own wait).
  5. Trigger Enable? = True -- this is where the real pulse should fire.
  6. Trigger Enable? = False (disarm).

Repeats once per second so it's easy to find/confirm on the oscilloscope,
same as 03_fpga_trigger_test.py. Ctrl+C to stop.
"""
import time

import nifpga

BITFILE = r"H:\UNM_Lightsheet\UNMScope_Source\bin\data\SPIMFPGAProject_SPIM_MAIN_VI.lvbitx"
RESOURCE = "RIO0"

AO_MODE_START_RUN_WVFRM = 0
AO_MODE_SET_AO = 2

TRIGGER_UP_TICKS = 4_000_000  # ~100ms @ 40MHz, easy to see on a scope
CAM_TRIGGER_DELAY_TICKS = 0
AO_TICKS_BETWEEN_POINTS = 4000  # ~100us @ 40MHz
AO_POINTS_PER_TRIGGER = 1

# Generous zero-fill: covers up to 12 possible channels x 4 samples, safely
# more than needed. All zero -> zero volts regardless of real channel count/order.
ZERO_WVFRM_WORDS = [0] * 48

WAIT_READY_TIMEOUT_S = 3.0
PERIOD_S = 1.0


def main():
    print(f"Opening FPGA session ({RESOURCE})...")
    with nifpga.Session(bitfile=BITFILE, resource=RESOURCE) as session:
        print("Connected. SW Version:", session.registers["SW Version"].read())

        # Safety first: confirm/force static-zero state before touching the
        # waveform engine at all.
        session.registers["AO Mode"].write(AO_MODE_SET_AO)
        zero_static = {
            "X Galvo": 0, "Z Galvo": 0, "Z Piezo": 0,
            "Dither Galvo": 0, "Tiling": 0, "Filter": 0, "AOTF on?": False,
        }
        session.registers["Static AO to set"].write(zero_static)
        session.registers["Set F.P. (T)"].write(True)
        print("Confirmed safe (all-zero) static state before starting.")

        # Full register bundle, matching FPGA code/Host to FPGA/Front panel/
        # HHMI - Set all FPGA devices with waveform config information.vi
        # EXACTLY (previous attempt only set a subset of these -- in
        # particular never set Cycle(ticks)/AO Trigger delay(ticks)/
        # AI settings/Shutter Ticks/Chnl+Stack+TP Trigger #s, which per the
        # FPGA Main VI's internal globals look like what actually drives
        # the trigger-generator state machine).
        session.registers["Cam Trigger delay (ticks)"].write(CAM_TRIGGER_DELAY_TICKS)
        session.registers["# of triggers"].write(1)
        session.registers["Continuous Mode"].write(False)
        session.registers["Cycle(Ticks)"].write(TRIGGER_UP_TICKS)
        session.registers["Trigger up (ticks)"].write(TRIGGER_UP_TICKS)
        session.registers["AO Trigger delay (ticks)"].write(0)
        session.registers["Free run"].write(False)
        session.registers["AI # of channels"].write(0)
        session.registers["AI loop period (ticks)"].write(AO_TICKS_BETWEEN_POINTS)
        session.registers["AO # of points per trigger"].write(AO_POINTS_PER_TRIGGER)
        session.registers["AO ticks between points"].write(AO_TICKS_BETWEEN_POINTS)
        session.registers['Shutter Ticks "on" (Ticks)'].write(0)
        # NOTE: the diagram's "Chnl/Stack/TP Trigger #s" are NOT flat
        # registers -- they're the real clusters "Trigger #s", "Trigger
        # stack #s", "Trigger blast #s", each shaped {'# on': int, '# off':
        # int} (confirmed by reading them back: all defaulted to 0/0).
        # Zero "on" count is almost certainly why nothing fired before.
        on_off_one = {"# on": 1, "# off": 0}
        session.registers["Trigger #s"].write(on_off_one)
        session.registers["Trigger stack #s"].write(on_off_one)
        session.registers["Trigger blast #s"].write(on_off_one)
        session.registers["Set F.P. (T)"].write(True)
        print("Full trigger+AO+AI register bundle written (matching LabVIEW's combined write).")

        # Belt-and-suspenders: the separate stack-delay write LabVIEW also
        # does (HHMI - Set FPGA Trigger stack delay.vi).
        session.registers["Trigger #s"].write(on_off_one)
        session.registers["Trigger stack #s"].write(on_off_one)
        session.registers["Trigger blast #s"].write(on_off_one)
        session.registers["Set F.P. (T)"].write(True)

        fifo = session.fifos["Wvfrm2"]
        fifo.stop()
        fifo.start()
        fifo.write(ZERO_WVFRM_WORDS, timeout_ms=2000)
        print(f"Wrote {len(ZERO_WVFRM_WORDS)} all-zero words to Wvfrm2 FIFO.")

        print("\nSetting AO Mode = 'Start/Run Wvfrm'...")
        session.registers["AO Mode"].write(AO_MODE_START_RUN_WVFRM)
        session.registers["Set F.P. (T)"].write(True)

        t0 = time.time()
        ready = False
        while time.time() - t0 < WAIT_READY_TIMEOUT_S:
            ready = session.registers["AO wvfrm ready"].read()
            if ready:
                break
            time.sleep(0.02)
        print(f"AO wvfrm ready = {ready} (waited {time.time() - t0:.2f}s)")

        if not ready:
            print("\nAO waveform engine never reported ready -- not firing triggers.")
            print("(Check FIFO word count/format -- may need adjusting.)")
            return

        print(f"\nFiring one trigger pulse every {PERIOD_S:.1f}s. Ctrl+C to stop.\n")
        n = 0
        try:
            while True:
                n += 1
                session.registers["Trigger Enable?"].write(True)
                session.registers["Set F.P. (T)"].write(True)
                # Kept deliberately short (< Cycle(ticks) ~100ms) -- holding
                # Enable across a full cycle boundary produced a SECOND
                # pulse in testing. Just needs to be a real edge, not a
                # sustained hold -- the hardware times the actual pulse
                # width via Trigger up (ticks), not this host-side hold.
                time.sleep(0.01)
                session.registers["Trigger Enable?"].write(False)
                session.registers["Set F.P. (T)"].write(True)
                print(f"  [{n}] pulse fired at {time.strftime('%H:%M:%S')}")
                time.sleep(PERIOD_S - 0.15)
        except KeyboardInterrupt:
            print("\nStopped.")
        finally:
            # Return to safe static state before closing.
            session.registers["AO Mode"].write(AO_MODE_SET_AO)
            session.registers["Static AO to set"].write(zero_static)
            session.registers["Set F.P. (T)"].write(True)
            fifo.stop()
            print("Returned to safe (all-zero) static state.")

    print("Session closed.")


if __name__ == "__main__":
    main()
