"""
Stage E -- full round trip: prove the camera is actually being triggered
by the FPGA's external trigger line (DIO4 = "Cam Ext Trigger Out DO"),
not just running on its own internal/software trigger.

How it works: put the camera in EXTERNAL/EDGE/POSITIVE trigger mode (see
docs/fpga_io_map.md), then call snapImage() in a background thread --
in that mode the DCAM/Hamamatsu adapter blocks waiting for a real trigger
pulse before returning a frame. Meanwhile the main thread opens the FPGA
session and fires exactly one trigger pulse (the same register sequence as
tools/fpga_live_panel.py's "Fire" button). If a real frame comes back
promptly after the pulse fires, the external trigger link is proven.

IMPORTANT: close the UNMScope GUI (or at least Disconnect its camera) and
close the FPGA Live Panel before running this -- both the camera and the
FPGA session are exclusive resources; this script needs to hold both.
"""
import sys
import threading
import time

sys.path.insert(0, r"H:\UNM_Lightsheet\UNMScope_Python\src")

import nifpga
from unmscope.hardware.camera import OrcaFlash4Camera, CameraError

BITFILE = r"H:\UNM_Lightsheet\UNMScope_Source\bin\data\SPIMFPGAProject_SPIM_MAIN_VI.lvbitx"
RESOURCE = "RIO0"
AO_MODE_SET_AO = 2

EXPOSURE_MS = 100.0  # generous, so we have time to fire the trigger by hand/script
TRIGGER_UP_TICKS = 400  # ~10us @ 40MHz
CAM_TRIGGER_DELAY_TICKS = 0
SNAP_TIMEOUT_S = 30.0


def main():
    print("=== Stage E: FPGA -> camera external trigger round trip ===\n")

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

    # Background thread: blocks in snapImage() until the external trigger arrives.
    result = {"frame": None, "error": None, "t_returned": None}

    def wait_for_triggered_frame():
        try:
            t0 = time.time()
            frame = cam.snap_waiting_for_trigger(timeout_s=SNAP_TIMEOUT_S)
            result["frame"] = frame
            result["t_returned"] = time.time() - t0
        except Exception as e:
            result["error"] = e

    print(f"\nArming camera (snapImage in background thread, will block up to {SNAP_TIMEOUT_S}s for the trigger)...")
    snap_thread = threading.Thread(target=wait_for_triggered_frame, daemon=True)
    t_armed = time.time()
    snap_thread.start()
    time.sleep(1.0)  # give the camera a moment to actually be waiting

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
        cam.disconnect()
        return

    print(f"\nWaiting for snap thread to complete (up to {SNAP_TIMEOUT_S}s)...")
    snap_thread.join(timeout=SNAP_TIMEOUT_S + 2)

    print()
    if result["error"] is not None:
        print(f"RESULT: FAIL -- snapImage() raised: {result['error']}")
        print("No frame arrived -- camera did not respond to the FPGA trigger")
        print("(check: DIO4 actually wired to the camera's ext trigger input? polarity/level? cabling?)")
    elif result["frame"] is None:
        print("RESULT: FAIL -- snap thread did not complete in time (camera still waiting).")
        print("No frame arrived within the timeout -- camera did not see a trigger.")
    else:
        frame = result["frame"]
        print("RESULT: PASS")
        print(f"  Frame received {result['t_returned']:.3f}s after arming "
              f"(trigger fired at t+{t_fire - t_armed:.3f}s)")
        print(f"  shape={frame.shape} dtype={frame.dtype} "
              f"min={frame.min()} max={frame.max()} mean={frame.mean():.1f}")
        print("  --> Camera IS being triggered by the FPGA's external trigger line (DIO4).")

    # Leave the camera on internal trigger afterward so it's not left waiting.
    try:
        cam.set_trigger_source("INTERNAL")
    except Exception:
        pass
    cam.disconnect()
    print("\nCamera disconnected.")


if __name__ == "__main__":
    main()
