"""Main application window -- LouisXIV-style, scoped to Continuous Scan
and Z stack modes only.

Layout modeled on the real SPIM MAIN.vi front panel (see
VI_Diagrams/SPIM/SPIM LV8.6 VIs/SPIM MAIN/SPIM MAINp.png for the visual
reference): top bar with Acquire/Stop + mode dropdown + Status + Exit,
left Scan Setup panel, right image display -- scoped to only what these
two modes need (no AO/multi-camera/perfusion/etc. yet).

Threading design (IMPORTANT -- see hardware/fpga_trigger.py and the
session's crash history): Camera is touched ONLY from the GUI thread via
QTimer polling (the same pattern validated in the round-trip test).
FpgaTriggerController's continuous-firing loop runs on ITS OWN internal
thread (see start_continuous()); it never touches Qt widgets directly --
its on_frame callback only emits a thread-safe Qt Signal, which Qt safely
queues onto the GUI thread. Z-stack mode reuses the exact same continuous-
firing mechanism, just with a target frame count checked on the GUI-thread
side of that signal (never self-joins the firing thread from within
itself). This keeps each hardware resource touched by exactly one thread,
matching the discipline that avoided further native-layer crashes.
"""
from __future__ import annotations

import datetime

import numpy as np
from PySide6.QtCore import Qt, QTimer, QObject, Signal, QRect
from PySide6.QtGui import QImage, QPixmap, QPainter, QColor, QFont
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QFormLayout,
    QLabel, QPushButton, QComboBox, QDoubleSpinBox, QSpinBox, QTextEdit,
    QMessageBox, QSizePolicy, QCheckBox,
)

from unmscope.hardware.camera import Camera, OrcaFlash4Camera, SimulatedCamera
from unmscope.hardware.fpga_trigger import FpgaTriggerController

MODE_CONTINUOUS = "Continuous Scan"
MODE_ZSTACK = "Z stack"


def frame_to_qpixmap(frame: np.ndarray, label_text: str | None = None) -> QPixmap:
    """uint16 (or any) 2D frame -> auto-contrast 8-bit QPixmap for display.
    If label_text is given, it's burned into the top-left corner of the
    image itself (not just a separate widget), with a dark backing box for
    readability regardless of the underlying frame's brightness."""
    lo, hi = np.percentile(frame, (0.5, 99.5))
    if hi <= lo:
        hi = lo + 1
    scaled = np.clip((frame.astype(np.float32) - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)
    h, w = scaled.shape
    qimg = QImage(scaled.data, w, h, w, QImage.Format_Grayscale8).copy()
    pix = QPixmap.fromImage(qimg)

    if label_text:
        painter = QPainter(pix)
        font = QFont()
        font.setPointSize(max(14, w // 40))
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        text_rect = metrics.boundingRect(label_text)
        pad = 8
        box = QRect(10, 10, text_rect.width() + 2 * pad, text_rect.height() + 2 * pad)
        painter.fillRect(box, QColor(0, 0, 0, 160))
        painter.setPen(QColor(255, 255, 0))
        painter.drawText(box, Qt.AlignCenter, label_text)
        painter.end()

    return pix


class FpgaSignals(QObject):
    """Bridges FpgaTriggerController's background-thread callbacks to the
    GUI thread. Signal.emit() is thread-safe in Qt -- calling it from a
    non-GUI thread safely queues delivery to the connected slot on the GUI
    thread, rather than touching widgets directly from the wrong thread."""
    frame_fired = Signal(int)
    error = Signal(str)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("UNMScope")
        self.resize(1000, 820)

        self.camera: Camera | None = None
        self.fpga: FpgaTriggerController | None = None
        self.fpga_signals = FpgaSignals()
        self.fpga_signals.frame_fired.connect(self._on_fpga_frame_fired)
        self.fpga_signals.error.connect(self._on_fpga_error)

        self.camera_poll_timer = QTimer(self)
        self.camera_poll_timer.timeout.connect(self._poll_camera_for_frame)

        self.frame_count = 0
        self.z_target_frames = 0
        self.acquiring = False

        self._build_ui()

    # -- UI --------------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # Top bar: Acquire/Stop, mode, Status, Exit -- mirrors SPIM MAIN's layout.
        top_bar = QHBoxLayout()
        self.acquire_btn = QPushButton("Acquire")
        self.acquire_btn.setMinimumHeight(40)
        self.acquire_btn.setStyleSheet("font-weight: bold; font-size: 12pt;")
        self.acquire_btn.clicked.connect(self.on_acquire_clicked)
        self.acquire_btn.setEnabled(False)
        top_bar.addWidget(self.acquire_btn)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems([MODE_CONTINUOUS, MODE_ZSTACK])
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        top_bar.addWidget(self.mode_combo)

        top_bar.addStretch(1)

        status_box = QGroupBox("Status")
        status_layout = QVBoxLayout(status_box)
        self.acq_status_label = QLabel("IDLE")
        self.acq_status_label.setAlignment(Qt.AlignCenter)
        self.acq_status_label.setStyleSheet(
            "background-color: #2a2; color: white; font-weight: bold; padding: 4px; border-radius: 3px;"
        )
        status_layout.addWidget(self.acq_status_label)
        top_bar.addWidget(status_box)

        exit_btn = QPushButton("EXIT")
        exit_btn.clicked.connect(self.close)
        top_bar.addWidget(exit_btn)
        root.addLayout(top_bar)

        # Main body: left Scan Setup panel + right image/log panel.
        body = QHBoxLayout()
        root.addLayout(body, stretch=1)

        left_panel = QVBoxLayout()
        body.addLayout(left_panel)

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

        self.exposure_spin = QDoubleSpinBox()
        self.exposure_spin.setRange(0.1, 10000.0)
        self.exposure_spin.setDecimals(2)
        self.exposure_spin.setSuffix(" ms")
        self.exposure_spin.setValue(100.0)
        self.exposure_spin.valueChanged.connect(self.on_exposure_changed)
        conn_form.addRow("Exposure:", self.exposure_spin)

        left_panel.addWidget(conn_box)

        # FPGA connection controls
        fpga_box = QGroupBox("FPGA Trigger (DIO4)")
        fpga_form = QFormLayout(fpga_box)
        fpga_btn_row = QHBoxLayout()
        self.fpga_connect_btn = QPushButton("Connect FPGA")
        self.fpga_connect_btn.clicked.connect(self.on_fpga_connect_clicked)
        self.fpga_disconnect_btn = QPushButton("Disconnect FPGA")
        self.fpga_disconnect_btn.clicked.connect(self.on_fpga_disconnect_clicked)
        self.fpga_disconnect_btn.setEnabled(False)
        fpga_btn_row.addWidget(self.fpga_connect_btn)
        fpga_btn_row.addWidget(self.fpga_disconnect_btn)
        fpga_form.addRow(fpga_btn_row)
        self.fpga_status_label = QLabel("Not connected")
        self.fpga_status_label.setStyleSheet("font-weight: bold;")
        fpga_form.addRow("Status:", self.fpga_status_label)
        left_panel.addWidget(fpga_box)

        # Z Stack Settings -- only relevant/shown for Z stack mode.
        self.zstack_box = QGroupBox("Z Stack Settings")
        zstack_form = QFormLayout(self.zstack_box)
        self.z_interval_spin = QDoubleSpinBox()
        self.z_interval_spin.setRange(0.01, 1000.0)
        self.z_interval_spin.setValue(1.0)
        self.z_interval_spin.setSuffix(" um")
        self.z_start_spin = QDoubleSpinBox()
        self.z_start_spin.setRange(-1000.0, 1000.0)
        self.z_start_spin.setValue(0.0)
        self.z_start_spin.setSuffix(" um")
        self.z_end_spin = QDoubleSpinBox()
        self.z_end_spin.setRange(-1000.0, 1000.0)
        self.z_end_spin.setValue(10.0)
        self.z_end_spin.setSuffix(" um")
        for w in (self.z_interval_spin, self.z_start_spin, self.z_end_spin):
            w.valueChanged.connect(self._update_slice_count)
        zstack_form.addRow("Interval:", self.z_interval_spin)
        zstack_form.addRow("Start Pos:", self.z_start_spin)
        zstack_form.addRow("End Pos:", self.z_end_spin)
        self.slice_count_label = QLabel("-")
        self.slice_count_label.setStyleSheet("font-weight: bold;")
        zstack_form.addRow("# Slices:", self.slice_count_label)
        note = QLabel(
            "Z motion is NOT yet implemented -- galvo/piezo stay at a "
            "fixed safe value. This tests the trigger/frame-count "
            "mechanism only."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-style: italic;")
        zstack_form.addRow(note)
        left_panel.addWidget(self.zstack_box)
        self._update_slice_count()

        left_panel.addStretch(1)

        # Right panel: image + frame counter + log.
        right_panel = QVBoxLayout()
        body.addLayout(right_panel, stretch=1)

        self.frame_counter_label = QLabel("0")
        self.frame_counter_label.setStyleSheet("font-weight: bold; font-size: 16pt; color: #2a7;")
        counter_row = QHBoxLayout()
        counter_row.addWidget(QLabel("Frames received:"))
        counter_row.addWidget(self.frame_counter_label)
        counter_row.addStretch(1)
        right_panel.addLayout(counter_row)

        self.frame_info_label = QLabel("-")
        right_panel.addWidget(self.frame_info_label)

        self.image_label = QLabel("(no image yet)")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet("background-color: #202020; color: #888;")
        self.image_label.setMinimumHeight(400)
        self.image_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right_panel.addWidget(self.image_label, stretch=1)

        right_panel.addWidget(QLabel("Log:"))
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(160)
        right_panel.addWidget(self.log)

        self._on_mode_changed(self.mode_combo.currentText())

    def _log(self, msg: str):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log.append(f"[{ts}] {msg}")

    def _update_acquire_enabled(self):
        ok = (
            self.camera is not None and self.camera.is_connected
            and self.fpga is not None and self.fpga.is_connected
        )
        if not self.acquiring:
            self.acquire_btn.setEnabled(ok)

    def _on_mode_changed(self, mode: str):
        self.zstack_box.setVisible(mode == MODE_ZSTACK)
        self._update_slice_count()

    def _clear_image_to_black(self):
        black = QPixmap(self.image_label.size())
        black.fill(QColor(0, 0, 0))
        self.image_label.setPixmap(black)

    def _update_slice_count(self):
        interval = self.z_interval_spin.value()
        start = self.z_start_spin.value()
        end = self.z_end_spin.value()
        if interval <= 0:
            self.slice_count_label.setText("-")
            return
        n = max(1, int(round(abs(end - start) / interval)) + 1)
        self.slice_count_label.setText(str(n))

    # -- Camera actions ----------------------------------------------------
    def on_connect_clicked(self):
        backend = self.backend_combo.currentText()
        self.camera = OrcaFlash4Camera() if backend.startswith("Orca") else SimulatedCamera()
        self._log(f"Connecting camera ({backend})...")
        try:
            self.camera.connect()
        except Exception as e:
            self._log(f"Camera connect FAILED: {type(e).__name__}: {e}")
            QMessageBox.warning(self, "Connection failed", str(e))
            self.camera = None
            return

        self.frame_count = 0
        self.frame_counter_label.setText("0")
        info = self.camera.info
        self.status_label.setText(f"Connected: {info.name} (S/N {info.serial}), {info.width}x{info.height}")
        self.status_label.setStyleSheet("font-weight: bold; color: green;")
        self._log(f"Camera connected: {info}")

        try:
            self.exposure_spin.blockSignals(True)
            self.exposure_spin.setValue(self.camera.get_exposure_ms())
            self.exposure_spin.blockSignals(False)
        except Exception:
            pass

        self.connect_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(True)
        self.backend_combo.setEnabled(False)
        self._update_acquire_enabled()

    def on_disconnect_clicked(self):
        if self.acquiring:
            self.on_acquire_clicked()  # stop first
        if self.camera is not None:
            try:
                self.camera.set_trigger_source("INTERNAL")
            except Exception:
                pass
            self.camera.disconnect()
            self._log("Camera disconnected.")
        self.camera = None
        self.status_label.setText("Not connected")
        self.status_label.setStyleSheet("font-weight: bold;")
        self.connect_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(False)
        self.backend_combo.setEnabled(True)
        self._update_acquire_enabled()

    def on_exposure_changed(self, value: float):
        if self.camera is None or not self.camera.is_connected:
            return
        try:
            self.camera.set_exposure_ms(value)
        except Exception as e:
            self._log(f"Set exposure failed: {type(e).__name__}: {e}")

    # -- FPGA actions --------------------------------------------------------
    def on_fpga_connect_clicked(self):
        self._log("Connecting FPGA...")
        ctrl = FpgaTriggerController()
        try:
            ctrl.connect()
        except Exception as e:
            self._log(f"FPGA connect FAILED: {type(e).__name__}: {e}")
            QMessageBox.warning(self, "FPGA connection failed", str(e))
            return
        self.fpga = ctrl
        self.fpga_status_label.setText("Connected, safe state")
        self.fpga_status_label.setStyleSheet("font-weight: bold; color: green;")
        self._log("FPGA connected and in safe state.")
        self.fpga_connect_btn.setEnabled(False)
        self.fpga_disconnect_btn.setEnabled(True)
        self._update_acquire_enabled()

    def on_fpga_disconnect_clicked(self):
        if self.acquiring:
            self.on_acquire_clicked()  # stop first
        if self.fpga is not None:
            self.fpga.close()
            self._log("FPGA disconnected (safe state restored).")
        self.fpga = None
        self.fpga_status_label.setText("Not connected")
        self.fpga_status_label.setStyleSheet("font-weight: bold;")
        self.fpga_connect_btn.setEnabled(True)
        self.fpga_disconnect_btn.setEnabled(False)
        self._update_acquire_enabled()

    # -- Acquire / Stop ----------------------------------------------------
    def on_acquire_clicked(self):
        if self.acquiring:
            self._stop_acquisition()
        else:
            self._start_acquisition()

    def _start_acquisition(self):
        if self.camera is None or self.fpga is None:
            return
        mode = self.mode_combo.currentText()
        self._log(f"Starting acquisition: {mode}")

        try:
            self.camera.set_trigger_source("EXTERNAL")
            self.camera.set_trigger_polarity("POSITIVE")
        except Exception as e:
            self._log(f"Failed to set external trigger: {type(e).__name__}: {e}")
            QMessageBox.warning(self, "Error", f"Failed to set external trigger:\n{e}")
            return

        self.frame_count = 0
        self.frame_counter_label.setText("0")

        if mode == MODE_ZSTACK:
            n = int(self.slice_count_label.text()) if self.slice_count_label.text().isdigit() else 1
            self.z_target_frames = n
            self._log(f"Z-stack: arming camera for {n} frames.")
            self.camera.start_sequence(n)
        else:
            self.z_target_frames = 0  # unbounded
            self._log("Continuous: arming camera for a long sequence.")
            self.camera.start_sequence(100000)

        self.acquiring = True
        self.acquire_btn.setText("Stop")
        self.acq_status_label.setText("ACQUIRING")
        self.acq_status_label.setStyleSheet(
            "background-color: #c33; color: white; font-weight: bold; padding: 4px; border-radius: 3px;"
        )
        self.mode_combo.setEnabled(False)
        self.connect_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(False)
        self.fpga_connect_btn.setEnabled(False)
        self.fpga_disconnect_btn.setEnabled(False)

        self.camera_poll_timer.start(30)

        inter_delay_s = max(0.05, self.exposure_spin.value() / 1000.0 + 0.05)
        self.fpga.start_continuous(
            inter_trigger_delay_s=inter_delay_s,
            on_frame=lambda count: self.fpga_signals.frame_fired.emit(count),
        )

    def _stop_acquisition(self):
        self._log("Stopping acquisition.")
        self.camera_poll_timer.stop()
        if self.fpga is not None:
            self.fpga.stop_continuous()
        if self.camera is not None:
            self.camera.stop_sequence()
            try:
                self.camera.set_trigger_source("INTERNAL")
            except Exception:
                pass

        self.acquiring = False
        self.acquire_btn.setText("Acquire")
        self.acq_status_label.setText("IDLE")
        self.acq_status_label.setStyleSheet(
            "background-color: #2a2; color: white; font-weight: bold; padding: 4px; border-radius: 3px;"
        )
        self.mode_combo.setEnabled(True)
        self.connect_btn.setEnabled(self.camera is None)
        self.disconnect_btn.setEnabled(self.camera is not None)
        self.fpga_connect_btn.setEnabled(self.fpga is None)
        self.fpga_disconnect_btn.setEnabled(self.fpga is not None)
        self._clear_image_to_black()
        self._log("Acquisition stopped, back to IDLE.")

    def _on_fpga_frame_fired(self, count: int):
        # Runs on the GUI thread (Qt marshals this safely across threads).
        self._log(f"FPGA fired trigger #{count}.")
        if self.z_target_frames and count >= self.z_target_frames:
            self._log(f"Z-stack target of {self.z_target_frames} triggers reached.")
            self._stop_acquisition()

    def _on_fpga_error(self, msg: str):
        self._log(f"FPGA error: {msg}")

    def _poll_camera_for_frame(self):
        if self.camera is None or not self.camera.is_connected:
            return
        try:
            if self.camera.remaining_image_count() <= 0:
                return
            frame = self.camera.pop_image()
        except Exception as e:
            self._log(f"Camera poll FAILED: {type(e).__name__}: {e}")
            return

        self.frame_count += 1
        pix = frame_to_qpixmap(frame, label_text=f"Frame #{self.frame_count}")
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
        if self.acquiring:
            self._stop_acquisition()
        if self.camera is not None:
            self.on_disconnect_clicked()
        if self.fpga is not None:
            self.on_fpga_disconnect_clicked()
        event.accept()
