"""Hunt for a short Z stack on the real Orca across the settings that matter.

Spike 36 found 51/51 at 100 ms full frame with the period = exposure, so the
user's "fewer images than Slices" does not reproduce there. This sweeps the
places it plausibly could: exposures at and under the 33.3 ms readout (where
the camera's floor decides the period), EDGE as well as SYNCREADOUT, the
201-slice size LouisXIV defaults to, and a 512x512 sub-array (faster
readout, tighter timing). Each case runs twice -- Custom Cycle Time OFF
(the fix: exposure x 1.27) and ON at the exposure (the old behaviour) -- so
a short count, if it appears, is attributed to the period and not the case.

Hardware: Orca + PCIe-7852R exclusively; galvos and Z piezo move; 488 at 0.1%.
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from unmscope.gui import main_window as mw  # noqa: E402

#            label                       sync   exposure_ms  slices  roi
CASES = [
    ("SYNC 100 ms, 201 slices (LouisXIV default size)", True,  100.0, 201, None),
    ("SYNC  30 ms, 51 (at the readout floor)",          True,   30.0,  51, None),
    ("SYNC  10 ms, 51 (well under readout)",            True,   10.0,  51, None),
    ("EDGE 100 ms, 51",                                 False, 100.0,  51, None),
    ("EDGE  30 ms, 51",                                 False,  30.0,  51, None),
    ("SYNC 100 ms, 51, ROI 512x512",                    True,  100.0,  51, 512),
]
OUT = Path(__file__).resolve().parent / "37_zstack_count_sweep_real_orca.result.txt"

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
    mw.QMessageBox.warning = staticmethod(lambda *a, **k: None)
    w.backend_combo.setCurrentText("Orca Flash 4.0 (real)")
    for attempt in range(1, 6):
        w.on_connect_clicked(); pump(0.5)
        if w.camera is not None and w.camera.is_connected:
            break
        time.sleep(3)
    else:
        print("FAILED to connect the camera:", logs[-1:]); return 1
    w.on_fpga_connect_clicked(); pump(0.5)
    if w.fpga is None:
        print("FAILED to connect the FPGA:", logs[-1:]); return 1
    print(f"camera {w.camera.info.name} {w.camera.info.serial}  readout {w.camera.readout_ms():.2f} ms")

    for i, (chk, wl, spin) in enumerate(w.excitation_rows):
        chk.setChecked(wl == 488); spin.setValue(0.1 if wl == 488 else 0)
    w.mode_combo.setCurrentText(mw.MODE_ZSTACK)
    panel = w.utilities_tab.waveform_panel
    cam_tab = getattr(w, "camera_tab", None)

    lines = [f"camera {w.camera.info.name} {w.camera.info.serial}, readout {w.camera.readout_ms():.2f} ms", ""]
    short = []
    for label, sync, exp_ms, slices, roi in CASES:
        w.sync_readout_chk.setChecked(sync)
        w.exposure_spin.setValue(exp_ms)
        w.z_start_spin.setValue(0.0); w.z_interval_spin.setValue(0.5)
        w.z_end_spin.setValue(0.5 * (slices - 1))
        if cam_tab is not None:
            if roi:
                cam_tab.on_preset(roi)
            else:
                cam_tab.on_use_all_pixels()
        pump(0.1)
        shown = w.slice_count_field.text()
        for variant, custom, cycle_s in (("fix: exposure x 1.27", False, 0.0),
                                         ("old: period = exposure", True, exp_ms / 1000.0)):
            panel.custom_cycle_time.setChecked(custom)
            if custom:
                panel.cycle_time_s.setValue(cycle_s)
            logs.clear()
            t0 = time.perf_counter()
            w.on_acquire_clicked()
            if not w.acquiring:
                row = f"{label} | {variant}: did not start -- {logs[-1] if logs else '?'}"
                print(row); lines.append(row); continue
            finished = pump(slices * 0.2 + 20, until=lambda: not w.acquiring)
            wall = time.perf_counter() - t0
            pump(0.4)
            stack = w.acquired_stack()
            n_stack = 0 if stack is None else int(stack.shape[0])
            per = wall / max(1, w._triggers_fired) * 1000.0
            floor_hit = any("below the camera's minimum" in m for m in logs)
            ok = (w.frame_count == w.z_target_frames == n_stack == slices)
            row = (f"{label} | {variant}\n"
                   f"    Slices field {shown}   period {w._trigger_period_s*1000:8.3f} ms"
                   f"{'  (raised to camera floor)' if floor_hit else ''}\n"
                   f"    triggers {w._triggers_fired:4d}  frames {w.frame_count:4d}/{w.z_target_frames}"
                   f"  stack {n_stack:4d}  warm-up {w._warmup_discarded}  "
                   f"observed {per:7.2f} ms/trigger  finished={finished}  "
                   f"{'OK' if ok else '*** SHORT ***'}")
            print(row); lines.append(row)
            if not ok:
                short.append(row)
            if w.acquiring:
                w.on_acquire_clicked(); pump(1.0)
            pump(1.0)
        lines.append("")

    if cam_tab is not None:
        cam_tab.on_use_all_pixels()
    w.on_disconnect_clicked(); w.on_fpga_disconnect_clicked(); pump(0.3)
    lines.append(f"short counts: {len(short)}")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nshort counts: {len(short)}   result -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
