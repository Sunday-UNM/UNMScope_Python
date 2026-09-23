"""ASI MS-2000 stage: the protocol layer against a fake transport (no serial
port, no pyserial), and the move-time-estimate fix to ASIStage/SimulatedASIStage
(2026-09-22) -- is_moving() used to be permanently False, so there was no way
to tell whether a move had actually landed. Mirrors tests/test_stage.py's
FakeClock pattern for MP285Serial/SimulatedMP285."""
import pytest

from unmscope.hardware.asi_stage import ASIError, ASIProtocol, ASIStage, SimulatedASIStage


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeASITransport:
    def __init__(self):
        self.writes: list[bytes] = []
        self.replies: list[bytes] = []
        self.closed = False

    def write(self, data: bytes):
        self.writes.append(data)
        return len(data)

    def read_until(self, terminator: bytes = b"\n", size=None) -> bytes:
        return self.replies.pop(0) if self.replies else b""

    def close(self) -> None:
        self.closed = True


# -- protocol layer -----------------------------------------------------------
def test_asi_protocol_command_formatting_and_parsing():
    t = FakeASITransport()
    # one reply per round trip: get_position_units, get_position_um (its own
    # internal get_position_units call), move_absolute_um, move_relative_um, set_here
    t.replies = [b":A 1000 -2000\r", b":A 1000 -2000\r", b":A\r", b":A\r", b":A\r"]
    p = ASIProtocol(t, sleep=lambda s: None)

    assert p.get_position_units() == (1000, -2000)
    assert p.get_position_um() == (100.0, -200.0)   # 1 unit = 0.1 um
    assert t.writes[0] == b"W X Y\r"

    p.move_absolute_um(55.5, -10.0)
    assert t.writes[2] == b"M X=555 Y=-100\r"

    p.move_relative_um(1.0, -1.0)
    assert t.writes[3] == b"R X=10 Y=-10\r"

    p.set_here()
    assert t.writes[4] == b"HERE X=0 Y=0\r"


def test_asi_protocol_unparsable_reply_raises():
    t = FakeASITransport()
    t.replies = [b"garbage\r"]
    p = ASIProtocol(t, sleep=lambda s: None)
    with pytest.raises(ASIError):
        p.get_position_units()


# -- ASIStage: the move-time-estimate fix --------------------------------------
def test_asi_stage_requires_connect():
    st = ASIStage(lambda port: FakeASITransport())
    with pytest.raises(ASIError):
        st.get_position_um()


def test_asi_stage_is_moving_reflects_estimated_move_time():
    clk = FakeClock()
    t = FakeASITransport()
    t.replies = [b":A 0 0\r"]   # the connect()-time position read
    st = ASIStage(lambda port: t, com_port="COM5", velocity_um_s=1000, settling_ms=300,
                  sleep=clk.sleep, clock=clk)
    st.connect()
    assert st.is_connected and not st.is_moving()

    t.replies.append(b":A\r")   # the move's ':A' ack
    st.move_absolute_um((1000.0, 0.0, 0.0))          # 1000 um / 1000 um/s + 0.3 s settle = 1.3 s
    assert st.is_moving(), "is_moving() must be True right after a move starts"
    # position is recorded immediately (command accepted), no extra round trip needed
    assert st._pos_um == (1000.0, 0.0)

    clk.t += 1.0
    assert st.is_moving(), "estimated move time has not elapsed yet"
    clk.t += 0.5
    assert not st.is_moving(), "estimated move time has elapsed"


def test_asi_stage_wait_true_blocks_for_the_estimate():
    clk = FakeClock()
    t = FakeASITransport()
    t.replies = [b":A 0 0\r", b":A\r"]
    st = ASIStage(lambda port: t, velocity_um_s=1000, settling_ms=300, sleep=clk.sleep, clock=clk)
    st.connect()
    t0 = clk.t   # connect() itself sleeps WARMUP_S; only count the move's own wait
    st.move_absolute_um((1000.0, 0.0, 0.0), wait=True)
    assert clk.t - t0 == pytest.approx(1.3)
    assert not st.is_moving()


def test_asi_stage_z_is_always_zero():
    t = FakeASITransport()
    t.replies = [b":A 0 0\r", b":A\r"]
    st = ASIStage(lambda port: t, sleep=lambda s: None)
    st.connect()
    st.move_absolute_um((5.0, 5.0, 999.0))   # Z is ignored, never sent
    assert st._pos_um == (5.0, 5.0)
    assert b"999" not in b"".join(t.writes)


# -- SimulatedASIStage: same timing contract -----------------------------------
def test_simulated_asi_stage_timing_matches_real_backend():
    clk = FakeClock()
    st = SimulatedASIStage(velocity_um_s=1000, settling_ms=300, clock=clk, sleep=clk.sleep)
    with pytest.raises(ASIError):
        st.get_position_um()
    st.connect()
    assert not st.is_moving()

    st.move_absolute_um((1000.0, 0.0, 0.0))
    assert st.is_moving()
    clk.t += 1.3
    assert not st.is_moving()

    st.move_absolute_um((0.0, 0.0, 0.0), wait=True)
    assert clk.t == pytest.approx(1.3 + 1.3)
    assert not st.is_moving()
