"""The GUI's own scan waveform, observed on its own FPGA Scope. FPGA ONLY
("simulate on FPGA": Simulated camera, real FPGA, nothing connected).

Two Z stacks of 5 slices through the real GUI code path:
  1. Scope test clamp 500 mV: the scope's X Galvo column must show one
     sweep per trigger spanning the Scan Setup Range (100 um at 2000 um/V
     = +-25 mV), and the Z Piezo column must step by the Z Piezo Interval
     (0.5 um at 8 um/V = 62.5 mV) once per slice.
  2. Test clamp 0 (the default): the same acquisition must leave every AO
     column at exactly 0 -- nothing can move.

    python -u spikes/30_gui_waveform_on_scope_headless.py
"""
import faulthandler
import sys
import time

import numpy as np

faulthandler.enable()

from PySide6.QtWidgets import QApplication  # noqa: E402
from unmscope.gui import main_window as mw  # noqa: E402
from unmscope.hardware.fpga_scope import IDX_DIO4, digital_edges  # noqa: E402
from unmscope.hardware.waveform import counts_to_volts  # noqa: E402

mw.QMessageBox.warning = lambda *a, **k: print(f"  [QMessageBox.warning] {a[1]!r}: {a[2]!r}", flush=True)


def pump(app, seconds, until=None):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.005)
    return False


def analyse(snap, n_slices, period_s):
    frames, fs = snap.frames, snap.fs_hz
    rising, _ = digital_edges(frames[:, IDX_DIO4])
    if frames[0, IDX_DIO4] > 2048 and (len(rising) == 0 or rising[0] > 50):
        rising = np.concatenate(([0], rising))
    out = []
    win = int(period_s * fs) - 20
    for e in rising[:n_slices]:
        # The engine holds the PREVIOUS block's last point until this
        # block's first point lands (~30-60 us after the edge): skip it.
        seg = frames[e + 10: e + win]
        x = counts_to_volts(seg[:, 8])
        zp = counts_to_volts(seg[:, 10])
        out.append({"x_min_mv": x.min() * 1e3, "x_max_mv": x.max() * 1e3,
                    "x_changes": int((np.diff(seg[:, 8]) != 0).sum()),
                    "zp_mv": float(np.median(zp) * 1e3), "zp_spread": int(np.ptp(seg[:, 10]))})
    return rising, out


def main() -> int:
    app = QApplication([])
    w = mw.MainWindow()
    w._log = lambda msg: print("   LOG:", msg, flush=True)
    w.backend_combo.setCurrentText("Simulated")
    w.on_connect_clicked()
    w.on_fpga_connect_clicked()
    assert w.fpga is not None and w.scope is not None
    w.exposure_spin.setValue(100.0)
    for i, (chk, _wl, spin) in enumerate(w.excitation_rows):
        chk.setChecked(i == 0)
        spin.setValue(10 if i == 0 else 0)
    # Scan Setup: X range 100 um (-> +-25 mV), Z piezo 0..2 um in 0.5 um steps -> 5 slices (62.5 mV/step)
    w.xg_range.setValue(100.0)
    w.xg_offset.setValue(0.0)
    w.z_start_spin.setValue(0.0)
    w.z_end_spin.setValue(2.0)
    w.z_interval_spin.setValue(0.5)
    w.zg_start.setValue(0.0)
    w.zg_interval.setValue(0.0)
    w.mode_combo.setCurrentText(mw.MODE_ZSTACK)
    pump(app, 0.2)
    assert w.slice_count_field.text() == "5", w.slice_count_field.text()
    results = {}

    def run(label, clamp_mv, expect_motion):
        print(f"\n=== {label} ===", flush=True)
        w.scope_panel.test_clamp_spin.setValue(clamp_mv)
        w.scope.clear()
        pump(app, 0.2)
        w.on_acquire_clicked()
        assert w.acquiring
        period_s = w._trigger_period_s
        done = pump(app, 15.0, until=lambda: not w.acquiring)
        pump(app, 0.5)
        snap = w.scope.snapshot(seconds=4.0)
        rising, per = analyse(snap, 5, period_s)
        print(f"  finished={done} frames={w.frame_count} triggers={w._triggers_fired} "
              f"waveform points/trigger={w.last_waveform.points_per_trigger} edges={len(rising)}")
        ok = done and w.frame_count == 5 and len(per) == 5
        for k, r in enumerate(per):
            print(f"  slice {k + 1}: X {r['x_min_mv']:+.2f}..{r['x_max_mv']:+.2f} mV ({r['x_changes']} changes), "
                  f"Z piezo {r['zp_mv']:+.2f} mV (spread {r['zp_spread']} counts)")
        if expect_motion:
            for k, r in enumerate(per):
                ok &= abs(r["x_min_mv"] + 25.0) < 1.5 and abs(r["x_max_mv"] - 25.0) < 1.5 and r["x_changes"] > 100
                ok &= abs(r["zp_mv"] - 62.5 * k) < 1.5 and r["zp_spread"] == 0
        else:
            for r in per:
                ok &= r["x_min_mv"] == 0 and r["x_max_mv"] == 0 and r["x_changes"] == 0 and r["zp_mv"] == 0
        print(f"  -> {'OK' if ok else 'FAIL'}")
        results[label] = bool(ok)

    run("test clamp 500 mV: waveform visible", 500.0, True)
    run("clamp 0: nothing moves", 0.0, False)

    w.on_disconnect_clicked()
    w.on_fpga_disconnect_clicked()
    pump(app, 0.2)
    print("\nRESULTS:", results)
    print("VERDICT:", "PASS" if all(results.values()) else "FAIL")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
