"""
Live FPGA status/control panel for the UNMScope hardware-validation spike.

Opens ONE nifpga session and keeps it open for the life of this window --
polls and displays live register state, and provides safe, explicit
controls for the things we're validating stage by stage (static voltage
output, single trigger pulse), so we don't have to edit/rerun a separate
script and re-read terminal output for every little check.

Run with:
    C:\\Users\\Chakrabortylab\\AppData\\Local\\Programs\\Python\\Python311\\python.exe tools\\fpga_live_panel.py

See ../docs/fpga_io_map.md for the register/pinout reference this is built
from, and ../spikes/ for the one-shot validation scripts this complements
(don't run a spikes/*.py script at the same time as this panel -- the FPGA
session is exclusive, only one can hold it at once).
"""
import sys
import datetime

import nifpga
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QFormLayout,
    QLabel, QPushButton, QComboBox, QDoubleSpinBox, QSpinBox, QTextEdit,
    QMessageBox, QCheckBox,
)

BITFILE = r"H:\UNM_Lightsheet\UNMScope_Source\bin\data\SPIMFPGAProject_SPIM_MAIN_VI.lvbitx"
RESOURCE = "RIO0"

# From the bitfile's own StringList for the "AO Mode" register.
AO_MODE_NAMES = {0: "Start/Run Wvfrm", 1: "Stop Wvfrm", 2: "Set AO", 3: "Clear AO DMA"}
AO_MODE_SET_AO = 2

# Static AO cluster fields, verified against the actual deployed bitfile
# (see docs/fpga_io_map.md) -- NOT the same as the source block diagrams.
STATIC_AO_CHANNELS = ["X Galvo", "Z Galvo", "Z Piezo", "Dither Galvo", "Tiling", "Filter"]

# PCIe-7852R AO range assumption: +/-10V mapped linearly to +/-32767 counts
# (matches the "AO Limit Min/Max (counts)" = -32767/32767 registers).
# VERIFY THIS with the oscilloscope in Stage B -- if the measured voltage
# doesn't match, this scale factor is wrong and needs correcting here.
AO_VOLT_RANGE = 10.0
AO_COUNTS_FULL_SCALE = 32767


def volts_to_counts(v: float) -> int:
    counts = round((v / AO_VOLT_RANGE) * AO_COUNTS_FULL_SCALE)
    return max(-32767, min(32767, counts))


class FpgaPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("UNMScope FPGA Live Panel")
        self.resize(620, 720)
        self.session = None

        self._build_ui()
        self._connect_fpga()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(300)

    # -- UI ----------------------------------------------------------
    def _build_ui(self):
        root = QVBoxLayout(self)

        # Connection status
        self.status_label = QLabel("Connecting...")
        self.status_label.setStyleSheet("font-weight: bold;")
        root.addWidget(self.status_label)

        # Live readback
        ro_box = QGroupBox("Live register readback")
        ro_form = QFormLayout(ro_box)
        self.lbl_sw_version = QLabel("-")
        self.lbl_ao_mode = QLabel("-")
        self.lbl_static_ao = QLabel("-")
        self.lbl_trigger = QLabel("-")
        ro_form.addRow("SW Version:", self.lbl_sw_version)
        ro_form.addRow("AO Mode:", self.lbl_ao_mode)
        ro_form.addRow("Static AO to set:", self.lbl_static_ao)
        ro_form.addRow("Trigger regs:", self.lbl_trigger)
        root.addWidget(ro_box)

        # Safe state
        safe_box = QGroupBox("Safe state")
        safe_row = QHBoxLayout(safe_box)
        btn_safe = QPushButton("Write all-zero safe state")
        btn_safe.clicked.connect(self.on_safe_state)
        safe_row.addWidget(btn_safe)
        root.addWidget(safe_box)

        # Static voltage control (Stage B)
        volt_box = QGroupBox("Static voltage (Stage B) -- verify with oscilloscope")
        volt_form = QFormLayout(volt_box)
        self.channel_combo = QComboBox()
        self.channel_combo.addItems(STATIC_AO_CHANNELS)
        self.voltage_spin = QDoubleSpinBox()
        self.voltage_spin.setRange(-10.0, 10.0)
        self.voltage_spin.setDecimals(3)
        self.voltage_spin.setSingleStep(0.1)
        self.voltage_spin.setValue(1.0)
        self.counts_preview = QLabel("")
        self.voltage_spin.valueChanged.connect(self._update_counts_preview)
        self._update_counts_preview()
        btn_apply = QPushButton("Apply (other channels -> 0)")
        btn_apply.clicked.connect(self.on_apply_voltage)
        volt_form.addRow("Channel:", self.channel_combo)
        volt_form.addRow("Voltage (V):", self.voltage_spin)
        volt_form.addRow("-> counts:", self.counts_preview)
        volt_form.addRow(btn_apply)
        root.addWidget(volt_box)

        # Single trigger pulse control (Stage C)
        trig_box = QGroupBox("Single trigger pulse (Stage C) -- fires a real pulse on DIO4")
        trig_form = QFormLayout(trig_box)
        self.trigger_up_ticks = QSpinBox()
        self.trigger_up_ticks.setRange(1, 1_000_000)
        self.trigger_up_ticks.setValue(400)  # 400 ticks @ 40MHz = 10us, adjust as needed
        self.cam_delay_ticks = QSpinBox()
        self.cam_delay_ticks.setRange(0, 1_000_000)
        self.cam_delay_ticks.setValue(0)
        self.confirm_check = QCheckBox("I'm ready -- probing DIO4 now")
        btn_fire = QPushButton("Configure + Fire ONE trigger pulse")
        btn_fire.clicked.connect(self.on_fire_trigger)
        trig_form.addRow("Trigger up (ticks):", self.trigger_up_ticks)
        trig_form.addRow("Cam Trigger delay (ticks):", self.cam_delay_ticks)
        trig_form.addRow(self.confirm_check)
        trig_form.addRow(btn_fire)
        root.addWidget(trig_box)

        # Log
        root.addWidget(QLabel("Action log:"))
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        root.addWidget(self.log)

    def _update_counts_preview(self):
        v = self.voltage_spin.value()
        self.counts_preview.setText(f"{volts_to_counts(v)}  (assumes +/-{AO_VOLT_RANGE:.0f}V = +/-{AO_COUNTS_FULL_SCALE} counts -- VERIFY)")

    # -- FPGA session --------------------------------------------------
    def _connect_fpga(self):
        try:
            self.session = nifpga.Session(bitfile=BITFILE, resource=RESOURCE)
            self.status_label.setText(f"Connected: {RESOURCE}")
            self.status_label.setStyleSheet("font-weight: bold; color: green;")
            self._log(f"Connected to {RESOURCE} ({BITFILE})")
        except Exception as e:
            self.status_label.setText(f"CONNECTION FAILED: {e}")
            self.status_label.setStyleSheet("font-weight: bold; color: red;")
            self._log(f"Connection failed: {e}")
            self.session = None

    def _log(self, msg: str):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log.append(f"[{ts}] {msg}")

    def _poll(self):
        if self.session is None:
            return
        try:
            sw_ver = self.session.registers["SW Version"].read()
            self.lbl_sw_version.setText(str(sw_ver))

            ao_mode = self.session.registers["AO Mode"].read()
            self.lbl_ao_mode.setText(f"{ao_mode} ({AO_MODE_NAMES.get(ao_mode, 'unknown')})")

            static_ao = self.session.registers["Static AO to set"].read()
            self.lbl_static_ao.setText(", ".join(f"{k}={v}" for k, v in static_ao.items()))

            trig_bits = []
            for reg_name in ("# of triggers", "Continuous Mode", "Free run", "Trigger Enable?",
                              "Trigger up (ticks)", "Cam Trigger delay (ticks)"):
                trig_bits.append(f"{reg_name}={self.session.registers[reg_name].read()}")
            self.lbl_trigger.setText("\n".join(trig_bits))
        except Exception as e:
            self._log(f"Poll error: {e}")

    # -- Actions --------------------------------------------------------
    def on_safe_state(self):
        if self.session is None:
            return
        try:
            zero_cluster = {ch: (False if ch == "AOTF on?" else 0) for ch in STATIC_AO_CHANNELS}
            zero_cluster["AOTF on?"] = False
            self.session.registers["AO Mode"].write(AO_MODE_SET_AO)
            self.session.registers["Static AO to set"].write(zero_cluster)
            self.session.registers["Set F.P. (T)"].write(True)
            self._log("Safe state written (all-zero Static AO, AO Mode=Set AO).")
        except Exception as e:
            self._log(f"Safe state FAILED: {e}")
            QMessageBox.warning(self, "Error", f"Safe state write failed:\n{e}")

    def on_apply_voltage(self):
        if self.session is None:
            return
        channel = self.channel_combo.currentText()
        v = self.voltage_spin.value()
        counts = volts_to_counts(v)
        cluster = {ch: 0 for ch in STATIC_AO_CHANNELS}
        cluster[channel] = counts
        cluster["AOTF on?"] = False
        try:
            self.session.registers["AO Mode"].write(AO_MODE_SET_AO)
            self.session.registers["Static AO to set"].write(cluster)
            self.session.registers["Set F.P. (T)"].write(True)
            self._log(f"Applied {v:.3f} V ({counts} counts) to {channel}; all other channels -> 0.")
        except Exception as e:
            self._log(f"Apply voltage FAILED: {e}")
            QMessageBox.warning(self, "Error", f"Voltage write failed:\n{e}")

    def on_fire_trigger(self):
        if self.session is None:
            return
        if not self.confirm_check.isChecked():
            QMessageBox.information(self, "Not confirmed",
                                     "Check \"I'm ready -- probing DIO4 now\" first. "
                                     "This will fire a real pulse on the camera trigger line.")
            return
        try:
            self.session.registers["# of triggers"].write(1)
            self.session.registers["Continuous Mode"].write(False)
            self.session.registers["Free run"].write(False)
            self.session.registers["Trigger up (ticks)"].write(self.trigger_up_ticks.value())
            self.session.registers["Cam Trigger delay (ticks)"].write(self.cam_delay_ticks.value())
            self._log("Trigger settings configured (# of triggers=1, Continuous=False, Free run=False).")
            self.session.registers["Trigger Enable?"].write(True)
            self._log("Trigger Enable? = True -- pulse should have fired on DIO4.")
            self.session.registers["Trigger Enable?"].write(False)
            self._log("Trigger Enable? = False (disarmed).")
        except Exception as e:
            self._log(f"Fire trigger FAILED: {e}")
            QMessageBox.warning(self, "Error", f"Trigger sequence failed:\n{e}")

    # -- Lifecycle --------------------------------------------------------
    def closeEvent(self, event):
        if self.session is not None:
            try:
                self.session.close()
                self._log("Session closed.")
            except Exception:
                pass
        event.accept()


def main():
    app = QApplication(sys.argv)
    panel = FpgaPanel()
    panel.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
