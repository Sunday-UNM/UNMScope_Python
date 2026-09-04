"""Why does the Orca's exposure come back as 0 / 3 ms after a sequence?

Seen 2026-09-03 in the GUI: Z-stack (EXTERNAL, 100 ms, sequence, stop,
back to INTERNAL) then Continuous: set_exposure_ms(100) read back 3.021 ms
and the camera really ran at ~3 ms. Camera only, no FPGA, no triggers --
this just walks the state transitions and reads the exposure after each.

    python -u spikes/21_exposure_after_sequence.py
"""
import sys
import time

from unmscope.hardware.camera import OrcaFlash4Camera


def main() -> int:
    cam = OrcaFlash4Camera()
    cam.connect()
    mmc = cam._mmc

    def show(label):
        print(f"  [{label:<46}] Exposure={cam.get_exposure_ms():9.3f} ms  "
              f"prop Exposure={mmc.getProperty('Camera', 'Exposure')!s:>9}  "
              f"TRIGGER SOURCE={cam.get_property('TRIGGER SOURCE')}  "
              f"seq={mmc.isSequenceRunning()}", flush=True)

    show("after connect")
    cam.set_exposure_ms(100.0);                       show("set 100 (INTERNAL)")
    cam.set_trigger_source("EXTERNAL")
    cam.set_trigger_polarity("POSITIVE");             show("-> EXTERNAL/POSITIVE")
    cam.set_exposure_ms(100.0);                       show("set 100 (EXTERNAL)")
    cam.start_sequence(100000);                       show("start_sequence(100000)")
    time.sleep(0.5)
    n = 0
    while cam.remaining_image_count() > 0:
        cam.pop_image(); n += 1
    print(f"    ({n} frames popped while idle in EXTERNAL -- expect 0 or 1 stale)")
    cam.stop_sequence();                              show("stop_sequence()")
    cam.set_trigger_source("INTERNAL");               show("-> INTERNAL")
    # ---- this is the GUI's second-run order ----
    cam.set_trigger_source("EXTERNAL")
    cam.set_trigger_polarity("POSITIVE");             show("-> EXTERNAL/POSITIVE (2nd run)")
    cam.set_exposure_ms(100.0);                       show("set 100 (EXTERNAL, 2nd run)  <-- ?")
    # ---- alternatives ----
    cam.set_trigger_source("INTERNAL");               show("-> INTERNAL")
    cam.set_exposure_ms(100.0);                       show("set 100 (INTERNAL)")
    cam.set_trigger_source("EXTERNAL");               show("-> EXTERNAL again")
    cam.set_exposure_ms(100.0);                       show("set 100 (EXTERNAL) after INTERNAL set")
    mmc.setProperty("Camera", "Exposure", "100.0");   show("setProperty('Exposure','100.0')")
    cam.set_exposure_ms(50.0);                        show("set 50")
    cam.set_exposure_ms(100.0);                       show("set 100")
    time.sleep(0.2);                                  show("after 0.2 s")
    cam.start_sequence(100000)
    time.sleep(0.3)
    cam.stop_sequence();                              show("after a 2nd start/stop sequence")
    cam.set_exposure_ms(100.0);                       show("set 100 after 2nd sequence")

    cam.set_trigger_source("INTERNAL")
    cam.disconnect()
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
