"""Exercise the GUI's REAL camera-connect code path headlessly, in a
throwaway process -- see docs/known_issues.md.

spikes/18_probe_dcam_isolated.py opens the camera fine via bare
pymmcore-plus. This runs the actual MainWindow.on_connect_clicked() /
on_disconnect_clicked() handlers (Qt loaded, real Orca backend), twice,
then again with the FPGA connected first, to find out whether the
2026-09-03 dcamapi.dll access violation lives in the GUI process
(Qt + nifpga + DCAM together) rather than in DCAM itself.

USAGE:  python -u spikes/18b_gui_connect_path_isolated.py
Nothing else may hold the camera or RIO0.
"""
import faulthandler
import sys

faulthandler.enable()


def step(msg):
    print(f"[STEP] {msg}", flush=True)


step("import Qt + MainWindow")
from PySide6.QtWidgets import QApplication  # noqa: E402
from unmscope.gui import main_window as mw  # noqa: E402

# A failed connect pops a modal QMessageBox, which would hang a headless
# script inside a nested event loop. Print instead.
mw.QMessageBox.warning = lambda *a, **k: print(f"  [QMessageBox.warning] {a[1]!r}: {a[2]!r}", flush=True)

app = QApplication([])
w = mw.MainWindow()
w.backend_combo.setCurrentText("Orca Flash 4.0 (real)")
assert w.backend_combo.currentText().startswith("Orca"), w.backend_combo.currentText()

for i in (1, 2):
    step(f"camera on_connect_clicked() #{i}")
    w.on_connect_clicked()
    print(f"  camera = {w.camera.info if w.camera else None}", flush=True)
    print(f"  status = {w.status_label.text()!r}", flush=True)
    step(f"camera on_disconnect_clicked() #{i}")
    w.on_disconnect_clicked()

step("FPGA on_fpga_connect_clicked()  (nifpga + DCAM in one process)")
w.on_fpga_connect_clicked()
print(f"  fpga = {w.fpga}  status = {w.fpga_status_label.text()!r}", flush=True)
step("camera on_connect_clicked() with FPGA already connected")
w.on_connect_clicked()
print(f"  camera = {w.camera.info if w.camera else None}", flush=True)
step("camera on_disconnect_clicked()")
w.on_disconnect_clicked()
step("FPGA on_fpga_disconnect_clicked()")
w.on_fpga_disconnect_clicked()
print("[DONE] GUI connect path completed with no crash", flush=True)
