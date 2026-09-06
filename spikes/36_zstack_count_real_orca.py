"""Does a real Z stack on the Orca deliver every slice? A/B on the cycle time.

The user's bug: fewer images than Slices in Z stack mode. Diagnosis
(docs/known_issues.md, "Z stack came up short"): the trigger period was the
exposure, leaving the X galvo no flyback time. Fix: the period is the Cycle
time, exposure + flyback, editable behind Custom Cycle Time.

This runs the SAME stack twice on the real camera and real FPGA:

    A  Custom Cycle Time 0.100 s  -> the old behaviour (period = exposure)
    B  Custom Cycle Time 0.127 s  -> the user's LouisXIV value (27 ms flyback)

and counts what comes back. Frames vs slices is the number that matters;
the observed period and warm-up count are recorded so a discrepancy can be
attributed rather than guessed at.

Hardware: takes the Orca and the PCIe-7852R exclusively -- LouisXIV must be
closed. Galvos and the Z piezo move. The 488 row is set to its 0.1% floor.
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from unmscope.gui import main_window as mw  # noqa: E402

EXPOSURE_MS = 100.0
SLICES = 51                       # 0..25 um at 0.5 um -> 51 slices, ~6.5 s at 127 ms
RUNS = [("A  period = exposure (old behaviour)", 0.100),
        ("B  period = 0.127 s (the fix)", 0.127)]
OUT = Path(__file__).resolve().parent / "36_zstack_count_real_orca.result.txt"

app = QApplication.instance() or QApplication([])


def pump(seconds, until=None):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.002)
    return False


def main() -> int:
    w = mw.MainWindow()
    logs: list[str] = []
    w._log = logs.append
    mw.QMessageBox.warning = staticmethod(lambda *a, **k: None)   # no modal dialogs headless

    # -- connect the REAL camera and REAL FPGA (DCAM can be slow to release) ---
    w.backend_combo.setCurrentText("Orca Flash 4.0 (real)")
    for attempt in range(1, 6):
        w.on_connect_clicked()
        pump(0.5)
        if w.camera is not None and w.camera.is_connected:
            break
        print(f"camera connect attempt {attempt} failed: {logs[-1] if logs else '?'}")
        time.sleep(3)
    else:
        print("FAILED to connect the camera"); return 1
    w.on_fpga_connect_clicked()
    pump(0.5)
    if w.fpga is None:
        print("FAILED to connect the FPGA:", logs[-1] if logs else "?"); return 1
    info = w.camera.info
    print(f"camera: {info.name} {info.serial} {info.width}x{info.height}   "
          f"readout {w.camera.readout_ms():.2f} ms   reacts_to_dio4={w.camera.reacts_to_dio4}")

    # -- the acquisition: SYNCREADOUT, 100 ms, 488 at its floor, Z stack -------
    w.exposure_spin.setValue(EXPOSURE_MS)
    w.sync_readout_chk.setChecked(True)
    for i, (chk, wl, spin) in enumerate(w.excitation_rows):
        chk.setChecked(wl == 488)
        spin.setValue(0.1 if wl == 488 else 0)      # 0.1%: LouisXIV's own floor, and > 0 for the one-laser gate
    w.mode_combo.setCurrentText(mw.MODE_ZSTACK)
    w.z_start_spin.setValue(0.0); w.z_end_spin.setValue(25.0); w.z_interval_spin.setValue(0.5)
    pump(0.1)
    shown = w.slice_count_field.text()
    print(f"Z stack 0..25 um at 0.5 um: Slices field = {shown} (expected {SLICES})")
    panel = w.utilities_tab.waveform_panel

    lines = [f"exposure {EXPOSURE_MS} ms, SYNCREADOUT, {SLICES} slices, camera {info.name} {info.serial}", ""]
    for label, cycle_s in RUNS:
        panel.custom_cycle_time.setChecked(True)
        panel.cycle_time_s.setValue(cycle_s)
        logs.clear()
        t0 = time.perf_counter()
        w.on_acquire_clicked()
        if not w.acquiring:
            print(f"{label}: did not start -- {logs[-1] if logs else '?'}"); continue
        finished = pump(SLICES * cycle_s * 3 + 15, until=lambda: not w.acquiring)
        wall = time.perf_counter() - t0
        pump(0.5)
        stack = w.acquired_stack()
        n_stack = 0 if stack is None else int(stack.shape[0])
        per = wall / max(1, w._triggers_fired) * 1000.0
        row = (f"{label}\n"
               f"    period set   {w._trigger_period_s*1000:8.3f} ms\n"
               f"    triggers     {w._triggers_fired:4d}   (slices + closing {w._run_closing})\n"
               f"    frames kept  {w.frame_count:4d} / {w.z_target_frames}   stack {n_stack}\n"
               f"    warm-up discarded {w._warmup_discarded}   stale discarded {getattr(w, '_stale_discarded', '?')}\n"
               f"    wall {wall:6.2f} s  -> {per:7.2f} ms per trigger observed\n"
               f"    finished cleanly: {finished}")
        notable = [m for m in logs if any(k in m for k in
                   ("minimum", "drop", "DROP", "missed", "Waveform", "FAILED", "Channel delays", "warm"))]
        if notable:
            row += "\n    log: " + "\n         ".join(n[:110] for n in notable[:6])
        print(row); lines.append(row); lines.append("")
        if w.acquiring:
            w.on_acquire_clicked(); pump(1.0)
        pump(1.5)

    w.on_disconnect_clicked(); w.on_fpga_disconnect_clicked(); pump(0.3)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nresult -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
