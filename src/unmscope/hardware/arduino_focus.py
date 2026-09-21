"""Arduino stepper-motor driver for the Nikon Eclipse TE200 Z focus (the
built-in objective motor, driven by a stepper driver board connected to an
Arduino Mega/Uno over a USB-serial COM port).

Protocol (ASCII, 115200 8-N-1, ``\\n``-terminated, 2 s warmup):

    GET_POS\\n  ->  POS:<int_steps>\\n
    MOVE <steps>\\n  ->  (optional) POS:<int>\\n  ...  DONE:<int_steps>\\n
    SET_ZERO\\n  ->  (no reply)
    HOME\\n  ->  HOME_DONE\\n  or  DONE\\n
    CAL_RANGE\\n  ->  RANGE:<low>,<high>\\n

Units: the Arduino counts in **stepper steps**.  The GUI converts to µm
using a calibration factor (default 10 steps / µm, changeable in the
dialog).

Architecture follows ``hardware/stage.py``: the protocol class
``ArduinoFocusProtocol`` takes a ``LineTransport`` (anything with
``.write()`` / ``.readline()`` / ``.close()``), ``ArduinoFocusSerial``
is the real-hardware wrapper, and ``SimulatedArduinoFocus`` is the
in-process fake used while the Arduino is not connected.
"""
from __future__ import annotations

import abc
import time
from typing import Callable, Protocol


class FocusError(RuntimeError):
    """Raised for Arduino focus connect / command failures."""


# ---------------------------------------------------------------------------
# Transport protocol (structural sub-type of pyserial.Serial)
# ---------------------------------------------------------------------------

class LineTransport(Protocol):
    """What ``ArduinoFocusProtocol`` needs from a serial port.

    A ``serial.Serial`` opened at 115200 baud with ``timeout`` set (so
    ``readline()`` returns after the line *or* after the timeout) satisfies
    this protocol; tests inject a fake.
    """

    def write(self, data: bytes) -> int | None: ...

    def readline(self) -> bytes:
        """Read bytes up to and including the next ``\\n`` (or until timeout).
        Returns an empty ``b""`` on timeout.
        """
        ...

    def reset_input_buffer(self) -> None:
        """Discard any bytes waiting in the receive buffer."""
        ...

    @property
    def timeout(self) -> float | None: ...

    @timeout.setter
    def timeout(self, value: float | None) -> None: ...

    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BAUD = 115200
WARMUP_S = 3.0            # wait after open: Arduino Mega 2560 bootloader + banner
POLL_TIMEOUT_S = 1.0      # serial read timeout for GET_POS (fast, < 50 ms normally)
MOVE_TIMEOUT_S = 35.0     # serial read timeout while waiting for DONE (≤ 30 s move)

DEFAULT_STEPS_PER_UM: float = 10.0


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class ZFocusStage(abc.ABC):
    """One-axis Z focus stage: connect / disconnect / position / move."""

    steps_per_um: float = DEFAULT_STEPS_PER_UM

    @abc.abstractmethod
    def connect(self) -> None: ...

    @abc.abstractmethod
    def disconnect(self) -> None: ...

    @property
    @abc.abstractmethod
    def is_connected(self) -> bool: ...

    @abc.abstractmethod
    def get_position_steps(self) -> int:
        """Send ``GET_POS`` and return the current step count."""

    def get_position_um(self) -> float:
        return self.get_position_steps() / self.steps_per_um

    @abc.abstractmethod
    def move_relative_steps(self, delta_steps: int) -> int:
        """Send ``MOVE <delta_steps>`` and block until ``DONE``.
        Returns the final absolute step count."""

    def move_relative_um(self, delta_um: float) -> float:
        steps = int(round(delta_um * self.steps_per_um))
        final = self.move_relative_steps(steps)
        return final / self.steps_per_um

    @abc.abstractmethod
    def set_zero(self) -> None:
        """Send ``SET_ZERO`` (no reply expected; counter reset to 0)."""


# ---------------------------------------------------------------------------
# Real protocol over an injected transport
# ---------------------------------------------------------------------------

class ArduinoFocusProtocol:
    """Low-level command VIs over ``LineTransport``.

    Every method mirrors one Arduino command.  ``readline()`` is called with
    the port's configured timeout; callers set a generous timeout before
    connecting.
    """

    def __init__(self, transport: LineTransport,
                 sleep: Callable[[float], None] = time.sleep):
        self.t = transport
        self._sleep = sleep

    # -- initialisation ----------------------------------------------------------

    def init_io(self) -> None:
        """Flush the startup banner the Arduino Mega sends on reset
        (``TE200 Hardware Controller Initialized.\\r\\nReady for Manual
        Serial Commands...\\r\\n``) so the first ``readline()`` after a
        command reads the command's reply, not the banner."""
        self.t.reset_input_buffer()

    # -- helpers -----------------------------------------------------------------

    def _send(self, cmd: str) -> None:
        """Write ``cmd + '\\n'`` to the port."""
        self.t.write((cmd + "\n").encode())

    def _readline(self, timeout_hint: str = "reply") -> str:
        """Read one ``\\n``-terminated line.  Raises ``FocusError`` on timeout
        (empty read)."""
        raw = self.t.readline()
        if not raw:
            raise FocusError(f"Arduino: no {timeout_hint} within timeout")
        return raw.decode(errors="replace").strip()

    # -- commands ----------------------------------------------------------------

    def get_position(self) -> int:
        """``GET_POS\\n`` -> ``POS:<steps>``"""
        self._send("GET_POS")
        line = self._readline("GET_POS reply")
        if not line.startswith("POS:"):
            raise FocusError(f"Arduino GET_POS: unexpected reply {line!r}")
        try:
            return int(line[4:])
        except ValueError as exc:
            raise FocusError(f"Arduino GET_POS: cannot parse {line!r}") from exc

    def move(self, delta_steps: int,
             progress_cb: Callable[[int], None] | None = None) -> int:
        """``MOVE <steps>\\n`` -> optional ``POS:<n>`` lines -> ``DONE:<steps>``.

        ``progress_cb`` is called with each intermediate step count (may be
        ``None``).  Returns the final absolute step count from ``DONE:``.

        **Never changes the port timeout** -- on Windows, pyserial's
        ``_reconfigure_port()`` (called by the ``timeout`` setter) calls
        ``SetCommState()`` which briefly resets the port state and corrupts
        any bytes written immediately afterwards.  Instead, we loop on
        short ``readline()`` calls (using the port's existing 1 s timeout)
        until we see ``DONE:`` or the overall ``MOVE_TIMEOUT_S`` deadline
        expires.
        """
        self._send(f"MOVE {delta_steps}")
        deadline = time.monotonic() + MOVE_TIMEOUT_S
        while time.monotonic() < deadline:
            raw = self.t.readline()
            if not raw:
                # 1 s read timeout elapsed with no data -- keep waiting
                continue
            line = raw.decode(errors="replace").strip()
            if line.startswith("DONE:"):
                try:
                    return int(line[5:])
                except ValueError as exc:
                    raise FocusError(f"Arduino MOVE: cannot parse {line!r}") from exc
            if line.startswith("POS:") and progress_cb is not None:
                try:
                    progress_cb(int(line[4:]))
                except ValueError:
                    pass  # ignore bad intermediate lines
            # any other line (e.g. ERROR:) is logged and we keep waiting
        raise FocusError(
            f"Arduino MOVE {delta_steps}: no DONE reply within {MOVE_TIMEOUT_S:.0f} s"
        )

    def set_zero(self) -> None:
        """``SET_ZERO\\n`` -> ``Zero position set.``

        The Arduino replies with a confirmation line.  Reading it here keeps
        the RX buffer clean so the next ``GET_POS`` gets its own reply.
        """
        self._send("SET_ZERO")
        line = self._readline("SET_ZERO reply")
        if not line.lower().startswith("zero"):
            raise FocusError(f"Arduino SET_ZERO: unexpected reply {line!r}")

    def home(self) -> None:
        """``HOME\\n`` -> ``HOME_DONE`` or ``DONE``."""
        self._send("HOME")
        line = self._readline("HOME reply")
        if line not in ("HOME_DONE", "DONE"):
            raise FocusError(f"Arduino HOME: unexpected reply {line!r}")

    def cal_range(self) -> tuple[int, int]:
        """``CAL_RANGE\\n`` -> ``RANGE:<low>,<high>``."""
        self._send("CAL_RANGE")
        line = self._readline("CAL_RANGE reply")
        if not line.startswith("RANGE:"):
            raise FocusError(f"Arduino CAL_RANGE: unexpected reply {line!r}")
        try:
            lo, hi = line[6:].split(",", 1)
            return int(lo), int(hi)
        except (ValueError, IndexError) as exc:
            raise FocusError(f"Arduino CAL_RANGE: cannot parse {line!r}") from exc


# ---------------------------------------------------------------------------
# Real-hardware backend (uses a pyserial transport factory)
# ---------------------------------------------------------------------------

class ArduinoFocusSerial(ZFocusStage):
    """Z focus stage over ``ArduinoFocusProtocol``.

    ``transport_factory`` receives the COM port string and must return an
    open ``LineTransport`` (typically ``serial.Serial(port, BAUD,
    timeout=READLINE_TIMEOUT_S)``).  Tests inject a fake factory.

    Example real factory::

        import serial
        from unmscope.hardware.arduino_focus import BAUD, READLINE_TIMEOUT_S
        factory = lambda port: serial.Serial(port, BAUD,
                                             timeout=READLINE_TIMEOUT_S)
    """

    def __init__(self, transport_factory: Callable[[str], LineTransport],
                 com_port: str = "COM8",
                 steps_per_um: float = DEFAULT_STEPS_PER_UM,
                 sleep: Callable[[float], None] = time.sleep):
        self._factory = transport_factory
        self.com_port = com_port
        self.steps_per_um = float(steps_per_um)
        self._sleep = sleep
        self._proto: ArduinoFocusProtocol | None = None
        self._position_steps: int = 0

    # -- lifecycle ---------------------------------------------------------------

    def connect(self) -> None:
        """Open the port, wait for the Arduino to boot, flush the startup
        banner, then read the initial position."""
        transport = self._factory(self.com_port)
        try:
            self._sleep(WARMUP_S)          # let bootloader + user setup() finish
            proto = ArduinoFocusProtocol(transport, sleep=self._sleep)
            proto.init_io()                # discard startup banner from buffer
            self._position_steps = proto.get_position()
        except Exception:
            transport.close()
            raise
        self._proto = proto

    def disconnect(self) -> None:
        if self._proto is not None:
            self._proto.t.close()
            self._proto = None

    @property
    def is_connected(self) -> bool:
        return self._proto is not None

    def _p(self) -> ArduinoFocusProtocol:
        if self._proto is None:
            raise FocusError("Arduino focus stage not connected")
        return self._proto

    # -- operations ---------------------------------------------------------------

    def get_position_steps(self) -> int:
        self._position_steps = self._p().get_position()
        return self._position_steps

    def move_relative_steps(self, delta_steps: int,
                            progress_cb: Callable[[int], None] | None = None) -> int:
        final = self._p().move(delta_steps, progress_cb=progress_cb)
        self._position_steps = final
        return final

    def set_zero(self) -> None:
        self._p().set_zero()
        self._position_steps = 0


# ---------------------------------------------------------------------------
# Simulated backend (used when no Arduino is connected)
# ---------------------------------------------------------------------------

class SimulatedArduinoFocus(ZFocusStage):
    """In-process fake: moves are instant, position is tracked in memory."""

    def __init__(self, steps_per_um: float = DEFAULT_STEPS_PER_UM,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.steps_per_um = float(steps_per_um)
        self._clock = clock
        self._sleep = sleep
        self._connected = False
        self._position_steps: int = 0

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _require(self) -> None:
        if not self._connected:
            raise FocusError("Arduino focus stage not connected")

    def get_position_steps(self) -> int:
        self._require()
        return self._position_steps

    def move_relative_steps(self, delta_steps: int,
                            progress_cb: Callable[[int], None] | None = None) -> int:
        self._require()
        self._position_steps += delta_steps
        return self._position_steps

    def set_zero(self) -> None:
        self._require()
        self._position_steps = 0
