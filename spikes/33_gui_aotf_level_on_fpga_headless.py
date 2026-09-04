"""The GUI's AOTF wiring on the REAL FPGA, headless. FPGA ONLY; the camera
is the Simulated one, patched to look like the Orca to the FPGA side
(reacts_to_dio4=True) so the run is NOT clamped and the level is written.
Test level: the 488 nm row at 1 % = 50 mV = 164 counts on AOTF ch 2 (AO7),
for about half a second. Galvo content is the GUI's own small sweep.

Checks, from the FPGA's own 'AOTF ch out (V)' readback:
  1. real-camera-style run: ch 2 = 164 during the run, every other 0
  2. after Stop: all 0
  3. simulate-on-FPGA run (unpatched camera): all 0 throughout

    python -u spikes/33_gui_aotf_level_on_fpga_headless.py
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
    w.backend_combo.setCurrentText("Simulated")
    w.on_connect_clicked()
    w.on_fpga_connect_clicked()
    assert w.fpga is not None and w.scope is not None
    regs = w.fpga._session.registers
    w.exposure_spin.setValue(100.0)
    for i, (chk, _wl, spin) in enumerate(w.excitation_rows):
        chk.setChecked(i == 2)                      # 488 nm row -> AOTF ch 2
        spin.setValue(1.0 if i == 2 else 0)         # 1 % = 50 mV
    w.xg_range.setValue(20.0)                       # +-5 mV X sweep, irrelevant here
    w.mode_combo.setCurrentText(mw.MODE_CONTINUOUS)
    pump(app, 0.2)
    results = {}

    def out():
        return {k: int(v) for k, v in regs["AOTF ch out (V)"].read().items()}

    print("\n=== 1. real-camera-style run (not clamped): level must reach the output ===")
    w.camera.reacts_to_dio4 = True
    w.on_acquire_clicked()
    assert w.acquiring
    pump(app, 0.5)
    during = out()
    print(f"  during run: AOTF ch out = {during}; levels written = {w.fpga.aotf_levels}; "
          f"gate allowed = {w.fpga.aotf_gate_allowed}")
    results["level on ch2 during run"] = during == {"AOTF ch 0": 0, "AOTF ch 1": 0, "AOTF ch 2": 164, "AOTF ch 3": 0}
    w.on_acquire_clicked()
    pump(app, 0.5)
    after = out()
    print(f"  after Stop: AOTF ch out = {after}; levels = {regs['AOTF ch (V)'].read()}")
    results["all 0 after stop"] = all(v == 0 for v in after.values())

    print("\n=== 2. simulate-on-FPGA run (clamped): AOTF must stay off ===")
    w.camera.reacts_to_dio4 = False
    w.on_acquire_clicked()
    assert w.acquiring
    pump(app, 0.5)
    sim = out()
    lim = regs["AO Limit Max (counts)"].read()["AOTF on?"]
    print(f"  during sim run: AOTF ch out = {sim}; AO Limit Max 'AOTF on?' = {lim}; clamp verified = {w.fpga.ao_clamped}")
    results["sim keeps AOTF off"] = all(v == 0 for v in sim.values()) and not lim and w.fpga.ao_clamped
    w.on_acquire_clicked()
    pump(app, 0.5)

    w.on_disconnect_clicked()
    w.on_fpga_disconnect_clicked()
    pump(app, 0.2)
    print("\nRESULTS:", results)
    print("VERDICT:", "PASS" if all(results.values()) else "FAIL")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
