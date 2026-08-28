"""
Stage E -- full round trip: prove the camera is actually being triggered
by the FPGA's external trigger line (DIO4 = "Cam Ext Trigger Out DO"),
not just running on its own internal/software trigger.

How it works: put the camera in EXTERNAL/EDGE/POSITIVE trigger mode (see
docs/fpga_io_map.md), arm it with a non-blocking sequence acquisition
(startSequenceAcquisition), then -- still on the main thread, nothing
concurrent -- open the FPGA session and fire exactly one trigger pulse
using the CORRECTED sequence (see spikes/03b_fpga_trigger_with_waveform.py
and docs/fpga_io_map.md -- a bare Trigger Enable? toggle does NOT produce
a real pulse; the AO waveform engine must be running and the Trigger #s/
Trigger stack #s/Trigger blast #s clusters must be nonzero). Poll (with
sleeps) for the frame to land in MMCore's circular buffer. If it does,
promptly after the pulse, the external trigger link is proven.

NOTE: an earlier version of this script waited for the triggered frame by
calling snapImage() on a background thread while the FPGA call happened on
the main thread. That caused a real access-violation crash (VCRUNTIME140.dll,
0xc0000005) -- almost certainly the native DCAM/MMCore layer being touched
from two threads at once. This version is fully single-threaded.

IMPORTANT: close the UNMScope GUI (or at least Disconnect its camera) and
close the FPGA Live Panel before running this -- both the camera and the
FPGA session are exclusive resources; this script needs to hold both.
The DIO4 -> camera ext trigger BNC must actually be connected for this to
pass.
"""
import sys
import time

sys.path.insert(0, r"H:\UNM_Lightsheet\UNMScope_Python\src")

import nifpga
from unmscope.hardware.camera import OrcaFlash4Camera, CameraError

BITFILE = r"H:\UNM_Lightsheet\UNMScope_Source\bin\data\SPIMFPGAProject_SPIM_MAIN_VI.lvbitx"
RESOURCE = "RIO0"

AO_MODE_START_RUN_WVFRM = 0
AO_MODE_SET_AO = 2

EXPOSURE_MS = 100.0
TRIGGER_UP_TICKS = 4_000_000  # ~100ms @ 40MHz -- matches the oscilloscope-verified pulse
CAM_TRIGGER_DELAY_TICKS = 0
AO_TICKS_BETWEEN_POINTS = 4000
AO_POINTS_PER_TRIGGER = 1
ZERO_WVFRM_WORDS = [0] * 48
WAIT_READY_TIMEOUT_S = 3.0
POLL_TIMEOUT_S = 30.0
POLL_INTERVAL_S = 0.05


def main():
    print("=== Stage E: FPGA -> camera external trigger round trip (single-threaded) ===\n")

    print("Connecting camera...")
    cam = OrcaFlash4Camera()
    try:
        cam.connect()
    except CameraError as e:
        print(f"Camera connect FAILED: {e}")
        print("(Is the UNMScope GUI still holding the camera? Disconnect it there first.)")
        return
    print("Camera connected:", cam.info)

    cam.set_exposure_ms(EXPOSURE_MS)
    print(f"Exposure set to {EXPOSURE_MS} ms")
    print("Current TRIGGER SOURCE:", cam.get_property("TRIGGER SOURCE"))
    print("Current TriggerPolarity:", cam.get_property("TriggerPolarity"))
    print("Setting TRIGGER SOURCE=EXTERNAL, TriggerPolarity=POSITIVE...")
    cam.set_trigger_source("EXTERNAL")
    cam.set_trigger_polarity("POSITIVE")
    print("Now:", cam.get_property("TRIGGER SOURCE"), "/", cam.get_property("TriggerPolarity"))

    print("\nArming camera (non-blocking, startSequenceAcquisition)...")
    cam.start_sequence(1)
    t_armed = time.time()

    print("Connecting FPGA and firing ONE trigger pulse on DIO4 (corrected sequence)...")
    t_fire = None
    try:
        with nifpga.Session(bitfile=BITFILE, resource=RESOURCE) as session:
            session.registers["AO Mode"].write(AO_MODE_SET_AO)
            zero_static = {
                "X Galvo": 0, "Z Galvo": 0, "Z Piezo": 0,
                "Dither Galvo": 0, "Tiling": 0, "Filter": 0, "AOTF on?": False,
            }
            session.registers["Static AO to set"].write(zero_static)
            session.registers["Set F.P. (T)"].write(True)

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
            on_off_one = {"# on": 1, "# off": 0}
            session.registers["Trigger #s"].write(on_off_one)
            session.registers["Trigger stack #s"].write(on_off_one)
            session.registers["Trigger blast #s"].write(on_off_one)
            session.registers["Set F.P. (T)"].write(True)

            fifo = session.fifos["Wvfrm2"]
            fifo.stop()
            fifo.start()
            fifo.write(ZERO_WVFRM_WORDS, timeout_ms=2000)

            session.registers["AO Mode"].write(AO_MODE_START_RUN_WVFRM)
            session.registers["Set F.P. (T)"].write(True)
            t0 = time.time()
            ready = False
            while time.time() - t0 < WAIT_READY_TIMEOUT_S:
                ready = session.registers["AO wvfrm ready"].read()
                if ready:
                    break
                time.sleep(0.02)
            print(f"  AO wvfrm ready = {ready} (waited {time.time() - t0:.2f}s)")
            if not ready:
                print("FPGA waveform engine never became ready -- not firing.")
                cam.stop_sequence()
                cam.disconnect()
                return

            t_fire = time.time()
            session.registers["Trigger Enable?"].write(True)
            session.registers["Set F.P. (T)"].write(True)
            time.sleep(0.01)  # keep short -- longer hold produced a 2nd pulse in testing
            session.registers["Trigger Enable?"].write(False)
            session.registers["Set F.P. (T)"].write(True)
            print(f"  Trigger fired at t+{t_fire - t_armed:.3f}s after arming.")

            fifo.stop()
            session.registers["AO Mode"].write(AO_MODE_SET_AO)
            session.registers["Static AO to set"].write(zero_static)
            session.registers["Set F.P. (T)"].write(True)
    except Exception as e:
        print(f"FPGA trigger FAILED: {e}")
        cam.stop_sequence()
        cam.disconnect()
        return

    print(f"\nPolling for the frame (up to {POLL_TIMEOUT_S:.0f}s)...")
    t_poll_start = time.time()
    frame = None
    while time.time() - t_poll_start < POLL_TIMEOUT_S:
        if cam.remaining_image_count() > 0:
            frame = cam.pop_image()
            break
        time.sleep(POLL_INTERVAL_S)
    t_received = time.time()

    cam.stop_sequence()

    print()
    if frame is None:
        print("RESULT: FAIL -- no frame arrived within the timeout.")
        print("Camera did not see a trigger. Check: DIO4 actually wired to the")
        print("camera's ext trigger input? polarity/level? cabling?")
    else:
        print("RESULT: PASS")
        print(f"  Frame received {t_received - t_armed:.3f}s after arming "
              f"(trigger fired at t+{t_fire - t_armed:.3f}s)")
        print(f"  shape={frame.shape} dtype={frame.dtype} "
              f"min={frame.min()} max={frame.max()} mean={frame.mean():.1f}")
        print("  --> Camera IS being triggered by the FPGA's external trigger line (DIO4).")

    try:
        cam.set_trigger_source("INTERNAL")
    except Exception:
        pass
    cam.disconnect()
    print("\nCamera disconnected.")


if __name__ == "__main__":
    main()
