"""
Control test for 05_roundtrip_test.py: arm the camera for EXTERNAL trigger
and poll for up to 30s WITHOUT ever firing the FPGA trigger. If a frame
shows up anyway, the PASS result in 05_roundtrip_test.py is a false
positive (the camera is producing frames on its own, not because of a
real external trigger) and something else needs explaining before we
trust that test.
"""
import sys
import time

sys.path.insert(0, r"H:\UNM_Lightsheet\UNMScope_Python\src")

from unmscope.hardware.camera import OrcaFlash4Camera, CameraError

POLL_TIMEOUT_S = 30.0
POLL_INTERVAL_S = 0.05


def main():
    print("=== Control: EXTERNAL trigger armed, NO FPGA pulse fired ===\n")
    cam = OrcaFlash4Camera()
    try:
        cam.connect()
    except CameraError as e:
        print(f"Camera connect FAILED: {e}")
        return
    print("Camera connected:", cam.info)
    cam.set_exposure_ms(100.0)
    cam.set_trigger_source("EXTERNAL")
    cam.set_trigger_polarity("POSITIVE")
    print("Trigger mode:", cam.get_property("TRIGGER SOURCE"), "/", cam.get_property("TriggerPolarity"))

    print(f"\nArming and polling for {POLL_TIMEOUT_S:.0f}s with NO trigger fired...")
    cam.start_sequence(1)
    t0 = time.time()
    frame = None
    while time.time() - t0 < POLL_TIMEOUT_S:
        n = cam.remaining_image_count()
        if n > 0:
            frame = cam.pop_image()
            print(f"  Frame appeared at t+{time.time() - t0:.3f}s (UNEXPECTED -- no trigger was fired!)")
            break
        time.sleep(POLL_INTERVAL_S)
    cam.stop_sequence()

    print()
    if frame is None:
        print("RESULT: No frame arrived in", POLL_TIMEOUT_S, "s with no trigger fired.")
        print("This is the expected/good outcome -- means 05's PASS wasn't a false positive from this cause.")
    else:
        print("RESULT: A frame arrived WITHOUT any trigger being fired.")
        print(f"  shape={frame.shape} min={frame.min()} max={frame.max()} mean={frame.mean():.1f}")
        print("  --> 05_roundtrip_test.py's PASS is NOT reliable evidence of real external triggering.")

    try:
        cam.set_trigger_source("INTERNAL")
    except Exception:
        pass
    cam.disconnect()
    print("\nCamera disconnected.")


if __name__ == "__main__":
    main()
