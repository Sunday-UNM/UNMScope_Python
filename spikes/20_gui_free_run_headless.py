"""Drive the REAL GUI code path headlessly on real hardware through the
new FPGA free-run acquisition, and check the counts.

MainWindow is built without .show(); Qt timers/signals are pumped with
processEvents(). Camera = Orca, FPGA = RIO0. Runs a 10-slice Z-stack
(expects exactly 10 triggers and 10 frames, auto-return to IDLE) and then
a 5 s Continuous Scan (expects frames to track the FPGA trigger count).

    python -u spikes/20_gui_free_run_headless.py
Nothing else may hold the camera or RIO0.
"""
import faulthandler
import sys
import time

faulthandler.enable()

from PySide6.QtWidgets import QApplication  # noqa: E402
from unmscope.gui import main_window as mw  # noqa: E402

mw.QMessageBox.warning = lambda *a, **k: print(f"  [QMessageBox.warning] {a[1]!r}: {a[2]!r}", flush=True)


def pump(app, seconds, until=None):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.005)
    return False


def main() -> int:
    app = QApplication([])
    w = mw.MainWindow()
    w._log = lambda msg: print("   LOG:", msg, flush=True)

    w.backend_combo.setCurrentText("Orca Flash 4.0 (real)")
    w.on_connect_clicked()
    assert w.camera is not None, "camera connect failed"
    w.on_fpga_connect_clicked()
    assert w.fpga is not None, "FPGA connect failed"
    w.exposure_spin.setValue(100.0)
    pump(app, 0.2)
    for i, (chk, _wl, spin) in enumerate(w.excitation_rows):
        chk.setChecked(i == 0)
        spin.setValue(10 if i == 0 else 0)

    # ---------------- Z stack of 10 ----------------
    print("\n=== Z STACK (10 slices) ===", flush=True)
    w.mode_combo.setCurrentText(mw.MODE_ZSTACK)
    w.z_start_spin.setValue(0.0)
    w.z_end_spin.setValue(9.0)
    w.z_interval_spin.setValue(1.0)
    pump(app, 0.2)
    print("slice count field:", w.slice_count_field.text(), flush=True)
    w.on_acquire_clicked()
    assert w.acquiring, "Z-stack did not start"
    done = pump(app, 20.0, until=lambda: not w.acquiring)
    pump(app, 0.3)
    print(f"Z-STACK RESULT: finished_on_its_own={done} frames={w.frame_count} "
          f"triggers={w._triggers_fired} stale_discarded={w._stale_discarded}", flush=True)
    zs_ok = done and w.frame_count == 10 and w._triggers_fired == 10
    if w.acquiring:
        w.on_acquire_clicked()
        pump(app, 0.5)

    # ---------------- Continuous 5 s ----------------
    print("\n=== CONTINUOUS (5 s) ===", flush=True)
    w.mode_combo.setCurrentText(mw.MODE_CONTINUOUS)
    pump(app, 0.2)
    w.on_acquire_clicked()
    assert w.acquiring, "continuous did not start"
    pump(app, 5.0)
    w.on_acquire_clicked()   # Stop
    pump(app, 1.0)           # > the stop grace period (2 periods + 200 ms)
    trig = w._triggers_fired   # the FPGA's final count, read at disarm
    print(f"CONTINUOUS RESULT: frames={w.frame_count} final_triggers={trig} "
          f"stale_discarded={w._stale_discarded} acquiring={w.acquiring}", flush=True)
    # With the stop grace period the in-flight frames are collected; the
    # very last trigger's frame may still be lost at disarm -> allow 1.
    cont_ok = (not w.acquiring) and trig >= 30 and 0 <= trig - w.frame_count <= 1

    # ---------------- Z stack again: repeatability with the sequence kept running ----------------
    print("\n=== Z STACK #2 (10 slices) ===", flush=True)
    w.mode_combo.setCurrentText(mw.MODE_ZSTACK)
    pump(app, 0.2)
    w.on_acquire_clicked()
    assert w.acquiring, "Z-stack #2 did not start"
    done2 = pump(app, 20.0, until=lambda: not w.acquiring)
    pump(app, 1.0)
    print(f"Z-STACK #2 RESULT: finished_on_its_own={done2} frames={w.frame_count} "
          f"triggers={w._triggers_fired} stale_discarded={w._stale_discarded}", flush=True)
    zs_ok = zs_ok and done2 and w.frame_count == 10 and w._triggers_fired == 10

    w.on_disconnect_clicked()
    w.on_fpga_disconnect_clicked()
    pump(app, 0.2)
    print(f"\nZ-stack {'PASS' if zs_ok else 'FAIL'}   Continuous {'PASS' if cont_ok else 'FAIL'}")
    print("VERDICT:", "PASS" if zs_ok and cont_ok else "FAIL")
    return 0 if zs_ok and cont_ok else 1


if __name__ == "__main__":
    sys.exit(main())
