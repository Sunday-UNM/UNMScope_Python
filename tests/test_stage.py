"""Stage backends: the pure functions ported from the 3D Stage VIs, the
simulated MP-285 (timed moves), and the MP-285 serial protocol against a
fake controller on a fake transport (no serial port, no pyserial)."""
import math
import struct

import pytest

from unmscope.hardware.stage import (
    MICROSTEPS_PER_UM, UM_PER_MICROSTEP, MP285Protocol, MP285Serial, SimulatedMP285,
    SimulatedRotationStage, StageError, calc_max_move_time_s, calc_xyz_move_time_s,
    translate_xyz_assignment,
)


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


# -- pure functions -------------------------------------------------------------
def test_translate_xyz_assignment_cases():
    p = (1.0, 2.0, 3.0)
    assert translate_xyz_assignment(p, "XYZ") == (1.0, 2.0, 3.0)
    assert translate_xyz_assignment(p, "XZY") == (1.0, 3.0, 2.0)     # rendered case: swaps Y and Z
    assert translate_xyz_assignment(p, "YXZ") == (2.0, 1.0, 3.0)
    assert translate_xyz_assignment(p, "YZX") == (2.0, 3.0, 1.0)
    assert translate_xyz_assignment(p, "ZXY") == (3.0, 1.0, 2.0)
    assert translate_xyz_assignment(p, 5) == (3.0, 2.0, 1.0)         # ini value 5 = ZYX
    with pytest.raises(ValueError):
        translate_xyz_assignment(p, "ABC")


def test_move_time_formulas():
    # SIMP-285 - Calc XYZ Move Time: max delta / velocity + settling
    assert calc_xyz_move_time_s((0, 0, 0), (2500, 100, -1250), 2500, 300) == pytest.approx(1.3)
    assert calc_xyz_move_time_s((5, 5, 5), (5, 5, 5), 2500, 300) == pytest.approx(0.3)
    assert calc_xyz_move_time_s((0, 0, 0), (1, 0, 0), 0, 0) == math.inf
    # Calculate Max Move Time: rotate the list by one, max delta of consecutive moves
    pts = [(0, 0, 0), (100, 0, 0), (100, 250, 0), (0, 0, 0)]
    assert calc_max_move_time_s(pts, 100, 0) == pytest.approx(2.5)             # (100,250,0)->(0,0,0)
    assert calc_max_move_time_s(pts, 100, 0, enabled=False) == 0.0
    assert calc_max_move_time_s([(0, 0, 0)], 100, 0) == 0.0


# -- simulated stage ------------------------------------------------------------------
def test_simulated_mp285_timed_moves_and_assignment():
    clk = FakeClock()
    st = SimulatedMP285(velocity_um_s=1000, settling_ms=300, xyz_assignment=5, clock=clk, sleep=clk.sleep)
    with pytest.raises(StageError):
        st.get_position_um()
    st.connect()
    assert st.get_position_um() == (0.0, 0.0, 0.0) and not st.is_moving()
    st.move_absolute_um((100.0, 20.0, -50.0))
    assert st.get_position_um() == (100.0, 20.0, -50.0)         # image axes round-trip through ZYX
    assert st._stage_pos == (-50.0, 20.0, 100.0)                # stored in stage axes
    assert st.is_moving() and st.last_move_time_s == pytest.approx(0.4)
    clk.t += 0.3
    assert st.is_moving()
    clk.t += 0.2
    assert not st.is_moving()
    st.move_absolute_um((0.0, 0.0, 0.0), wait=True)
    assert not st.is_moving() and clk.t == pytest.approx(0.5 + 0.4)
    st.move_absolute_um((1000.0, 0.0, 0.0))
    assert st.wait_for_move(timeout_s=0.5, poll_ms=25, sleep=clk.sleep, clock=clk) is False   # 1.3 s move
    assert st.wait_for_move(timeout_s=5.0, poll_ms=25, sleep=clk.sleep, clock=clk) is True
    st.set_origin()
    assert st.get_position_um() == (0.0, 0.0, 0.0)
    st.set_velocity_um_s(2500)
    assert st.velocity_um_s == 2500
    st.disconnect()
    assert not st.is_connected


def test_simulated_rotation_stage():
    clk = FakeClock()
    r = SimulatedRotationStage(speed_deg_s=90, settling_ms=100, clock=clk)
    assert r.read_status().connected is False
    r.connect()
    r.move_absolute_deg(45)
    s = r.read_status()
    assert s.connected and s.degrees == 45 and s.moving
    clk.t += 0.61
    assert not r.read_status().moving


# -- the serial protocol against a fake controller -------------------------------------
class FakeMP285:
    """A transport that behaves like an MP-285: answers the siMP-285 command
    set and records every write."""

    def __init__(self, position_steps=(0, 0, 0), velocity=2500, high_res=False, moving_replies=True):
        self.steps = list(position_steps)
        self.velocity = velocity
        self.high_res = high_res
        self.rx = b""
        self.writes: list[bytes] = []
        self.closed = False
        self.moving_replies = moving_replies

    # SerialTransport
    def write(self, data: bytes):
        self.writes.append(data)
        cmd = data[:1]
        if cmd == b"c":
            self.rx += struct.pack("<iii", *self.steps) + b"\r"
        elif cmd == b"m":
            self.steps = list(struct.unpack("<iii", data[1:13]))
            if self.moving_replies:
                self.rx += b"\r"                        # move "completes" at once
        elif cmd in (b"a", b"b", b"o", b"n"):
            if cmd == b"o":
                self.steps = [0, 0, 0]
            self.rx += b"\r"
        elif cmd == b"s":
            word = (self.velocity & 0x7FFF) | (0x8000 if self.high_res else 0)
            self.rx += b"\x00" * 28 + struct.pack("<H", word) + b"\x00\x00" + b"\r"
        elif cmd == b"V":
            word = struct.unpack("<H", data[1:3])[0]
            self.velocity, self.high_res = word & 0x7FFF, bool(word & 0x8000)
            self.rx += b"\r"
        elif cmd == b"\x03":
            self.rx += b"=\r"
        return len(data)

    def read(self, size: int) -> bytes:
        out, self.rx = self.rx[:size], self.rx[size:]
        return out

    @property
    def in_waiting(self) -> int:
        return len(self.rx)

    def reset_input_buffer(self):
        self.rx = b""

    def close(self):
        self.closed = True


def make_proto(fake=None):
    clk = FakeClock()
    fake = fake or FakeMP285()
    return MP285Protocol(fake, sleep=clk.sleep, clock=clk), fake, clk


def test_get_position_framing_and_scale():
    p, fake, _ = make_proto(FakeMP285(position_steps=(25, -50, 250000)))
    assert p.get_position_microsteps() == (25, -50, 250000)
    assert p.get_position_um() == (1.0, -2.0, 10000.0)
    assert fake.writes[-1] == b"c\r"
    assert UM_PER_MICROSTEP * MICROSTEPS_PER_UM == 1.0


def test_move_payload_is_little_endian_int32_times_25():
    p, fake, _ = make_proto()
    p.move_absolute_um((1.0, -2.5, 0.02))
    assert fake.writes[-1] == b"m" + struct.pack("<iii", 25, -62, 0) + b"\r"     # round(0.02*25)=0 (banker's: 0.5 -> 0)
    assert p.is_position_reached() is True                                          # the fake answered CR
    assert p.is_position_reached() is False                                         # nothing waiting


def test_non_cr_reply_is_a_driver_error():
    p, fake, _ = make_proto()
    fake.rx = b"x"
    p.t.write = lambda data: fake.writes.append(data)      # 'a' without the fake's CR answer
    with pytest.raises(StageError):
        p.set_absolute_mode()
    assert fake.writes[-1] == b"n\r"                        # the driver refreshes the VFD on error


def test_timeout_when_controller_silent():
    fake = FakeMP285()
    fake.write = lambda data: None
    p, _, clk = make_proto(fake)
    with pytest.raises(StageError):
        p.get_position_microsteps()
    assert clk.t > 3.0


def test_velocity_and_resolution_words():
    p, fake, _ = make_proto(FakeMP285(velocity=2500))
    assert p.get_velocity() == (2500, False)
    assert p.set_velocity(2500) == 2500 and not any(w.startswith(b"V") for w in fake.writes)   # same -> nothing
    assert p.set_velocity(0) == 2500                                                              # <= 0 -> nothing
    assert p.set_velocity(1000) == 1000
    assert fake.writes[-1] == b"V" + struct.pack("<H", 1000) + b"\r"
    with pytest.raises(StageError):
        p.set_velocity(3001)
    assert p.set_resolution(True) is True and fake.velocity == 1000 and fake.high_res
    assert fake.writes[-1] == b"V" + struct.pack("<H", 0x8000 | 1000) + b"\r"
    with pytest.raises(StageError):
        p.set_velocity(2000)                                                                     # > 1310 in high res
    p.set_resolution(False)
    p.set_velocity(2500)
    with pytest.raises(StageError):
        p.set_resolution(True)                                                                   # 2500 > 1310
    assert p.interrupt_move() is True
    p.set_origin()
    assert fake.steps == [0, 0, 0] and fake.writes[-1] == b"n\r"
    p.reset()
    assert fake.writes[-1] == b"r\r"


def test_mp285serial_init_sequence_and_moves():
    fake = FakeMP285(position_steps=(250, 500, 750), velocity=2500)
    clk = FakeClock()
    st = MP285Serial(lambda port: fake, com_port="COM8", velocity_um_s=2500, settling_ms=300, xyz_assignment=5,
                     sleep=clk.sleep, clock=clk)
    assert not st.is_connected
    st.connect()
    letters = [w[:1] for w in fake.writes]
    # Init: 'n' refresh, then speed 1000 (s + V), low res (s, already low), the ini speed (s + V), 'a', 'c'
    assert letters == [b"n", b"s", b"V", b"s", b"s", b"V", b"a", b"c"]
    assert fake.velocity == 2500 and not fake.high_res
    assert st.get_position_um() == (30.0, 20.0, 10.0)          # ZYX: stage (10, 20, 30) um -> image (30, 20, 10)
    fake.writes.clear()
    st.move_absolute_um((0.0, 20.0, 40.0), wait=True)           # image -> stage (40, 20, 0)
    assert fake.steps == [1000, 500, 0]
    assert [w[:1] for w in fake.writes][:3] == [b"c", b"a", b"m"]
    assert not st.is_moving() and st.get_position_um() == (0.0, 20.0, 40.0)
    # a non-waited move: moving until the CR arrives or the expected time passes
    fake.moving_replies = False
    st.move_absolute_um((0.0, 0.0, 0.0))
    assert st.is_moving()
    clk.t += 0.2
    assert st.is_moving()
    fake.rx += b"\r"
    assert not st.is_moving() and st.get_position_um() == (0.0, 0.0, 0.0)
    st.set_velocity_um_s(1500)
    assert st.velocity_um_s == 1500 and fake.velocity == 1500
    st.set_origin()
    st.disconnect()
    assert fake.closed and not st.is_connected


def test_mp285serial_connect_failure_closes_port():
    fake = FakeMP285()
    fake.write = lambda data: None                      # a dead controller
    clk = FakeClock()
    st = MP285Serial(lambda port: fake, sleep=clk.sleep, clock=clk)
    with pytest.raises(StageError):
        st.connect()
    assert fake.closed and not st.is_connected
