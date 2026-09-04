"""'Simulate on FPGA' through the REAL GUI code path, headless. FPGA ONLY:
the camera is the SimulatedCamera fed from the FPGA's own trigger counter,
and every AO output is clamped to 0 by the FPGA's limit registers.

Runs a 10-slice Z stack, a 5 s Continuous Scan and another Z stack, in
SYNCREADOUT accounting by default (--edge for EDGE), and checks:
  * frames == slices for the stacks (FPGA fires slices + closing triggers)
  * continuous frames == triggers - closing (the simulated camera is exact)
  * 'AO Limit Max/Min (counts)' read back as 0 while armed
  * no stale/warm-up mis-discards for the simulated camera

    python -u spikes/26_gui_simulate_on_fpga_headless.py [--edge]
Nothing else may hold RIO0.
"""
import argparse
import faulthandler
import sys
import time

faulthandler.enable()

from PySide6.QtWidgets import QApplication  # noqa: E402
from unmscope.gui import main_window as mw  # noqa: E402
from unmscope.hardware.fpga_trigger import AO_LIMIT_CHANNELS  # noqa: E402

mw.QMessageBox.warning = lambda *a, **k: print(f"  [QMessageBox.warning] {a[1]!r}: {a[2]!r}", flush=True)


def pump(app, seconds, until=None):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.005)
    return False


def ao_limits_zero(w) -> bool:
    regs = w.fpga._session.registers
    mx = dict(regs["AO Limit Max (counts)"].read())
    mn = dict(regs["AO Limit Min (counts)"].read())
    return all(mx[c] == 0 and mn[c] == 0 for c in AO_LIMIT_CHANNELS)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edge", action="store_true")
    args = ap.parse_args()
    closing = 0 if args.edge else 1

    app = QApplication([])
    w = mw.MainWindow()
    w._log = lambda msg: print("   LOG:", msg, flush=True)
    w.sync_readout_chk.setChecked(not args.edge)
    print(f"mode: {'EDGE' if args.edge else 'SYNCREADOUT'}; expect {10 + closing} triggers per 10-slice stack")

    w.backend_combo.setCurrentText("Simulated")
    w.on_connect_clicked()
    assert w.camera is not None and not w.camera.reacts_to_dio4
    w.on_fpga_connect_clicked()
    assert w.fpga is not None, "FPGA connect failed"
    clamp_at_connect = ao_limits_zero(w)
    print(f"AO limits zero right after FPGA connect: {clamp_at_connect}")
    w.exposure_spin.setValue(100.0)
    pump(app, 0.2)
    for i, (chk, _wl, spin) in enumerate(w.excitation_rows):
        chk.setChecked(i == 0)
        spin.setValue(10 if i == 0 else 0)

    results = {}

    def zstack(label):
        print(f"\n=== {label} ===", flush=True)
        w.mode_combo.setCurrentText(mw.MODE_ZSTACK)
        w.z_start_spin.setValue(0.0); w.z_end_spin.setValue(9.0); w.z_interval_spin.setValue(1.0)
        pump(app, 0.2)
        w.on_acquire_clicked()
        assert w.acquiring
        pump(app, 0.3)
        clamped = ao_limits_zero(w)
        done = pump(app, 20.0, until=lambda: not w.acquiring)
        pump(app, 1.0)
        ok = done and w.frame_count == 10 and w._triggers_fired == 10 + closing and clamped and w._stale_discarded == 0
        print(f"{label}: finished={done} frames={w.frame_count} triggers={w._triggers_fired} "
              f"warmup_discarded={w._warmup_discarded} stale={w._stale_discarded} AO clamped while armed={clamped} -> {'OK' if ok else 'FAIL'}")
        results[label] = ok

    zstack("Z STACK #1")

    print("\n=== CONTINUOUS (5 s) ===", flush=True)
    w.mode_combo.setCurrentText(mw.MODE_CONTINUOUS)
    pump(app, 0.2)
    w.on_acquire_clicked()
    assert w.acquiring
    pump(app, 0.3)
    clamped = ao_limits_zero(w)
    pump(app, 5.0)
    w.on_acquire_clicked()
    pump(app, 1.0)
    trig = w._triggers_fired
    exp_frames = trig - closing                 # sync: warm-up discarded and last exposure never read out
    ok = (not w.acquiring) and trig >= 30 and w.frame_count == exp_frames and clamped
    print(f"CONTINUOUS: frames={w.frame_count} triggers={trig} expected_frames={exp_frames} "
          f"warmup_discarded={w._warmup_discarded} AO clamped while armed={clamped} -> {'OK' if ok else 'FAIL'}")
    results["CONTINUOUS"] = ok

    zstack("Z STACK #2")

    w.on_disconnect_clicked()
    w.on_fpga_disconnect_clicked()
    pump(app, 0.2)
    print("\nRESULTS:", results)
    verdict = all(results.values()) and clamp_at_connect
    print("VERDICT:", "PASS" if verdict else "FAIL")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
