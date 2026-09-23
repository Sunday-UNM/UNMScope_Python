"""ASI MS-2000 X/Y stage controller over a USB-serial COM port.

Protocol (ASCII, 115200 8-N-1, ``\\r``-terminated, 0.5 s warmup, 0.2 s
read timeout -- from the user's existing Tkinter GUI):

    W X Y\\r        ->  :A <X_units> <Y_units>\\r
    M X=<u> Y=<u>\\r  ->  move absolute
    R X=<u> Y=<u>\\r  ->  move relative
    HERE X=0 Y=0\\r  ->  set current position as origin

Units: 1 unit = 0.1 µm (= 1/10 000 mm).  Position parsing regex:
``r":A\\s+(-?\\d+)\\s+(-?\\d+)"``.

Architecture follows ``hardware/stage.py``: ``ASIProtocol`` takes an
``ASITransport``; ``ASIStage`` is the real-hardware wrapper (it
satisfies ``XYZStage`` for X/Y only -- Z always returns 0.0);
``SimulatedASIStage`` is the in-process fake.
"""
from __future__ import annotations

import abc
import re
import time
from typing import Callable, Protocol

from unmscope.hardware.stage import StageError, Vec3, XYZStage, calc_xyz_move_time_s


class ASIError(StageError):
    """Raised for ASI stage connect / command failures."""


# ---------------------------------------------------------------------------
# Transport protocol (structural sub-type of pyserial.Serial)
# ---------------------------------------------------------------------------

class ASITransport(Protocol):
    """What ``ASIProtocol`` needs from a serial port.

    A ``serial.Serial`` opened at 115200 baud with ``timeout=0.2`` and
    ``read_until(b'\\r')`` satisfies this protocol.
    """

    def write(self, data: bytes) -> int | None: ...

    def read_until(self, terminator: bytes = b"\n",
                   size: int | None = None) -> bytes:
        """Read bytes up to and including ``terminator`` (or until timeout).
        Returns whatever arrived on timeout (may be incomplete).
        """
        ...

    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BAUD = 115200
WARMUP_S = 0.5
READ_TIMEOUT_S = 0.2
UNITS_PER_UM: float = 10.0     # 1 unit = 0.1 µm  =>  10 units / µm
UNITS_PER_MM: float = 10_000.0  # 1 unit = 0.1 µm  =>  10 000 units / mm
_REPLY_RE = re.compile(r":A\s+(-?\d+)\s+(-?\d+)")


# ---------------------------------------------------------------------------
# Protocol layer
# ---------------------------------------------------------------------------

class ASIProtocol:
    """MS-2000 command VIs over ``ASITransport``."""

    def __init__(self, transport: ASITransport,
                 sleep: Callable[[float], None] = time.sleep):
        self.t = transport
        self._sleep = sleep

    # -- helpers -----------------------------------------------------------------

    def _send(self, cmd: str) -> None:
        self.t.write((cmd + "\r").encode())

    def _readline(self, timeout_hint: str = "reply") -> str:
        raw = self.t.read_until(b"\r")
        return raw.decode(errors="replace").strip()

    def _parse_xy(self, line: str) -> tuple[int, int]:
        m = _REPLY_RE.search(line)
        if not m:
            raise ASIError(f"ASI: cannot parse position from {line!r}")
        return int(m.group(1)), int(m.group(2))

    # -- commands ----------------------------------------------------------------

    def get_position_units(self) -> tuple[int, int]:
        """``W X Y\\r`` -> ``(x_units, y_units)`` (1 unit = 0.1 µm)."""
        self._send("W X Y")
        line = self._readline("W X Y reply")
        return self._parse_xy(line)

    def get_position_um(self) -> tuple[float, float]:
        x, y = self.get_position_units()
        return x / UNITS_PER_UM, y / UNITS_PER_UM

    def move_absolute_um(self, x_um: float, y_um: float) -> None:
        """``M X=<units> Y=<units>\\r`` — absolute move."""
        xu = int(round(x_um * UNITS_PER_UM))
        yu = int(round(y_um * UNITS_PER_UM))
        self._send(f"M X={xu} Y={yu}")
        # MS-2000 replies ':A' (no position), consume it
        self._readline("M reply")

    def move_relative_um(self, dx_um: float, dy_um: float) -> None:
        """``R X=<units> Y=<units>\\r`` — relative move."""
        xu = int(round(dx_um * UNITS_PER_UM))
        yu = int(round(dy_um * UNITS_PER_UM))
        self._send(f"R X={xu} Y={yu}")
        self._readline("R reply")

    def set_here(self) -> None:
        """``HERE X=0 Y=0\\r`` — set current position as origin."""
        self._send("HERE X=0 Y=0")
        self._readline("HERE reply")


# ---------------------------------------------------------------------------
# Real-hardware backend
# ---------------------------------------------------------------------------

class ASIStage(XYZStage):
    """X/Y stage over ``ASIProtocol``.  Z is always 0 (not driven by ASI).

    ``transport_factory`` receives the COM port string and must return an
    open ``ASITransport`` (typically ``serial.Serial(port, BAUD,
    timeout=READ_TIMEOUT_S)``).

    Example real factory::

        import serial
        from unmscope.hardware.asi_stage import BAUD, READ_TIMEOUT_S
        factory = lambda port: serial.Serial(port, BAUD, timeout=READ_TIMEOUT_S)
    """

    def __init__(self, transport_factory: Callable[[str], ASITransport],
                 com_port: str = "COM5",
                 velocity_um_s: float = 2500.0,
                 settling_ms: float = 300.0,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self._factory = transport_factory
        self.com_port = com_port
        self.velocity_um_s = velocity_um_s
        self.settling_ms = settling_ms
        self._sleep = sleep
        self._clock = clock
        self._proto: ASIProtocol | None = None
        self._pos_um: tuple[float, float] = (0.0, 0.0)
        self._moving = False
        self._move_done_time: float = 0.0
        # -- move-time estimate (see move_absolute_um/is_moving) --
        self._move_started: float = 0.0
        self._expected_complete: float = 0.0

    # -- lifecycle ---------------------------------------------------------------

    def connect(self) -> None:
        transport = self._factory(self.com_port)
        try:
            self._sleep(WARMUP_S)
            proto = ASIProtocol(transport, sleep=self._sleep)
            self._pos_um = proto.get_position_um()
        except Exception:
            transport.close()
            raise
        self._proto = proto

    def disconnect(self) -> None:
        if self._proto is not None:
            self._proto.t.close()
            self._proto = None
        self._moving = False

    @property
    def is_connected(self) -> bool:
        return self._proto is not None

    def _p(self) -> ASIProtocol:
        if self._proto is None:
            raise ASIError("ASI stage not connected")
        return self._proto

    # -- XYZStage interface -------------------------------------------------------

    def get_position_um(self) -> Vec3:
        x, y = self._p().get_position_um()
        self._pos_um = (x, y)
        return (x, y, 0.0)

    def move_absolute_um(self, target: Vec3,
                         wait: bool = False,
                         timeout_s: float | None = None) -> None:
        """FIXED 2026-09-22: previously returned as soon as the MS-2000
        ACKed the command (':A', command-accepted) and never set
        ``self._moving`` True anywhere, so ``is_moving()`` was permanently
        False -- there was no way to tell whether the stage had actually
        arrived. This estimates the move time from distance/velocity, the
        same pattern ``MP285Serial`` already uses in stage.py, since this
        bitfile/protocol has no implemented MS-2000 status-query command
        to poll for real arrival. ESTIMATED, NOT a hardware handshake --
        needs bench verification.
        """
        current = (self._pos_um[0], self._pos_um[1], 0.0)
        move_time = calc_xyz_move_time_s(current, (target[0], target[1], 0.0),
                                         self.velocity_um_s, self.settling_ms)
        self._p().move_absolute_um(target[0], target[1])
        self._pos_um = (target[0], target[1])
        self._moving = True
        self._move_started = self._clock()
        self._expected_complete = self._move_started + move_time
        if wait:
            limit = move_time if timeout_s is None else timeout_s
            self._sleep(min(move_time, limit) if limit is not None else move_time)
            self._moving = False
            self._expected_complete = self._clock()

    def is_moving(self) -> bool:
        if self._moving and self._clock() >= self._expected_complete:
            self._moving = False
        return self._moving

    def set_origin(self) -> None:
        self._p().set_here()
        self._pos_um = (0.0, 0.0)

    def reset(self) -> None:
        pass  # MS-2000 has no reset equivalent; reconnect if needed

    def set_velocity_um_s(self, velocity: float) -> None:
        pass  # velocity control not implemented (MS-2000 has separate speed cmd)

    # -- convenience (X/Y only) ---------------------------------------------------

    def move_relative_um_xy(self, dx: float, dy: float) -> None:
        self._p().move_relative_um(dx, dy)
        x, y = self._pos_um
        self._pos_um = (x + dx, y + dy)

    def set_here(self) -> None:
        """Alias for ``set_origin`` (used by the dialog)."""
        self.set_origin()


# ---------------------------------------------------------------------------
# Simulated backend
# ---------------------------------------------------------------------------

class SimulatedASIStage(XYZStage):
    """In-process fake: position tracked in memory, move time estimated
    the same way ``ASIStage``/``SimulatedMP285`` do (stage.py), so tests
    exercising ``wait``/``is_moving`` see realistic (if fast) timing
    instead of moves that are trivially already-done."""

    def __init__(self, velocity_um_s: float = 2500.0, settling_ms: float = 300.0,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.velocity_um_s = velocity_um_s
        self.settling_ms = settling_ms
        self._clock = clock
        self._sleep = sleep
        self._connected = False
        self._pos_um: tuple[float, float] = (0.0, 0.0)
        self._expected_complete: float = 0.0

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _require(self) -> None:
        if not self._connected:
            raise ASIError("ASI stage not connected")

    def get_position_um(self) -> Vec3:
        self._require()
        return (self._pos_um[0], self._pos_um[1], 0.0)

    def move_absolute_um(self, target: Vec3,
                         wait: bool = False,
                         timeout_s: float | None = None) -> None:
        self._require()
        current = (self._pos_um[0], self._pos_um[1], 0.0)
        move_time = calc_xyz_move_time_s(current, (target[0], target[1], 0.0),
                                         self.velocity_um_s, self.settling_ms)
        self._pos_um = (float(target[0]), float(target[1]))
        self._expected_complete = self._clock() + move_time
        if wait:
            self._sleep(move_time)
            self._expected_complete = self._clock()

    def is_moving(self) -> bool:
        return self._connected and self._clock() < self._expected_complete

    def set_origin(self) -> None:
        self._require()
        self._pos_um = (0.0, 0.0)

    def reset(self) -> None:
        pass

    def set_velocity_um_s(self, velocity: float) -> None:
        pass

    def move_relative_um_xy(self, dx: float, dy: float) -> None:
        self._require()
        x, y = self._pos_um
        self._pos_um = (x + float(dx), y + float(dy))

    def set_here(self) -> None:
        self.set_origin()
