"""Sample-stage hardware abstraction: the Sutter MP-285 XYZ stage LouisXIV
drives through ``Motion/3D Stage/SIMP-285/SIMP-285 Module.vi`` (an action
engine over the Sutter ``siMP-285.llb`` VISA driver) and the PI U-651
rotation stage (``PI 651-03 Module.vi``, disabled in the ini).

Ported VI for VI from the COM renders made 2026-09-05
(``VI_Diagrams/.../3D Stage/SIMP-285/SIMP-285 Module/hidden_frames`` and
``.../SIMP-285_Driver/siMP-285.llb/*``, ``siGen.llb/*``):

SIMP-285 Module.vi actions (uninitialised shift registers = the module's
state: connected, moving, Stage Position, COM Port, simulate):

* ``Init``: reads the [SIMP-285 3D Stage] register; ``simulate = ini Simulate
  OR Simulate? input OR NOT Enable?``. Real branch: ``Ctrl Init IO & Refresh
  VFD New`` (open the port, flush, 'n'), ``Set Velocity 1000`` ("force speed to
  1000 temporarily"), ``GetSet Resolution 'L'`` (low resolution), ``Set
  Velocity <ini velocity>``, ``Set Absolute Mode 'a'``, ``Get Position 'c'``.
* ``Query Position``: if moving -> "check if done": done when now > Expected
  Move Complete OR ``Is Position Reached`` (a CR arrived); when done, moving
  = False. If not moving -> ``Get Position``. Comment: "settling time N/A
  here - only applied during scan timing calculations".
* ``Set Position``: ``Translate XYZ Assignment`` on the input; (interrupt of a
  running move is disabled code: "move interrupt not working"); real branch:
  ``Get Position``, ``Set Absolute Mode``, ``Move to Position 'm'``, then, if
  Wait for Move, poll ``Is Position Reached`` until reached or elapsed >
  Calc XYZ Move Time x 1000 ms; ``Get Position``; Move started = now,
  Expected Move Complete = now + move time. Simulate branch: position :=
  target (+-0.01 um dice jitter, not ported), Sim Exp. Move Time = Calculate
  Max Move Time([current, new]); if Wait for Move, wait that long.
* ``Reset``: 'r' (sim: wait 1 s). ``Set Origin``: 'o' (sim: wait 100 ms,
  position := 0). ``Close``: close the VISA session, connected = False.
* ``Read Position (Register)``: the register only, no I/O.
* Output ``Stage Position out`` = ``Translate XYZ Assignment`` of the register.

Serial protocol (siMP-285.llb, siGen.llb): 9600 baud, 8 data bits, 1 stop
bit, no parity, no flow control ("mandatory RS-232 settings for all Sutter
controllers"); every command is a letter (+ payload) + CR (0x0D), every reply
ends with CR and "any byte other than CR after a command is a driver error
(-285 means 0)". 'c' -> 3 x int32 little-endian microsteps + CR (13 bytes),
x 0.04 = um; 'm' + 3 x int32 LE (um x 25) + CR, the CR reply arrives when the
move ends; 'a' absolute mode, 'b' relative, 'o' origin, 'r' reset (no reply
read), 'n' refresh VFD, 's' status (32 bytes + CR; velocity word at offset
28, bit 15 = high-resolution flag: low res max 3000 um/s, high res max 1310
um/s), 'V' + uint16 LE word + CR sets velocity/resolution, Ctrl-C (0x03)
interrupts a move ('=' reply). The driver reads with 50 ms waits and a 3 s
timeout. Only the fake transport exercised this code: NOT bench-verified.

Nothing here talks to a serial port by itself: ``MP285Serial`` takes a
transport factory (pyserial is not a dependency); ``SimulatedMP285`` is the
backend the panel uses while the stage is not connected to this rig.
"""
from __future__ import annotations

import abc
import struct
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from unmscope.config.spim_ini import XYZ_ASSIGNMENTS

Vec3 = tuple[float, float, float]

#: siMP-285 'Get Position' comment: "multiply by 0.04" (um per microstep);
#: 'Move to Position' uses the inverse, 25 microsteps per um.
UM_PER_MICROSTEP = 0.04
MICROSTEPS_PER_UM = 25
CR = b"\r"
#: siMP-285 'Set Velocity & Resolution' limits (comment on the diagram).
MAX_VELOCITY_LOW_RES = 3000
MAX_VELOCITY_HIGH_RES = 1310
#: 'Get Position' / 'Set Velocity': ms between write and read, and the
#: driver's overall read timeout.
DRIVER_WAIT_MS = 50
DRIVER_TIMEOUT_S = 3.0


class StageError(RuntimeError):
    """Raised for stage connect / command failures (the driver's -285)."""


# -- pure functions ported from the 3D Stage VIs ------------------------------
def translate_xyz_assignment(pos: Vec3, assignment: int | str) -> Vec3:
    """``Translate XYZ Assignment.vi``: permute a position cluster by the
    'Stage XYZ : Image XYZ' enum. Read from the six case frames: the case
    name lists which input axis feeds output X, Y, Z in turn ("XZY" = X, Z,
    Y -> output X = input X, output Y = input Z, output Z = input Y)."""
    name = assignment if isinstance(assignment, str) else XYZ_ASSIGNMENTS[int(assignment) % len(XYZ_ASSIGNMENTS)]
    if name not in XYZ_ASSIGNMENTS:
        raise ValueError(f"unknown XYZ assignment {assignment!r}")
    idx = {"X": 0, "Y": 1, "Z": 2}
    return (pos[idx[name[0]]], pos[idx[name[1]]], pos[idx[name[2]]])


def calc_xyz_move_time_s(current: Vec3, new: Vec3, velocity_um_s: float, settling_ms: float) -> float:
    """``SIMP-285 - Calc XYZ Move Time.vi``: max |new - current| / velocity
    + settling time (s). Velocity <= 0 gives an infinite time."""
    delta = max(abs(n - c) for n, c in zip(new, current))
    if velocity_um_s <= 0:
        return float("inf") if delta > 0 else settling_ms / 1000.0
    return delta / velocity_um_s + settling_ms * 0.001


def calc_max_move_time_s(positions: list[Vec3], velocity_um_s: float, settling_ms: float,
                         enabled: bool = True) -> float:
    """``Calculate Max Move Time.vi`` (XYZ term): rotate the list by one
    (first position moved to the end) and take the largest per-move XYZ
    delta; time = max delta / velocity + settling (ms -> s). 0 when the stage
    is not enabled (the Enable? = False case) or with fewer than two rows."""
    if not enabled or len(positions) < 2:
        return 0.0
    rotated = positions[1:] + positions[:1]
    max_delta = max(max(abs(a - b) for a, b in zip(p, q)) for p, q in zip(positions, rotated))
    if velocity_um_s <= 0:
        return float("inf")
    return max_delta / velocity_um_s + settling_ms * 0.001


# -- ABCs ----------------------------------------------------------------------
class XYZStage(abc.ABC):
    """Sample-stage interface. Positions are in um and in IMAGE axes: the
    'XYZ Assignment' permutation is applied at the boundary, as SIMP-285
    Module does on its Stage Position in/out terminals."""

    xyz_assignment: int = 5           # ini default: ZYX
    velocity_um_s: float = 2500.0
    settling_ms: float = 300.0

    @abc.abstractmethod
    def connect(self) -> None: ...

    @abc.abstractmethod
    def disconnect(self) -> None: ...

    @property
    @abc.abstractmethod
    def is_connected(self) -> bool: ...

    @abc.abstractmethod
    def get_position_um(self) -> Vec3:
        """'Query Position': the current position (image axes)."""

    @abc.abstractmethod
    def move_absolute_um(self, target: Vec3, wait: bool = False, timeout_s: float | None = None) -> None:
        """'Set Position'. ``wait`` = the module's 'Wait for Move (T, blocking)'."""

    @abc.abstractmethod
    def is_moving(self) -> bool: ...

    @abc.abstractmethod
    def set_origin(self) -> None: ...

    @abc.abstractmethod
    def reset(self) -> None: ...

    @abc.abstractmethod
    def set_velocity_um_s(self, velocity: float) -> None: ...

    def wait_for_move(self, timeout_s: float, poll_ms: int = 25,
                      sleep: Callable[[float], None] = time.sleep,
                      clock: Callable[[], float] = time.monotonic) -> bool:
        """``MP285 -Wait for Move (Poll).vi``: Query Position, 25 ms wait,
        stop when not moving OR timeout. Returns False on timeout."""
        t0 = clock()
        while self.is_moving():
            if clock() - t0 > timeout_s:
                return False
            sleep(poll_ms / 1000.0)
        return True

    # helpers shared by the backends
    def _to_stage(self, image_xyz: Vec3) -> Vec3:
        return translate_xyz_assignment(image_xyz, self.xyz_assignment)

    def _to_image(self, stage_xyz: Vec3) -> Vec3:
        return translate_xyz_assignment(stage_xyz, self.xyz_assignment)


@dataclass
class RotationStatus:
    """``Rotation Stage Status Cluster.ctl``: Connected, Degrees, Moving."""
    connected: bool = False
    degrees: float = 0.0
    moving: bool = False


class RotationStage(abc.ABC):
    """PI U-651 rotation stage (``PI 651-03 Module.vi``: Init | Read Status |
    Read Status (Register) | Move (Absolute) | Set Speed | Close)."""

    speed_deg_s: float = 72.0
    settling_ms: float = 100.0

    @abc.abstractmethod
    def connect(self) -> None: ...

    @abc.abstractmethod
    def disconnect(self) -> None: ...

    @abc.abstractmethod
    def read_status(self) -> RotationStatus: ...

    @abc.abstractmethod
    def move_absolute_deg(self, degrees: float) -> None: ...

    @abc.abstractmethod
    def set_speed_deg_s(self, speed: float) -> None: ...


# -- simulated backends ---------------------------------------------------------
class SimulatedMP285(XYZStage):
    """SIMP-285 Module's own simulate branch: a move sets the position at
    once and reports Moving? until Sim Exp. Move Time (max delta / velocity +
    settling) has elapsed. ``clock`` / ``sleep`` are injectable for tests;
    ``sim_delays`` adds the module's 1 s Reset and 100 ms Set Origin waits."""

    def __init__(self, velocity_um_s: float = 2500.0, settling_ms: float = 300.0, xyz_assignment: int = 5,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 sim_delays: bool = False):
        self.velocity_um_s = float(velocity_um_s)
        self.settling_ms = float(settling_ms)
        self.xyz_assignment = int(xyz_assignment)
        self._clock = clock
        self._sleep = sleep
        self._sim_delays = sim_delays
        self._connected = False
        self._stage_pos: Vec3 = (0.0, 0.0, 0.0)
        self._move_started = 0.0
        self._expected_complete = 0.0
        self.last_move_time_s = 0.0

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _require(self):
        if not self._connected:
            raise StageError("stage not connected")

    def get_position_um(self) -> Vec3:
        self._require()
        return self._to_image(self._stage_pos)

    def is_moving(self) -> bool:
        return self._connected and self._clock() < self._expected_complete

    def move_absolute_um(self, target: Vec3, wait: bool = False, timeout_s: float | None = None) -> None:
        self._require()
        new = self._to_stage(tuple(float(v) for v in target))
        t = calc_max_move_time_s([self._stage_pos, new], self.velocity_um_s, self.settling_ms)
        self._stage_pos = new
        self.last_move_time_s = t
        self._move_started = self._clock()
        self._expected_complete = self._move_started + t
        if wait:
            self._sleep(t)
            self._expected_complete = self._clock()

    def set_origin(self) -> None:
        self._require()
        if self._sim_delays:
            self._sleep(0.1)
        self._stage_pos = (0.0, 0.0, 0.0)

    def reset(self) -> None:
        self._require()
        if self._sim_delays:
            self._sleep(1.0)

    def set_velocity_um_s(self, velocity: float) -> None:
        self.velocity_um_s = float(velocity)


class SimulatedRotationStage(RotationStage):
    """PI 651-03 Module's Simulate? branch: instant moves; 'Moving' is
    reported for |delta| / speed + settling."""

    def __init__(self, speed_deg_s: float = 72.0, settling_ms: float = 100.0,
                 clock: Callable[[], float] = time.monotonic):
        self.speed_deg_s = float(speed_deg_s)
        self.settling_ms = float(settling_ms)
        self._clock = clock
        self._connected = False
        self._deg = 0.0
        self._expected_complete = 0.0

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def read_status(self) -> RotationStatus:
        return RotationStatus(self._connected, self._deg, self._connected and self._clock() < self._expected_complete)

    def move_absolute_deg(self, degrees: float) -> None:
        if not self._connected:
            raise StageError("rotation stage not connected")
        t = (abs(degrees - self._deg) / self.speed_deg_s if self.speed_deg_s > 0 else 0.0) + self.settling_ms * 0.001
        self._deg = float(degrees)
        self._expected_complete = self._clock() + t

    def set_speed_deg_s(self, speed: float) -> None:
        self.speed_deg_s = float(speed)


# -- the real MP-285, over an injected transport ----------------------------------
class SerialTransport(Protocol):
    """What ``MP285Protocol`` needs from a serial port (a pyserial ``Serial``
    opened at 9600 8-N-1 with a read timeout satisfies it)."""

    def write(self, data: bytes) -> int | None: ...
    def read(self, size: int) -> bytes: ...
    @property
    def in_waiting(self) -> int: ...
    def reset_input_buffer(self) -> None: ...
    def close(self) -> None: ...


#: 'siAll Const RS-232 Serial Settings' + 'Initialize VISA Serial Resource'.
SERIAL_SETTINGS = {"baudrate": 9600, "bytesize": 8, "parity": "N", "stopbits": 1, "rtscts": False,
                   "xonxoff": False}


class MP285Protocol:
    """The siMP-285.llb command VIs over a transport. Every method mirrors
    one VI (named in its docstring); replies are checked the way the driver
    does (the byte after the payload must be CR)."""

    def __init__(self, transport: SerialTransport, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, timeout_s: float = DRIVER_TIMEOUT_S):
        self.t = transport
        self._sleep = sleep
        self._clock = clock
        self.timeout_s = timeout_s

    # -- low level ---------------------------------------------------------------
    def _read_exact(self, n: int) -> bytes:
        """Read ``n`` bytes with the driver's loop: 50 ms wait, read, repeat
        until everything arrived or 3 s passed."""
        t0 = self._clock()
        buf = b""
        while len(buf) < n:
            buf += self.t.read(n - len(buf))
            if len(buf) >= n:
                break
            self._sleep(DRIVER_WAIT_MS / 1000.0)        # only while the reply is still incomplete
            if self._clock() - t0 > self.timeout_s:
                raise StageError(f"MP-285 did not answer within {self.timeout_s:g} s "
                                 f"({len(buf)} of {n} bytes)")
        return buf

    def _expect_cr(self, what: str) -> None:
        b = self._read_exact(1)
        if b != CR:
            self.refresh_vfd()
            raise StageError(f"MP-285 {what}: expected CR, got {b!r} (driver error -285)")

    def _command(self, letter: bytes, payload: bytes = b"") -> None:
        self.t.write(letter + payload + CR)

    # -- commands ------------------------------------------------------------------
    def init_io(self) -> None:
        """'Ctrl Init IO & Refresh VFD New': flush what the controller may
        have put on the port, then refresh the front display ('n')."""
        self.t.reset_input_buffer()
        self.refresh_vfd()

    def refresh_vfd(self) -> None:
        """'Cmd Refresh VFD (n)'. UNVERIFIED that 'n' answers with a CR (the
        VI was not read); we drain a CR if one is waiting."""
        self._command(b"n")
        self._sleep(DRIVER_WAIT_MS / 1000.0)
        if self.t.in_waiting:
            self.t.read(1)

    def get_position_microsteps(self) -> tuple[int, int, int]:
        """'Cmd Get Position (c)': 'c' + CR -> 12 bytes (3 x int32 LE) + CR."""
        self._command(b"c")
        data = self._read_exact(13)
        if data[12:13] != CR:
            self.refresh_vfd()
            raise StageError(f"MP-285 'c': byte 13 is {data[12:13]!r}, not CR")
        return struct.unpack("<iii", data[:12])

    def get_position_um(self) -> Vec3:
        x, y, z = self.get_position_microsteps()
        return (x * UM_PER_MICROSTEP, y * UM_PER_MICROSTEP, z * UM_PER_MICROSTEP)

    def move_absolute_um(self, xyz: Vec3) -> None:
        """'Cmd Move to Position (m)': 'm' + 3 x int32 LE (um x 25) + CR.
        Write only -- the controller answers CR when the move ends."""
        steps = [int(round(v * MICROSTEPS_PER_UM)) for v in xyz]
        self._command(b"m", struct.pack("<iii", *steps))

    def is_position_reached(self) -> bool:
        """'Func Is Position Reached': no byte waiting -> False; a CR -> True;
        anything else -> error -285."""
        if self.t.in_waiting <= 0:
            return False
        b = self.t.read(1)
        if b == CR:
            return True
        self.refresh_vfd()
        raise StageError(f"MP-285 move reply {b!r} is not CR (driver error -285)")

    def set_absolute_mode(self) -> None:
        """'Cmd Set Absolute Mode (a)'."""
        self._command(b"a")
        self._expect_cr("'a'")

    def set_relative_mode(self) -> None:
        """'Cmd Set Relative Mode (b)' (not used by the module)."""
        self._command(b"b")
        self._expect_cr("'b'")

    def set_origin(self) -> None:
        """'Cmd Set Origin (o)': 'o' + CR, expect CR, then refresh the VFD."""
        self._command(b"o")
        self._expect_cr("'o'")
        self.refresh_vfd()

    def reset(self) -> None:
        """'Cmd Reset (r)': write only ('The MP-285 controller was RESET')."""
        self._command(b"r")

    def interrupt_move(self) -> bool:
        """'Cmd Interrupt Move (^C)': 0x03, reads 2 bytes; '=' means a move
        was interrupted. The module has this call disabled ("move interrupt
        not working"); kept for completeness, UNVERIFIED."""
        self.t.write(b"\x03")
        self._sleep(DRIVER_WAIT_MS / 1000.0)
        reply = self.t.read(2)
        return b"=" in reply

    def get_status(self) -> bytes:
        """'s' + CR -> 32 status bytes + CR (read inside 'Set Velocity &
        Resolution' and 'GetSet Resolution')."""
        self._command(b"s")
        data = self._read_exact(33)
        if data[32:33] != CR:
            self.refresh_vfd()
            raise StageError("MP-285 's': status not terminated by CR")
        return data[:32]

    def get_velocity(self) -> tuple[int, bool]:
        """(velocity um/s, high resolution?) from status word at offset 28
        (little-endian uint16, bit 15 = resolution flag)."""
        word = struct.unpack("<H", self.get_status()[28:30])[0]
        return word & 0x7FFF, bool(word & 0x8000)

    def _write_velocity_word(self, velocity: int, high_res: bool) -> None:
        word = (int(velocity) & 0x7FFF) | (0x8000 if high_res else 0)
        self._command(b"V", struct.pack("<H", word))
        self._expect_cr("'V'")

    def set_velocity(self, velocity_um_s: float) -> int:
        """'Cmd Set Velocity & Resolution (V)': read the current word; an
        input <= 0 changes nothing; an equal value changes nothing; otherwise
        write the new speed with the current resolution flag. Returns the
        velocity now in force."""
        current, high_res = self.get_velocity()
        v = int(round(velocity_um_s))
        if v <= 0 or v == current:
            return current
        limit = MAX_VELOCITY_HIGH_RES if high_res else MAX_VELOCITY_LOW_RES
        if v > limit:
            raise StageError(f"MP-285 velocity {v} um/s exceeds {limit} in "
                             f"{'high' if high_res else 'low'} resolution")
        self._write_velocity_word(v, high_res)
        return v

    def set_resolution(self, high: bool) -> bool:
        """'Ctrl GetSet Resolution' with 'H' / 'L': rewrite the velocity word
        with the resolution flag; refuses high resolution above 1310 um/s
        ("Can't change the Resolution from LOW to HIGH")."""
        current, high_res = self.get_velocity()
        if high == high_res:
            return high_res
        if high and current > MAX_VELOCITY_HIGH_RES:
            raise StageError("MP-285: cannot switch to high resolution above 1310 um/s")
        self._write_velocity_word(current, high)
        return high


class MP285Serial(XYZStage):
    """SIMP-285 Module's real branch over ``MP285Protocol``. ``transport_factory``
    opens the port (``lambda port: serial.Serial(port, timeout=0.05,
    **SERIAL_SETTINGS)`` with pyserial); tests hand in a fake.

    Deviation from the module, deliberate until a bench test: after a
    non-waited 'm' the module still sends 'c' at once; a real controller
    answers 'c' only after the move, so here the position is re-read when
    the move is seen to complete (Query Position) instead.
    """

    def __init__(self, transport_factory: Callable[[str], SerialTransport], com_port: str = "COM8",
                 velocity_um_s: float = 2500.0, settling_ms: float = 300.0, xyz_assignment: int = 5,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self._factory = transport_factory
        self.com_port = com_port
        self.velocity_um_s = float(velocity_um_s)
        self.settling_ms = float(settling_ms)
        self.xyz_assignment = int(xyz_assignment)
        self._sleep = sleep
        self._clock = clock
        self._proto: MP285Protocol | None = None
        self._stage_pos: Vec3 = (0.0, 0.0, 0.0)
        self._moving = False
        self._move_started = 0.0
        self._expected_complete = 0.0

    # -- lifecycle ---------------------------------------------------------------
    def connect(self) -> None:
        """'Init' (real branch): init IO + 'n', speed 1000, low resolution,
        the ini speed, absolute mode, read the position."""
        transport = self._factory(self.com_port)
        p = MP285Protocol(transport, sleep=self._sleep, clock=self._clock)
        try:
            p.init_io()
            p.set_velocity(1000)
            p.set_resolution(False)
            p.set_velocity(self.velocity_um_s)
            p.set_absolute_mode()
            self._stage_pos = p.get_position_um()
        except Exception:
            transport.close()
            raise
        self._proto = p
        self._moving = False

    def disconnect(self) -> None:
        if self._proto is not None:
            self._proto.t.close()
            self._proto = None
        self._moving = False

    @property
    def is_connected(self) -> bool:
        return self._proto is not None

    def _p(self) -> MP285Protocol:
        if self._proto is None:
            raise StageError("stage not connected")
        return self._proto

    # -- actions ---------------------------------------------------------------------
    def _check_move_done(self) -> None:
        """'Query Position' while moving: done when the expected completion
        time passed or a CR arrived; then re-read the position."""
        if not self._moving:
            return
        p = self._p()
        if self._clock() > self._expected_complete or p.is_position_reached():
            self._moving = False
            p.t.reset_input_buffer()
            self._stage_pos = p.get_position_um()

    def is_moving(self) -> bool:
        self._check_move_done()
        return self._moving

    def get_position_um(self) -> Vec3:
        if self._moving:
            self._check_move_done()
        else:
            self._stage_pos = self._p().get_position_um()
        return self._to_image(self._stage_pos)

    def move_absolute_um(self, target: Vec3, wait: bool = False, timeout_s: float | None = None) -> None:
        p = self._p()
        new = self._to_stage(tuple(float(v) for v in target))
        current = p.get_position_um()
        p.set_absolute_mode()
        move_time = calc_xyz_move_time_s(current, new, self.velocity_um_s, self.settling_ms)
        p.move_absolute_um(new)
        self._moving = True
        self._move_started = self._clock()
        self._expected_complete = self._move_started + move_time
        if wait:
            limit = move_time if timeout_s is None else timeout_s
            t0 = self._clock()
            while not p.is_position_reached():
                if self._clock() - t0 > limit:
                    break
                self._sleep(DRIVER_WAIT_MS / 1000.0)
            self._moving = False
            self._stage_pos = p.get_position_um()

    def set_origin(self) -> None:
        self._p().set_origin()
        self._stage_pos = (0.0, 0.0, 0.0)

    def reset(self) -> None:
        self._p().reset()

    def set_velocity_um_s(self, velocity: float) -> None:
        self.velocity_um_s = float(self._p().set_velocity(velocity))
