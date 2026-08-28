"""
Stage E -- full round trip: prove the camera is actually being triggered
by the FPGA's external trigger line (DIO4 = "Cam Ext Trigger Out DO"),
not just running on its own internal/software trigger.

How it works: put the camera in EXTERNAL/EDGE/POSITIVE trigger mode (see
docs/fpga_io_map.md), arm it with a non-blocking sequence acquisition
(startSequenceAcquisition), then -- still on the main thread, nothing
concurrent -- open the FPGA session and fire exactly one trigger pulse.
Poll (with sleeps) for the frame to land in MMCore's circular buffer. If
it does, promptly after the pulse, the external trigger link is proven.

NOTE: an earlier version of this script waited for the triggered frame by
calling snapImage() on a background thread while the FPGA call happened on
the main thread. That caused a real access-violation crash (VCRUNTIME140.dll,
0xc0000005) -- almost certainly the native DCAM/MMCore layer being touched
from two threads at once. This version is fully single-threaded.

IMPORTANT: close the UNMScope GUI (or at least Disconnect its camera) and
close the FPGA Live Panel before running this -- both the camera and the
FPGA session are exclusive resources; this script needs to hold both.
"""
import sys
import time

sys.path.insert(0, r"H:\UNM_Lightsheet\UNMScope_Python\src")

import nifpga
from unmscope.hardware.camera import OrcaFlash4Camera, CameraError

BITFILE = r"H:\UNM_Lightsheet\UNMScope_Source\bin\data\SPIMFPGAProject_SPIM_MAIN_VI.lvbitx"
RESOURCE = "RIO0"

EXPOSURE_MS = 100.0
TRIGGER_UP_TICKS = 400  # ~10us @ 40MHz
CAM_TRIGGER_DELAY_TICKS = 0
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

    print("Connecting FPGA and firing ONE trigger pulse on DIO4...")
    t_fire = None
    try:
        with nifpga.Session(bitfile=BITFILE, resource=RESOURCE) as session:
            session.registers["# of triggers"].write(1)
            session.registers["Continuous Mode"].write(False)
            session.registers["Free run"].write(False)
            session.registers["Trigger up (ticks)"].write(TRIGGER_UP_TICKS)
            session.registers["Cam Trigger delay (ticks)"].write(CAM_TRIGGER_DELAY_TICKS)
            t_fire = time.time()
            session.registers["Trigger Enable?"].write(True)
            time.sleep(0.05)
            session.registers["Trigger Enable?"].write(False)
            print(f"  Trigger fired at t+{t_fire - t_armed:.3f}s after arming.")
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
