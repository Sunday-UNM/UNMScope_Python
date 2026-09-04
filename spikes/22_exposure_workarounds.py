"""Find a workaround for the exposure being lost after stopSequenceAcquisition
(spikes/21_exposure_after_sequence.py): after a sequence stop the Orca's
real exposure drops to 0 -> 3.021 ms, the adapter's 'Exposure' property
still says 100, and further setExposure() calls are ignored.

Candidates, camera only, no FPGA:
  A. change exposure WHILE the sequence is running (never stop it)
  B. after stop: nudge via the property to a different value, then back
  C. after stop: INTERNAL + snapImage(), then set
  D. after stop: setProperty('TRIGGER SOURCE','INTERNAL') + setExposure via property with EXPOSURE FULL RANGE toggle
  E. disconnect() + connect()  (cost in seconds)

    python -u spikes/22_exposure_workarounds.py
"""
import sys
import time

from unmscope.hardware.camera import OrcaFlash4Camera


def main() -> int:
    cam = OrcaFlash4Camera()
    cam.connect()
    mmc = cam._mmc

    def exp():
        return cam.get_exposure_ms()

    def show(label):
        print(f"  [{label:<52}] getExposure={exp():8.3f}  prop={mmc.getProperty('Camera', 'Exposure')!s:>9}  "
              f"src={cam.get_property('TRIGGER SOURCE'):<8} seq={mmc.isSequenceRunning()}", flush=True)

    def flush():
        n = 0
        while cam.remaining_image_count() > 0:
            cam.pop_image(); n += 1
        return n

    cam.set_trigger_source("EXTERNAL"); cam.set_trigger_polarity("POSITIVE")
    cam.set_exposure_ms(100.0);                         show("EXTERNAL, set 100")

    print("\n--- A: change exposure while the sequence runs ---")
    cam.start_sequence(100000);                         show("start_sequence")
    time.sleep(0.3); flush()
    cam.set_exposure_ms(50.0);                          show("set 50 during sequence")
    cam.set_exposure_ms(100.0);                         show("set 100 during sequence")
    a_ok = abs(exp() - 100.0) < 1.0
    print(f"  A verdict: {'WORKS' if a_ok else 'no'}")

    print("\n--- stop the sequence (this is what breaks it) ---")
    cam.stop_sequence();                                show("stop_sequence")
    time.sleep(0.2);                                    show("0.2 s later")

    print("\n--- B: nudge via the property ---")
    mmc.setProperty("Camera", "Exposure", "99.0");      show("setProperty Exposure 99.0")
    mmc.setProperty("Camera", "Exposure", "100.0");     show("setProperty Exposure 100.0")
    b_ok = abs(exp() - 100.0) < 1.0
    print(f"  B verdict: {'WORKS' if b_ok else 'no'}")

    print("\n--- C: INTERNAL + snapImage(), then set ---")
    cam.set_trigger_source("INTERNAL");                 show("-> INTERNAL")
    try:
        mmc.setExposure(10.0)
        t0 = time.perf_counter(); mmc.snapImage(); _ = mmc.getImage()
        print(f"    snap took {(time.perf_counter()-t0)*1e3:.0f} ms (exposure 10 requested)")
    except Exception as e:
        print(f"    snap failed: {e}")
    show("after snap")
    cam.set_exposure_ms(100.0);                         show("set 100 after snap")
    c_ok = abs(exp() - 100.0) < 1.0
    print(f"  C verdict: {'WORKS' if c_ok else 'no'}")

    print("\n--- D: EXPOSURE FULL RANGE toggle ---")
    try:
        mmc.setProperty("Camera", "EXPOSURE FULL RANGE", "ENABLE");  show("FULL RANGE ENABLE")
        cam.set_exposure_ms(100.0);                                    show("set 100")
        mmc.setProperty("Camera", "EXPOSURE FULL RANGE", "DISABLE"); show("FULL RANGE DISABLE")
        cam.set_exposure_ms(100.0);                                    show("set 100")
    except Exception as e:
        print(f"    failed: {e}")
    d_ok = abs(exp() - 100.0) < 1.0
    print(f"  D verdict: {'WORKS' if d_ok else 'no'}")

    print("\n--- E: reconnect ---")
    t0 = time.perf_counter()
    cam.disconnect()
    cam.connect()
    dt = time.perf_counter() - t0
    mmc = cam._mmc
    show(f"after reconnect ({dt:.2f} s)")
    cam.set_exposure_ms(100.0);                         show("set 100 after reconnect")
    e_ok = abs(exp() - 100.0) < 1.0
    print(f"  E verdict: {'WORKS' if e_ok else 'no'}  (cost {dt:.2f} s)")

    # Does a second sequence + stop break it again after reconnect? (expected yes)
    cam.set_trigger_source("EXTERNAL"); cam.set_trigger_polarity("POSITIVE")
    cam.start_sequence(100000); time.sleep(0.3); flush(); cam.stop_sequence()
    show("after another start/stop")

    cam.set_trigger_source("INTERNAL")
    cam.disconnect()
    print(f"\nSUMMARY  A(change during run)={a_ok}  B(nudge)={b_ok}  C(snap)={c_ok}  D(fullrange)={d_ok}  E(reconnect)={e_ok}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
