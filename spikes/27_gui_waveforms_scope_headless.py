"""Waveforms tab (FPGA Scope) through the real GUI, headless, FPGA ONLY.

Simulated camera + real FPGA ("simulate on FPGA"). Connecting the FPGA
starts the scope; a 3 s Continuous Scan puts a 10 Hz trigger train on
DIO4; the Waveforms tab must show it. The panel is rendered to a PNG so
the look can be checked by eye, and the numbers are asserted.

    python -u spikes/27_gui_waveforms_scope_headless.py [--png path]
"""
import argparse
import faulthandler
import sys
import time

faulthandler.enable()

from PySide6.QtWidgets import QApplication  # noqa: E402
from unmscope.gui import main_window as mw  # noqa: E402
from unmscope.hardware.fpga_scope import IDX_DIO4, measure_period  # noqa: E402

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
    ap = argparse.ArgumentParser()
    ap.add_argument("--png", default="docs/waveforms_tab_fpga_scope.png")
    args = ap.parse_args()

    app = QApplication([])
    w = mw.MainWindow()
    w._log = lambda msg: print("   LOG:", msg, flush=True)
    w.resize(1381, 898)
    w.backend_combo.setCurrentText("Simulated")
    w.on_connect_clicked()
    w.on_fpga_connect_clicked()
    assert w.fpga is not None and w.scope is not None and w.scope.running, "scope did not start"
    pump(app, 0.5)
    print(f"scope: {w.scope.channels} ch @ {w.scope.fs_hz:,.0f} S/s, frames so far {w.scope.ring.total:,}")

    w.exposure_spin.setValue(100.0)
    for i, (chk, _wl, spin) in enumerate(w.excitation_rows):
        chk.setChecked(i == 0)
        spin.setValue(10 if i == 0 else 0)
    w.mode_combo.setCurrentText(mw.MODE_CONTINUOUS)
    w.scope_panel.window_spin.setValue(1.0)
    pump(app, 0.2)
    w.on_acquire_clicked()
    assert w.acquiring
    pump(app, 3.0)
    snap = w.scope.snapshot(seconds=1.0)
    st = measure_period(snap.frames[:, IDX_DIO4], w.scope.fs_hz)
    print(f"DIO4 in the last 1 s window: {st}")
    status = w.scope_panel.status_label.text()
    print("panel status:", status)
    # render the Waveforms tab as the user would see it
    w.scope_panel.resize(975, 470)
    pix = w.scope_panel.grab()
    ok_png = pix.save(args.png)
    print(f"panel rendered to {args.png}: {ok_png} ({pix.width()}x{pix.height()})")
    w.on_acquire_clicked()
    pump(app, 1.0)
    frames_streamed = w.scope.ring.total
    w.on_disconnect_clicked()
    w.on_fpga_disconnect_clicked()
    pump(app, 0.2)
    ok = (st is not None and st.edges >= 8 and abs(st.period_ms - 100.0) < 0.1 and abs(st.high_ms - 0.1) < 0.02
          and frames_streamed > 100_000 and "DIO4:" in status and ok_png and w.scope is None)
    print(f"frames streamed {frames_streamed:,}; scope stopped at disconnect: {w.scope is None}")
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
