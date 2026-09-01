"""Simple main application window.

First real (non-throwaway) GUI for UNMScope. Deliberately minimal: connect
a camera backend (real Orca Flash 4.0 or simulated), snap/live-view frames,
confirm the hardware subsystem actually works end-to-end from a real
application window. More subsystems (motion, AO, FPGA-triggered scanning)
get wired in here as they're validated, per ROADMAP.md.
"""
from __future__ import annotations

import datetime

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QFormLayout,
    QLabel, QPushButton, QComboBox, QDoubleSpinBox, QTextEdit, QMessageBox,
    QSizePolicy, QCheckBox,
)

from unmscope.hardware.camera import Camera, CameraError, OrcaFlash4Camera, SimulatedCamera


def frame_to_qpixmap(frame: np.ndarray) -> QPixmap:
    """uint16 (or any) 2D frame -> auto-contrast 8-bit QPixmap for display."""
    lo, hi = np.percentile(frame, (0.5, 99.5))
    if hi <= lo:
        hi = lo + 1
    scaled = np.clip((frame.astype(np.float32) - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)
    h, w = scaled.shape
    qimg = QImage(scaled.data, w, h, w, QImage.Format_Grayscale8).copy()
    return QPixmap.fromImage(qimg)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("UNMScope")
        self.resize(900, 800)

        self.camera: Camera | None = None
        self.live_timer = QTimer(self)
        self.live_timer.timeout.connect(self._live_tick)
        self.frame_count = 0

        self._build_ui()

    # -- UI --------------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # Camera connection controls
        conn_box = QGroupBox("Camera")
        conn_form = QFormLayout(conn_box)

        self.backend_combo = QComboBox()
        self.backend_combo.addItems(["Orca Flash 4.0 (real)", "Simulated"])
        conn_form.addRow("Backend:", self.backend_combo)

        btn_row = QHBoxLayout()
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self.on_connect_clicked)
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.clicked.connect(self.on_disconnect_clicked)
        self.disconnect_btn.setEnabled(False)
        btn_row.addWidget(self.connect_btn)
        btn_row.addWidget(self.disconnect_btn)
        conn_form.addRow(btn_row)

        self.status_label = QLabel("Not connected")
        self.status_label.setStyleSheet("font-weight: bold;")
        conn_form.addRow("Status:", self.status_label)

        root.addWidget(conn_box)

        # Acquisition controls
        acq_box = QGroupBox("Acquisition")
        acq_form = QFormLayout(acq_box)

        self.exposure_spin = QDoubleSpinBox()
        self.exposure_spin.setRange(0.1, 10000.0)
        self.exposure_spin.setDecimals(2)
        self.exposure_spin.setSuffix(" ms")
        self.exposure_spin.setValue(33.325)
        self.exposure_spin.valueChanged.connect(self.on_exposure_changed)
        acq_form.addRow("Exposure:", self.exposure_spin)

        self.ext_trigger_check = QCheckBox("External Trigger (from FPGA, DIO4)")
        self.ext_trigger_check.setEnabled(False)
        self.ext_trigger_check.toggled.connect(self.on_ext_trigger_toggled)
        acq_form.addRow(self.ext_trigger_check)
        acq_note = QLabel(
            "With External Trigger on, Snap/Live will wait for a real FPGA "
            "pulse before showing a new frame -- the view will visibly hang "
            "between pulses. This is expected."
        )
        acq_note.setWordWrap(True)
        acq_note.setStyleSheet("color: #888; font-style: italic;")
        acq_form.addRow(acq_note)

        acq_btn_row = QHBoxLayout()
        self.snap_btn = QPushButton("Snap")
        self.snap_btn.clicked.connect(self.on_snap_clicked)
        self.snap_btn.setEnabled(False)
        self.live_btn = QPushButton("Start Live")
        self.live_btn.clicked.connect(self.on_live_clicked)
        self.live_btn.setEnabled(False)
        acq_btn_row.addWidget(self.snap_btn)
        acq_btn_row.addWidget(self.live_btn)
        acq_form.addRow(acq_btn_row)

        self.frame_counter_label = QLabel("0")
        self.frame_counter_label.setStyleSheet("font-weight: bold; font-size: 16pt; color: #2a7;")
        acq_form.addRow("Frames received:", self.frame_counter_label)

        self.frame_info_label = QLabel("-")
        acq_form.addRow("Last frame:", self.frame_info_label)

        root.addWidget(acq_box)

        # Image display
        self.image_label = QLabel("(no image yet)")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet("background-color: #202020; color: #888;")
        self.image_label.setMinimumHeight(400)
        self.image_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(self.image_label, stretch=1)

        # Log
        root.addWidget(QLabel("Log:"))
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(140)
        root.addWidget(self.log)

    def _log(self, msg: str):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log.append(f"[{ts}] {msg}")

    # -- Actions -----------------------------------------------------------
    def on_connect_clicked(self):
        backend = self.backend_combo.currentText()
        self.camera = OrcaFlash4Camera() if backend.startswith("Orca") else SimulatedCamera()
        self._log(f"Connecting ({backend})...")
        try:
            self.camera.connect()
        except Exception as e:
            self._log(f"Connect FAILED: {type(e).__name__}: {e}")
            QMessageBox.warning(self, "Connection failed", str(e))
            self.camera = None
            return

        self.frame_count = 0
        self.frame_counter_label.setText("0")
        info = self.camera.info
        self.status_label.setText(f"Connected: {info.name} (S/N {info.serial}), {info.width}x{info.height}")
        self.status_label.setStyleSheet("font-weight: bold; color: green;")
        self._log(f"Connected: {info}")

        try:
            self.exposure_spin.blockSignals(True)
            self.exposure_spin.setValue(self.camera.get_exposure_ms())
            self.exposure_spin.blockSignals(False)
        except Exception:
            pass

        self.connect_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(True)
        self.snap_btn.setEnabled(True)
        self.live_btn.setEnabled(True)
        self.backend_combo.setEnabled(False)
        self.ext_trigger_check.setEnabled(hasattr(self.camera, "set_trigger_source"))

    def on_disconnect_clicked(self):
        if self.live_timer.isActive():
            self.on_live_clicked()  # stop live first
        if self.camera is not None:
            if self.ext_trigger_check.isChecked():
                self.ext_trigger_check.setChecked(False)  # back to INTERNAL before disconnect
            self.camera.disconnect()
            self._log("Disconnected.")
        self.camera = None
        self.status_label.setText("Not connected")
        self.status_label.setStyleSheet("font-weight: bold;")
        self.connect_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(False)
        self.snap_btn.setEnabled(False)
        self.live_btn.setEnabled(False)
        self.backend_combo.setEnabled(True)
        self.ext_trigger_check.setEnabled(False)

    def on_ext_trigger_toggled(self, checked: bool):
        if self.camera is None or not hasattr(self.camera, "set_trigger_source"):
            return
        try:
            if checked:
                self.camera.set_trigger_source("EXTERNAL")
                self.camera.set_trigger_polarity("POSITIVE")
                self._log("External Trigger ON (EXTERNAL/POSITIVE) -- waiting for real FPGA pulses now.")
            else:
                self.camera.set_trigger_source("INTERNAL")
                self._log("External Trigger OFF (back to INTERNAL/software trigger).")
        except Exception as e:
            self._log(f"Set trigger mode FAILED: {type(e).__name__}: {e}")

    def on_exposure_changed(self, value: float):
        if self.camera is None or not self.camera.is_connected:
            return
        try:
            self.camera.set_exposure_ms(value)
        except Exception as e:
            self._log(f"Set exposure failed: {type(e).__name__}: {e}")

    def on_snap_clicked(self):
        self._snap_and_show()

    def on_live_clicked(self):
        if self.live_timer.isActive():
            self.live_timer.stop()
            self.live_btn.setText("Start Live")
        else:
            self.live_timer.start(100)
            self.live_btn.setText("Stop Live")

    def _live_tick(self):
        self._snap_and_show()

    def _snap_and_show(self):
        if self.camera is None or not self.camera.is_connected:
            return
        try:
            frame = self.camera.snap()
        except Exception as e:
            # Catch everything, not just our own CameraError -- a raw
            # exception from pymmcore-plus/DCAM (e.g. a transient device
            # error) escaping a Qt slot uncaught left the window looking
            # "frozen" rather than showing what actually happened.
            self._log(f"Snap FAILED: {type(e).__name__}: {e}")
            if self.live_timer.isActive():
                self.on_live_clicked()
            return

        self.frame_count += 1
        pix = frame_to_qpixmap(frame)
        scaled = pix.scaled(self.image_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.image_label.setPixmap(scaled)
        now = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.frame_counter_label.setText(f"{self.frame_count}  (last at {now})")
        self.frame_info_label.setText(
            f"{frame.shape[1]}x{frame.shape[0]} {frame.dtype}, "
            f"min={frame.min()} max={frame.max()} mean={frame.mean():.1f}"
        )
        self._log(f"Frame #{self.frame_count} received at {now}")

    def closeEvent(self, event):
        if self.camera is not None:
            self.on_disconnect_clicked()
        event.accept()
