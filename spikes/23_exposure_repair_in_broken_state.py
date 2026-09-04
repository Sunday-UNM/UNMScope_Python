"""Reproduce the post-stopSequenceAcquisition exposure loss deliberately
(sequence with NO exposure writes during it, ~0.5 s, then stop -- the
pattern that broke in spikes 21/22 and in the GUI), then test repairs IN
the broken state:

  B. nudge: setProperty('Exposure', x-1) then setProperty('Exposure', x)
  C. TRIGGER SOURCE INTERNAL + snapImage(), then setExposure
  E. disconnect()+connect()

Each attempt is re-broken first so the repairs are tested independently.
Camera only, no FPGA.

    python -u spikes/23_exposure_repair_in_broken_state.py
"""
import sys
import time

from unmscope.hardware.camera import OrcaFlash4Camera


def main() -> int:
    cam = OrcaFlash4Camera()
    cam.connect()

    def exp():
        return cam.get_exposure_ms()

    def show(label):
        mmc = cam._mmc
        print(f"  [{label:<40}] getExposure={exp():8.3f}  prop={mmc.getProperty('Camera', 'Exposure')!s:>9}  "
              f"src={cam.get_property('TRIGGER SOURCE'):<8} seq={mmc.isSequenceRunning()}", flush=True)

    def flush():
        n = 0
        while cam.remaining_image_count() > 0:
            cam.pop_image(); n += 1
        return n

    def break_it(tries=5) -> bool:
        """Return True once getExposure() has diverged from the 100 ms set."""
        for i in range(tries):
            cam.set_trigger_source("EXTERNAL"); cam.set_trigger_polarity("POSITIVE")
            cam.set_exposure_ms(100.0)
            cam.start_sequence(100000)
            time.sleep(0.5)
            flush()
            cam.stop_sequence()
            time.sleep(0.1)
            e = exp()
            print(f"    break attempt {i+1}: getExposure after stop = {e:.3f}", flush=True)
            if abs(e - 100.0) > 1.0:
                return True
        return False

    results = {}

    print("--- B: nudge via property ---")
    if break_it():
        show("broken")
        mmc = cam._mmc
        mmc.setProperty("Camera", "Exposure", "99.0");  show("setProperty 99.0")
        mmc.setProperty("Camera", "Exposure", "100.0"); show("setProperty 100.0")
        results["B nudge"] = abs(exp() - 100.0) < 1.0
        if not results["B nudge"]:
            cam.set_exposure_ms(50.0); show("setExposure 50")
            cam.set_exposure_ms(100.0); show("setExposure 100")
            results["B nudge (setExposure 50->100)"] = abs(exp() - 100.0) < 1.0
    else:
        print("  could not reproduce the broken state this time")

    print("\n--- C: INTERNAL + snap ---")
    if break_it():
        show("broken")
        cam.set_trigger_source("INTERNAL"); show("-> INTERNAL")
        try:
            cam._mmc.setExposure(10.0)
            cam._mmc.snapImage(); cam._mmc.getImage()
        except Exception as e:
            print(f"    snap failed: {e}")
        show("after snap @10")
        cam.set_exposure_ms(100.0); show("set 100")
        results["C snap"] = abs(exp() - 100.0) < 1.0
    else:
        print("  could not reproduce the broken state this time")

    print("\n--- E: reconnect ---")
    if break_it():
        show("broken")
        t0 = time.perf_counter(); cam.disconnect(); cam.connect(); dt = time.perf_counter() - t0
        cam.set_exposure_ms(100.0); show(f"reconnect ({dt:.2f}s) + set 100")
        results["E reconnect"] = abs(exp() - 100.0) < 1.0
    else:
        print("  could not reproduce the broken state this time")

    try:
        cam.set_trigger_source("INTERNAL")
    except Exception:
        pass
    cam.disconnect()
    print("\nSUMMARY:", results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
