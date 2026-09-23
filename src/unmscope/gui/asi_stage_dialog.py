"""ASI X/Y Stage Control -- Utilities window for the ASI MS-2000 stage
controller connected over a USB-serial COM port.

This dialog is **not** a port of any LouisXIV VI (the ASI X/Y stage was
controlled by a separate Tkinter GUI); its layout is new.

Threading model:
- Connect: ``_ASIConnectWorker`` (``QThread``) -- the 0.5 s warmup sleep
  runs off the GUI thread to keep the window responsive.
- Position poll: ``QTimer`` on the GUI thread every ``POLL_MS`` ms.
  ``get_position_um()`` is fast (< 50 ms) so blocking the GUI thread is
  acceptable here.
- Moves (absolute, relative): on the GUI thread -- MS-2000 moves complete
  quickly (< 1 s for typical jog steps).
- Disconnect / Set Here: on the GUI thread.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QDoubleSpinBox, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from unmscope.hardware.asi_stage import (
    BAUD, READ_TIMEOUT_S, ASIError, ASIStage, SimulatedASIStage, XYZStage,
)

POLL_MS = 150          # ms, matches the original Tkinter GUI's poll interval
WINDOW_TITLE = "ASI X/Y Stage Control"
UM_PER_MM = 1000.0


# ---------------------------------------------------------------------------
# Background connect worker
# ---------------------------------------------------------------------------

class _ASIConnectWorker(QThread):
    """Runs ``stage.connect()`` off the GUI thread (0.5 s warmup sleep)."""

    connected = Signal(object)   # the XYZStage object, ready to use
    error     = Signal(str)

    def __init__(self, stage: XYZStage, parent=None):
        super().__init__(parent)
        self._stage = stage

    def run(self) -> None:
        try:
            self._stage.connect()
            self.connected.emit(self._stage)
        except ASIError as exc:
            self.error.emit(str(exc))
        except Exception as exc:
            self.error.emit(f"{type(exc).__name__}: {exc}")


class ASIStageDialog(QDialog):
    """Non-modal Utilities window: ASI X/Y Stage Control.

    ``stage_factory(com_port)`` returns a connected ``ASIStage``; pass
    ``None`` to always use the simulated backend.
    """

    def __init__(self,
                 stage_factory: Callable[[str], "ASIStage"] | None = None,
                 log: Callable[[str], None] | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(WINDOW_TITLE)
        self.setWindowFlags(
            Qt.Window | Qt.WindowTitleHint |
            Qt.WindowCloseButtonHint | Qt.WindowMinimizeButtonHint
        )
        self.setModal(False)
        self.setFixedWidth(420)

        self._factory = stage_factory
        self._log: Callable[[str], None] = log or (lambda msg: None)
        self._stage = SimulatedASIStage()
        self._connect_worker: _ASIConnectWorker | None = None

        self._build_ui()
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(POLL_MS)
        self._poll_timer.timeout.connect(self._on_poll)
        self._update_connected_state()

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(10, 10, 10, 10)

        # -- Connection group ------------------------------------------------
        conn_grp = QGroupBox("Connection")
        conn_lay = QGridLayout(conn_grp)
        conn_lay.addWidget(QLabel("COM port:"), 0, 0)
        self._com_edit = QLineEdit("COM5")
        self._com_edit.setFixedWidth(80)
        conn_lay.addWidget(self._com_edit, 0, 1)
        self._connect_btn = QPushButton("Connect")
        self._connect_btn.clicked.connect(self._on_connect)
        self._disconnect_btn = QPushButton("Disconnect")
        self._disconnect_btn.clicked.connect(self._on_disconnect)
        conn_lay.addWidget(self._connect_btn, 0, 2)
        conn_lay.addWidget(self._disconnect_btn, 0, 3)
        self._status_lbl = QLabel("● Not connected")
        self._status_lbl.setStyleSheet("color: red;")
        conn_lay.addWidget(self._status_lbl, 1, 0, 1, 4)
        root.addWidget(conn_grp)

        # -- Position readout ------------------------------------------------
        pos_grp = QGroupBox("Position")
        pos_lay = QGridLayout(pos_grp)
        pos_lay.addWidget(QLabel("X (mm):"), 0, 0)
        self._pos_x_lbl = _readout_label("0.0000")
        pos_lay.addWidget(self._pos_x_lbl, 0, 1)
        pos_lay.addWidget(QLabel("Y (mm):"), 1, 0)
        self._pos_y_lbl = _readout_label("0.0000")
        pos_lay.addWidget(self._pos_y_lbl, 1, 1)
        root.addWidget(pos_grp)

        # -- Jog group -------------------------------------------------------
        jog_grp = QGroupBox("Jog")
        jog_outer = QVBoxLayout(jog_grp)

        step_row = QHBoxLayout()
        step_row.addWidget(QLabel("Step (µm):"))
        self._jog_um_spin = QDoubleSpinBox()
        self._jog_um_spin.setRange(0.1, 50_000.0)
        self._jog_um_spin.setDecimals(1)
        self._jog_um_spin.setSingleStep(10.0)
        self._jog_um_spin.setValue(100.0)
        step_row.addWidget(self._jog_um_spin)
        step_row.addStretch()
        jog_outer.addLayout(step_row)

        # D-pad
        dpad = QGridLayout()
        self._jog_up_btn    = QPushButton("▲")
        self._jog_down_btn  = QPushButton("▼")
        self._jog_left_btn  = QPushButton("◀")
        self._jog_right_btn = QPushButton("▶")
        for btn in (self._jog_up_btn, self._jog_down_btn,
                    self._jog_left_btn, self._jog_right_btn):
            btn.setFixedSize(48, 36)
        dpad.addWidget(self._jog_up_btn,    0, 1)
        dpad.addWidget(self._jog_left_btn,  1, 0)
        dpad.addWidget(self._jog_right_btn, 1, 2)
        dpad.addWidget(self._jog_down_btn,  2, 1)
        jog_outer.addLayout(dpad)

        self._jog_up_btn.clicked.connect(lambda: self._jog(dy=+self._jog_um_spin.value()))
        self._jog_down_btn.clicked.connect(lambda: self._jog(dy=-self._jog_um_spin.value()))
        self._jog_left_btn.clicked.connect(lambda: self._jog(dx=-self._jog_um_spin.value()))
        self._jog_right_btn.clicked.connect(lambda: self._jog(dx=+self._jog_um_spin.value()))
        root.addWidget(jog_grp)

        # -- Absolute move group ---------------------------------------------
        abs_grp = QGroupBox("Absolute Move (µm)")
        abs_lay = QGridLayout(abs_grp)
        abs_lay.addWidget(QLabel("X:"), 0, 0)
        self._abs_x_spin = QDoubleSpinBox()
        self._abs_x_spin.setRange(-1e7, 1e7); self._abs_x_spin.setDecimals(2)
        abs_lay.addWidget(self._abs_x_spin, 0, 1)
        abs_lay.addWidget(QLabel("Y:"), 1, 0)
        self._abs_y_spin = QDoubleSpinBox()
        self._abs_y_spin.setRange(-1e7, 1e7); self._abs_y_spin.setDecimals(2)
        abs_lay.addWidget(self._abs_y_spin, 1, 1)
        self._go_btn = QPushButton("Go")
        self._go_btn.clicked.connect(self._on_go)
        abs_lay.addWidget(self._go_btn, 0, 2, 2, 1)
        root.addWidget(abs_grp)

        # -- Control buttons -------------------------------------------------
        ctrl_lay = QHBoxLayout()
        self._here_btn = QPushButton("Set Here as Origin")
        self._here_btn.setToolTip("Tell the ASI controller this position is (0, 0)")
        self._here_btn.clicked.connect(self._on_set_here)
        ctrl_lay.addWidget(self._here_btn)
        ctrl_lay.addStretch()
        root.addLayout(ctrl_lay)

        # -- Status bar ------------------------------------------------------
        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        root.addWidget(sep)
        self._msg_lbl = QLabel("Ready")
        self._msg_lbl.setWordWrap(True)
        root.addWidget(self._msg_lbl)

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _update_connected_state(self) -> None:
        connecting = self._connect_worker is not None and self._connect_worker.isRunning()
        connected = self._stage.is_connected
        for w in (self._jog_up_btn, self._jog_down_btn,
                  self._jog_left_btn, self._jog_right_btn,
                  self._go_btn, self._here_btn,
                  self._abs_x_spin, self._abs_y_spin,
                  self._jog_um_spin):
            w.setEnabled(connected)
        self._connect_btn.setEnabled(not connected and not connecting)
        self._disconnect_btn.setEnabled(connected)
        if connected:
            self._status_lbl.setText("● Connected")
            self._status_lbl.setStyleSheet("color: green;")
            self._poll_timer.start()
        else:
            self._status_lbl.setText("● Not connected")
            self._status_lbl.setStyleSheet("color: red;")
            self._poll_timer.stop()

    def _set_position(self, x_um: float, y_um: float) -> None:
        self._pos_x_lbl.setText(f"{x_um / UM_PER_MM:.4f}")
        self._pos_y_lbl.setText(f"{y_um / UM_PER_MM:.4f}")

    def _set_msg(self, msg: str) -> None:
        self._msg_lbl.setText(msg)

    def _build_real_stage(self, com_port: str) -> "ASIStage":
        if self._factory is None:
            return SimulatedASIStage()
        return self._factory(com_port)

    # -----------------------------------------------------------------------
    # Slots
    # -----------------------------------------------------------------------

    def _on_connect(self) -> None:
        port = self._com_edit.text().strip()
        try:
            stage = self._build_real_stage(port)
        except Exception as exc:
            self._set_msg(f"Connect failed: {exc}")
            self._log(f"ASI stage connect failed: {exc}")
            return
        self._set_msg(f"Connecting to {port} …")
        self._connect_btn.setEnabled(False)
        self._connect_worker = _ASIConnectWorker(stage, parent=self)
        self._connect_worker.connected.connect(self._on_connect_done, Qt.QueuedConnection)
        self._connect_worker.error.connect(self._on_connect_error, Qt.QueuedConnection)
        self._connect_worker.start()

    def _on_connect_done(self, stage: XYZStage) -> None:
        self._stage = stage
        port = self._com_edit.text().strip()
        self._set_msg(f"Connected ({port})")
        self._log(f"ASI stage connected ({port})")
        self._connect_worker = None
        self._update_connected_state()
        self._on_poll()

    def _on_connect_error(self, msg: str) -> None:
        self._set_msg(f"Connect failed: {msg}")
        self._log(f"ASI stage connect failed: {msg}")
        self._connect_worker = None
        self._update_connected_state()

    def _on_disconnect(self) -> None:
        try:
            self._stage.disconnect()
        except Exception as exc:
            self._log(f"ASI stage disconnect: {exc}")
        self._set_msg("Disconnected")
        self._log("ASI stage disconnected")
        self._stage = SimulatedASIStage()
        self._update_connected_state()

    def _on_poll(self) -> None:
        if not self._stage.is_connected:
            return
        try:
            pos = self._stage.get_position_um()
            self._set_position(pos[0], pos[1])
        except ASIError as exc:
            self._set_msg(f"Poll error: {exc}")

    def _jog(self, dx: float = 0.0, dy: float = 0.0) -> None:
        if not self._stage.is_connected:
            return
        try:
            if hasattr(self._stage, "move_relative_um_xy"):
                self._stage.move_relative_um_xy(dx, dy)  # type: ignore[union-attr]
            else:
                pos = self._stage.get_position_um()
                self._stage.move_absolute_um((pos[0] + dx, pos[1] + dy, 0.0))
            self._on_poll()
        except ASIError as exc:
            self._set_msg(f"Jog error: {exc}")
            self._log(f"ASI jog error: {exc}")

    def _on_go(self) -> None:
        if not self._stage.is_connected:
            return
        x_um = self._abs_x_spin.value()
        y_um = self._abs_y_spin.value()
        try:
            self._stage.move_absolute_um((x_um, y_um, 0.0))
            self._on_poll()
            self._log(f"ASI stage → ({x_um:.2f}, {y_um:.2f}) µm")
        except ASIError as exc:
            self._set_msg(f"Move error: {exc}")
            self._log(f"ASI move error: {exc}")

    def _on_set_here(self) -> None:
        try:
            self._stage.set_origin()
            self._set_msg("Origin set to current position")
            self._log("ASI stage: HERE X=0 Y=0")
            self._set_position(0.0, 0.0)
            self._abs_x_spin.setValue(0.0)
            self._abs_y_spin.setValue(0.0)
        except ASIError as exc:
            self._set_msg(f"Set Here failed: {exc}")

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def shutdown(self) -> None:
        """Called from MainWindow.closeEvent."""
        self._poll_timer.stop()
        if self._connect_worker is not None and self._connect_worker.isRunning():
            self._connect_worker.wait(3000)
        if self._stage.is_connected:
            try:
                self._stage.disconnect()
            except Exception:
                pass

    def closeEvent(self, event) -> None:
        event.ignore()
        self.hide()


# ---------------------------------------------------------------------------
# Factory helper
# ---------------------------------------------------------------------------

def real_asi_factory(com_port: str, velocity_um_s: float = 2500.0,
                     settling_ms: float = 300.0) -> ASIStage:
    """Build an ``ASIStage`` with a real pyserial transport.

    ``velocity_um_s``/``settling_ms`` feed the move-time estimate
    ``ASIStage.is_moving()`` relies on (see asi_stage.py) -- previously
    hardcoded to the class defaults regardless of the Settings page's
    Stage Velocity / Settling Time controls, which is the same number
    the MP-285 codepath already uses live.
    """
    import serial
    from unmscope.hardware.asi_stage import BAUD, READ_TIMEOUT_S
    return ASIStage(
        transport_factory=lambda port: serial.Serial(port, BAUD,
                                                     timeout=READ_TIMEOUT_S),
        com_port=com_port,
        velocity_um_s=velocity_um_s,
        settling_ms=settling_ms,
    )


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _readout_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    lbl.setStyleSheet(
        "background: #000; color: #00cc33; font-family: monospace; "
        "font-size: 14pt; padding: 2px 6px; border-radius: 3px;"
    )
    lbl.setMinimumWidth(120)
    return lbl
