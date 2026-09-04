"""SYNCREADOUT: when does the FIRST trigger of a run read out a frame?

Each trigger in SYNCREADOUT ends the running exposure (reads it out) and
starts the next one, so T triggers give T-1 proper frames -- plus ONE
extra "warm-up" frame if an exposure was already running when trigger 1
landed. In the GUI (spikes/20) a fresh sequence gave NO warm-up frame,
while every later run (previous run's last exposure still open) gave one.
The spikes/19 runs always saw one, but there the FPGA was reset AFTER the
camera was armed. This isolates the conditions. Camera + FPGA, sync
readout, 100 ms period, bounded T=5 triggers per run.

    python -u spikes/24_syncreadout_first_frame.py
"""
from __future__ import annotations

import sys
import time

from unmscope.hardware.camera import OrcaFlash4Camera
from unmscope.hardware.fpga_trigger import FpgaTriggerController

PERIOD = 0.100
T = 5


def run_once(cam, ctrl, label) -> int:
    """One bounded free run of T triggers; returns frames received."""
    times = []
    t0_holder = {}

    def drain():
        while cam.remaining_image_count() > 0:
            cam.pop_image()
            times.append(time.perf_counter())

    pre = 0
    while cam.remaining_image_count() > 0:
        cam.pop_image(); pre += 1
    ok = ctrl.start_free_run(PERIOD, PERIOD - 0.001, n_triggers=T)
    assert ok, ctrl.last_error
    t0 = ctrl._free_run_t0
    deadline = time.perf_counter() + T * PERIOD + 1.0
    while time.perf_counter() < deadline:
        drain()
        time.sleep(0.002)
    final = ctrl.stop_free_run()
    time.sleep(0.4)
    drain()
    rel = [(t - t0) * 1e3 for t in times]
    print(f"  [{label:<58}] triggers={final} frames={len(times)} "
          f"(pre-run leftovers flushed: {pre})  first arrivals ms: {[round(x) for x in rel[:3]]}", flush=True)
    return len(times)


def fresh_camera(sync=True):
    cam = OrcaFlash4Camera()
    cam.connect()
    cam.set_exposure_ms(PERIOD * 1e3)
    cam.set_trigger_source("EXTERNAL")
    cam.set_trigger_polarity("POSITIVE")
    cam.set_trigger_active(cam.TRIGGER_SYNCREADOUT if sync else cam.TRIGGER_EDGE)
    return cam


def release(cam):
    try:
        cam.stop_sequence()
        cam.set_trigger_active(cam.TRIGGER_EDGE)
        cam.set_trigger_source("INTERNAL")
    except Exception:
        pass
    cam.disconnect()


def main() -> int:
    results = {}

    print("E1/E2: FPGA connected BEFORE the camera sequence starts; vary the wait before arming")
    ctrl = FpgaTriggerController(); ctrl.connect()
    for wait in (0.05, 0.3, 1.0):
        cam = fresh_camera()
        cam.start_sequence(None)
        time.sleep(wait)
        results[f"fresh sequence, wait {wait}s, FPGA already up"] = run_once(cam, ctrl, f"E fresh seq, wait {wait:.2f}s (FPGA up first)")
        release(cam)
        time.sleep(0.3)
    ctrl.close()

    print("\nE3: spikes/19 order -- camera armed FIRST, then FPGA connect (reset+run), wait 1 s, arm")
    cam = fresh_camera()
    cam.start_sequence(None)
    ctrl = FpgaTriggerController(); ctrl.connect()
    time.sleep(1.0)
    results["FPGA reset AFTER camera armed"] = run_once(cam, ctrl, "E3 camera armed, then FPGA reset+run, wait 1 s")

    print("\nE4: second run in the same sequence (previous run's last exposure still open)")
    time.sleep(0.5)
    results["second run, sequence kept running"] = run_once(cam, ctrl, "E4 second run, same sequence")

    print("\nE5: toggle TRIGGER ACTIVE EDGE -> SYNCREADOUT between runs (does it clear the open exposure?)")
    # NOTE: DCAM refuses to change TRIGGER ACTIVE while capturing; the Orca
    # backend now stops the sequence itself in that case, so restart it.
    cam.set_trigger_active(cam.TRIGGER_EDGE)
    time.sleep(0.2)
    cam.set_trigger_active(cam.TRIGGER_SYNCREADOUT)
    if not cam.is_sequence_running():
        cam.start_sequence(None)
    time.sleep(0.5)
    results["after EDGE->SYNC toggle (sequence restarted)"] = run_once(cam, ctrl, "E5 after EDGE->SYNCREADOUT toggle")

    print("\nE6: end the open exposure with ONE single pulse after the run, then next run")
    fired = ctrl.fire_single_trigger()
    time.sleep(0.4)
    n = 0
    while cam.remaining_image_count() > 0:
        cam.pop_image(); n += 1
    print(f"  single pulse fired={fired}: {n} frame(s) came out of the open exposure")
    time.sleep(0.3)
    results["after a closing single pulse"] = run_once(cam, ctrl, "E6 run after the closing pulse")

    print("\nE7: frame metadata keys (for a per-frame discriminator)")
    try:
        ok = ctrl.start_free_run(PERIOD, PERIOD - 0.001, n_triggers=3)
        time.sleep(0.8)
        ctrl.stop_free_run()
        time.sleep(0.3)
        mmc = cam._mmc
        while cam.remaining_image_count() > 0:
            img, md = mmc.popNextImageAndMD()
            print(f"  md type={type(md).__name__}", end="")
            try:
                keys = list(md.Keys())
                print(f"  keys={keys}")
                for k in keys:
                    if "Time" in k or "Number" in k or "Hamamatsu" in k or "Frame" in k:
                        print(f"     {k} = {md.GetSingleTag(k).GetValue()}")
            except Exception as e:
                try:
                    print(f"  dict-like: {dict(md)}")
                except Exception:
                    print(f"  (could not read metadata: {e})")
            break
    except Exception as e:
        print(f"  metadata probe failed: {e}")

    release(cam)
    ctrl.close()
    print("\nSUMMARY (T=5 triggers each; 5 frames = warm-up frame present, 4 = none):")
    for k, v in results.items():
        print(f"  {v} frames  <- {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
