"""Nikon Z Focus Control -- Utilities window for the Arduino stepper-motor
driver that moves the Nikon Eclipse TE200 objective via the built-in focus
motor.

Threading model (actor pattern):
- Connect: ``_ConnectWorker`` (``QThread``) -- 3 s boot warmup off the GUI
  thread.
- Serial actor: ``_SerialActor`` (``QThread``) -- the ONE thread that owns
  the serial port after connect.  Commands (MOVE, SET_ZERO, poll) arrive via
  a ``queue.Queue``; results go back to the GUI via Qt signals with
  ``Qt.QueuedConnection``.  Because only one thread ever touches the port
  there are no races, no locks, and no GUI blocking.
- All other slots (button clicks, signal handlers) run on the GUI thread and
  never do serial I/O directly.
"""
from __future__ import annotations

import queue
import time
from typing import Callable

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (
    QDialog, QDoubleSpinBox, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from unmscope.hardware.arduino_focus import (
    DEFAULT_STEPS_PER_UM, FocusError, SimulatedArduinoFocus, ZFocusStage,
)

POLL_MS      = 250          # position refresh interval (ms)
WINDOW_TITLE = "Nikon Z Focus Control"


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------

class _ConnectWorker(QThread):
    """3 s Arduino boot warmup in a background thread."""
    connected = Signal(object)   # ready ZFocusStage
    error     = Signal(str)

    def __init__(self, stage: ZFocusStage, parent=None):
        super().__init__(parent)
        self._stage = stage

    def run(self) -> None:
        try:
            self._stage.connect()
            self.connected.emit(self._stage)
        except FocusError as exc:
            self.error.emit(str(exc))
        except Exception as exc:
            self.error.emit(f"{type(exc).__name__}: {exc}")


class _SerialActor(QThread):
    """The ONE thread that owns the serial port.

    Commands arrive via ``send_cmd()``; results leave via Qt signals.
    Polling happens automatically when no command is queued.
    """

    # Outgoing signals (all delivered on GUI thread via QueuedConnection)
    position   = Signal(int)    # periodic GET_POS result
    move_done  = Signal(int)    # MOVE finished: final step count
    move_error = Signal(str)    # MOVE or SET_ZERO failed
    zero_done  = Signal()       # SET_ZERO finished
    poll_error = Signal(str)    # GET_POS failed (shown only when idle)

    _STOP = object()            # sentinel in the queue

    def __init__(self, stage: ZFocusStage, parent=None):
        super().__init__(parent)
        self._stage = stage
        self._q: queue.Queue = queue.Queue()

    # -----------------------------------------------------------------------
    # Public API (called from the GUI thread)
    # -----------------------------------------------------------------------

    def send_move(self, delta_steps: int) -> None:
        self._q.put(('move', delta_steps))

    def send_set_zero(self) -> None:
        self._q.put(('set_zero',))

    def stop_actor(self) -> None:
        """Signal the actor to exit and wait up to 2 s."""
        self._q.put(self._STOP)
        self.wait(2000)

    # -----------------------------------------------------------------------
    # Thread body
    # -----------------------------------------------------------------------

    def run(self) -> None:
        poll_interval = POLL_MS / 1000.0
        last_poll     = time.monotonic() - poll_interval   # poll immediately on start

        while True:
            # --- drain any pending commands first ---
            cmd = None
            try:
                cmd = self._q.get_nowait()
            except queue.Empty:
                pass

            if cmd is self._STOP:
                break

            if cmd is not None:
                kind = cmd[0]
                if kind == 'move':
                    try:
                        final = self._stage.move_relative_steps(cmd[1])
                        self.move_done.emit(final)
                    except FocusError as exc:
                        self.move_error.emit(str(exc))
                    except Exception as exc:
                        self.move_error.emit(f"{type(exc).__name__}: {exc}")
                    last_poll = time.monotonic()  # reset timer after move
                    continue

                elif kind == 'set_zero':
                    try:
                        self._stage.set_zero()
                        self.zero_done.emit()
                    except FocusError as exc:
                        self.move_error.emit(str(exc))
                    except Exception as exc:
                        self.move_error.emit(f"{type(exc).__name__}: {exc}")
                    continue

            # --- no command: poll if the interval has elapsed ---
            now = time.monotonic()
            if now - last_poll >= poll_interval:
                if self._stage.is_connected:
                    try:
                        steps = self._stage.get_position_steps()
                        self.position.emit(steps)
                    except FocusError as exc:
                        self.poll_error.emit(str(exc))
                last_poll = now
            else:
                # Sleep briefly so we don't spin at 100 % CPU
                time.sleep(0.02)


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class NikonFocusDialog(QDialog):
    """Non-modal Utilities window: Nikon Z Focus Control."""

    def __init__(self,
                 stage_factory: Callable[[str], ZFocusStage] | None = None,
                 log: Callable[[str], None] | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(WINDOW_TITLE)
        self.setWindowFlags(
            Qt.Window | Qt.WindowTitleHint |
            Qt.WindowCloseButtonHint | Qt.WindowMinimizeButtonHint
        )
        self.setModal(False)
        self.setFixedWidth(380)

        self._factory   = stage_factory
        self._log       = log or (lambda msg: None)
        self._stage: ZFocusStage = SimulatedArduinoFocus()

        self._connect_worker: _ConnectWorker | None = None
        self._actor:          _SerialActor   | None = None
        self._moving = False   # True while a MOVE command is in flight

        self._build_ui()
        self._update_connected_state()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(10, 10, 10, 10)

        # Connection
        conn_grp = QGroupBox("Connection")
        conn_lay = QGridLayout(conn_grp)
        conn_lay.addWidget(QLabel("COM port:"), 0, 0)
        self._com_edit = QLineEdit("COM8")
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

        # Calibration
        cal_grp = QGroupBox("Calibration")
        cal_lay = QHBoxLayout(cal_grp)
        cal_lay.addWidget(QLabel("Steps / µm:"))
        self._steps_um_spin = QDoubleSpinBox()
        self._steps_um_spin.setRange(0.1, 1000.0)
        self._steps_um_spin.setDecimals(2)
        self._steps_um_spin.setSingleStep(1.0)
        self._steps_um_spin.setValue(DEFAULT_STEPS_PER_UM)
        self._steps_um_spin.valueChanged.connect(self._on_cal_changed)
        cal_lay.addWidget(self._steps_um_spin)
        cal_lay.addStretch()
        root.addWidget(cal_grp)

        # Position readout
        pos_grp = QGroupBox("Position")
        pos_lay = QGridLayout(pos_grp)
        pos_lay.addWidget(QLabel("µm:"), 0, 0)
        self._pos_um_lbl = _readout_label("0.00")
        pos_lay.addWidget(self._pos_um_lbl, 0, 1)
        pos_lay.addWidget(QLabel("steps:"), 1, 0)
        self._pos_steps_lbl = _readout_label("0")
        pos_lay.addWidget(self._pos_steps_lbl, 1, 1)
        root.addWidget(pos_grp)

        # Jog
        jog_grp = QGroupBox("Jog")
        jog_lay = QGridLayout(jog_grp)
        jog_lay.addWidget(QLabel("Step size (steps):"), 0, 0)
        self._jog_steps_spin = QSpinBox()
        self._jog_steps_spin.setRange(1, 100_000)
        self._jog_steps_spin.setValue(100)
        jog_lay.addWidget(self._jog_steps_spin, 0, 1)
        self._jog_up_btn = QPushButton("▲  Jog Up (+)")
        self._jog_up_btn.clicked.connect(self._on_jog_up)
        self._jog_down_btn = QPushButton("▼  Jog Down (−)")
        self._jog_down_btn.clicked.connect(self._on_jog_down)
        jog_lay.addWidget(self._jog_up_btn,   1, 0, 1, 2)
        jog_lay.addWidget(self._jog_down_btn, 2, 0, 1, 2)
        root.addWidget(jog_grp)

        # Absolute move
        abs_grp = QGroupBox("Absolute Move")
        abs_lay = QHBoxLayout(abs_grp)
        abs_lay.addWidget(QLabel("Target (µm):"))
        self._abs_target_spin = QDoubleSpinBox()
        self._abs_target_spin.setRange(-1e6, 1e6)
        self._abs_target_spin.setDecimals(2)
        self._abs_target_spin.setSingleStep(1.0)
        abs_lay.addWidget(self._abs_target_spin)
        self._go_btn = QPushButton("Go")
        self._go_btn.clicked.connect(self._on_go)
        abs_lay.addWidget(self._go_btn)
        root.addWidget(abs_grp)

        # Set Zero
        ctrl_lay = QHBoxLayout()
        self._zero_btn = QPushButton("Set Zero")
        self._zero_btn.setToolTip("Reset step counter to 0 at current position")
        self._zero_btn.clicked.connect(self._on_set_zero)
        ctrl_lay.addWidget(self._zero_btn)
        ctrl_lay.addStretch()
        root.addLayout(ctrl_lay)

        # Status bar
        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        root.addWidget(sep)
        self._msg_lbl = QLabel("Ready")
        self._msg_lbl.setWordWrap(True)
        root.addWidget(self._msg_lbl)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _update_connected_state(self) -> None:
        connecting = self._connect_worker is not None and self._connect_worker.isRunning()
        connected  = self._stage.is_connected
        busy       = self._moving
        self._connect_btn.setEnabled(not connected and not connecting)
        self._disconnect_btn.setEnabled(connected and not busy)
        self._jog_up_btn.setEnabled(connected and not busy)
        self._jog_down_btn.setEnabled(connected and not busy)
        self._go_btn.setEnabled(connected and not busy)
        self._zero_btn.setEnabled(connected and not busy)
        self._abs_target_spin.setEnabled(connected and not busy)
        self._jog_steps_spin.setEnabled(connected and not busy)
        if connected:
            self._status_lbl.setText("● Connected")
            self._status_lbl.setStyleSheet("color: green;")
        else:
            self._status_lbl.setText("● Not connected")
            self._status_lbl.setStyleSheet("color: red;")

    def _set_position(self, steps: int) -> None:
        um = steps / self._stage.steps_per_um
        self._pos_steps_lbl.setText(str(steps))
        self._pos_um_lbl.setText(f"{um:.2f}")

    def _set_msg(self, msg: str) -> None:
        self._msg_lbl.setText(msg)

    def _build_real_stage(self, com_port: str) -> ZFocusStage:
        if self._factory is None:
            return SimulatedArduinoFocus(steps_per_um=self._steps_um_spin.value())
        stage = self._factory(com_port)
        stage.steps_per_um = self._steps_um_spin.value()
        return stage

    def _start_actor(self) -> None:
        self._stop_actor()
        self._actor = _SerialActor(self._stage, parent=self)
        self._actor.position.connect(self._set_position,   Qt.QueuedConnection)
        self._actor.move_done.connect(self._on_move_done,  Qt.QueuedConnection)
        self._actor.move_error.connect(self._on_move_error, Qt.QueuedConnection)
        self._actor.zero_done.connect(self._on_zero_done,  Qt.QueuedConnection)
        self._actor.poll_error.connect(self._on_poll_error, Qt.QueuedConnection)
        self._actor.start()

    def _stop_actor(self) -> None:
        if self._actor is not None:
            self._actor.stop_actor()
            self._actor = None

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_connect(self) -> None:
        port = self._com_edit.text().strip()
        try:
            stage = self._build_real_stage(port)
        except Exception as exc:
            self._set_msg(f"Connect failed: {exc}")
            self._log(f"Nikon focus connect failed: {exc}")
            return
        self._set_msg(f"Connecting to {port} … (3 s boot wait)")
        self._connect_btn.setEnabled(False)
        self._connect_worker = _ConnectWorker(stage, parent=self)
        self._connect_worker.connected.connect(self._on_connect_done,  Qt.QueuedConnection)
        self._connect_worker.error.connect(self._on_connect_error, Qt.QueuedConnection)
        self._connect_worker.start()

    def _on_connect_done(self, stage: ZFocusStage) -> None:
        self._stage = stage
        port = self._com_edit.text().strip()
        self._set_msg(f"Connected ({port})")
        self._log(f"Nikon focus connected ({port})")
        self._connect_worker = None
        self._moving = False
        self._update_connected_state()
        self._start_actor()

    def _on_connect_error(self, msg: str) -> None:
        self._set_msg(f"Connect failed: {msg}")
        self._log(f"Nikon focus connect failed: {msg}")
        self._connect_worker = None
        self._update_connected_state()

    def _on_disconnect(self) -> None:
        self._stop_actor()
        try:
            self._stage.disconnect()
        except Exception as exc:
            self._log(f"Nikon focus disconnect: {exc}")
        self._set_msg("Disconnected")
        self._log("Nikon focus disconnected")
        self._stage  = SimulatedArduinoFocus(steps_per_um=self._steps_um_spin.value())
        self._moving = False
        self._update_connected_state()

    def _on_poll_error(self, msg: str) -> None:
        if not self._moving:
            self._set_msg(f"Poll error: {msg}")

    def _on_cal_changed(self, value: float) -> None:
        self._stage.steps_per_um = value
        try:
            steps = int(self._pos_steps_lbl.text() or "0")
        except ValueError:
            steps = 0
        self._pos_um_lbl.setText(f"{steps / value:.2f}" if value else "0.00")

    def _start_move(self, delta_steps: int) -> None:
        if not self._stage.is_connected or self._actor is None:
            return
        if self._moving:
            return
        self._moving = True
        self._set_msg(f"Moving {delta_steps:+d} steps …")
        self._update_connected_state()
        self._actor.send_move(delta_steps)

    def _on_move_done(self, final_steps: int) -> None:
        self._set_position(final_steps)
        self._set_msg(f"Move done  →  {final_steps} steps")
        self._log(f"Nikon focus moved to {final_steps} steps "
                  f"({final_steps / self._stage.steps_per_um:.2f} µm)")
        self._moving = False
        self._update_connected_state()

    def _on_move_error(self, msg: str) -> None:
        self._set_msg(f"Move error: {msg}")
        self._log(f"Nikon focus move error: {msg}")
        self._moving = False
        self._update_connected_state()

    def _on_zero_done(self) -> None:
        self._set_position(0)
        self._abs_target_spin.setValue(0.0)
        self._set_msg("Position zeroed")
        self._log("Nikon focus: SET_ZERO")
        self._moving = False
        self._update_connected_state()

    def _on_jog_up(self) -> None:
        self._start_move(+self._jog_steps_spin.value())

    def _on_jog_down(self) -> None:
        self._start_move(-self._jog_steps_spin.value())

    def _on_go(self) -> None:
        try:
            cur_steps = int(self._pos_steps_lbl.text() or "0")
        except ValueError:
            cur_steps = 0
        target_steps = int(round(self._abs_target_spin.value() * self._stage.steps_per_um))
        delta = target_steps - cur_steps
        if delta:
            self._start_move(delta)

    def _on_set_zero(self) -> None:
        if not self._stage.is_connected or self._actor is None:
            return
        if self._moving:
            return
        self._moving = True
        self._update_connected_state()
        self._actor.send_set_zero()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        self._stop_actor()
        if self._connect_worker is not None and self._connect_worker.isRunning():
            self._connect_worker.wait(5000)
        if self._stage.is_connected:
            try:
                self._stage.disconnect()
            except Exception:
                pass

    def closeEvent(self, event) -> None:
        event.ignore()
        self.hide()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def real_focus_factory(com_port: str) -> ZFocusStage:
    from unmscope.hardware.arduino_focus import ArduinoFocusSerial, BAUD, POLL_TIMEOUT_S
    import serial
    return ArduinoFocusSerial(
        transport_factory=lambda port: serial.Serial(port, BAUD,
                                                     timeout=POLL_TIMEOUT_S),
        com_port=com_port,
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
    lbl.setMinimumWidth(110)
    return lbl
