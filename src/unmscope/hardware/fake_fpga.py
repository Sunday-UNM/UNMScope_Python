"""A fake NI-RIO session + trigger controller that behave like the real
PCIe-7852R bitfile as MEASURED in this project, so the acquisition flow
(GUI included) can be exercised and regression-tested with no hardware.

What is emulated (numbers from docs/trigger_free_run_plan.md,
docs/fpga_scope.md, docs/wvfrm2_packing.md):

- Registers: every control/indicator of the deployed bitfile with its
  compile-time default (docs/fpga_reset_defaults.json), clusters included.
- Free-run trigger: the cycle counter is reset by 'Trigger Enable?', so
  the first edge fires at the enable and then every Cycle(Ticks)+1 ticks;
  bounded mode stops accepting after '# of triggers' unless 'Continuous
  Mode'; '# of triggers read' counts while enabled and resets to 0 when
  the enable drops.
- AO engine: 'AO wvfrm ready' once AO Mode = 0 with words in Wvfrm2; each
  trigger consumes 2 words x 'AO # of points per trigger' and advances
  '# AO generated'; 'AO Mode' = 1 (Stop Wvfrm) resets it.
- AI stream: while armed (measured: the real stream only flows then),
  frames of 'AI # of channels' I16 at 40e6 / 'AI loop period (ticks)',
  columns 0-7 low noise, 8-13 the current AO point (X Galvo, Z Galvo,
  Z Piezo, Dither, Tiling, Filter -- decoded from the consumed words and
  range-checked against the AO limits), 14 Int Sync high for 'Trigger up
  (ticks)', 15 DIO4 high for 4000 ticks, the rest 0.
- Wvfrm2 / AI data FIFOs with configure/start/stop/read/write and the
  ReadValues namedtuple nifpga returns.

Use FakeFpgaTriggerController exactly like FpgaTriggerController; every
real code path (arm sequence, refill thread, monitor, FpgaScope) runs on
top of the fake session. Not emulated: PB registers, prefix slots,
perfusion/shutter/AOTF, DMA faults.
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque, namedtuple
from pathlib import Path

import numpy as np

from unmscope.hardware.fpga_trigger import (
    AO_LIMIT_CHANNELS, FpgaTriggerController, TICKS_PER_S, _begin_high_resolution_timers,
)

ReadValues = namedtuple("ReadValues", ["data", "elements_remaining"])
_DEFAULTS_JSON = Path(__file__).resolve().parents[3] / "docs" / "fpga_reset_defaults.json"
DIO4_HIGH_TICKS = 4000


def _load_defaults() -> dict:
    """Compile-time register defaults recorded on hardware, or a minimal
    set if the JSON is not next to the package."""
    try:
        d = json.loads(_DEFAULTS_JSON.read_text())
        regs = {}
        for section in ("controls", "indicators"):
            for name, value in d[section].items():
                regs[name] = _coerce(value)
        return regs
    except Exception:
        return {
            "AO Mode": 2, "Set F.P. (T)": False, "Trigger Enable?": False, "# of triggers": 0,
            "Continuous Mode": False, "Cycle(Ticks)": 0, "Trigger up (ticks)": 0, "AO wvfrm ready": False,
            "# of triggers read": 0, "# of triggers ignored": 0, "# AO generated": 0, "AO # points left": 0,
            "AO Waveform State": 0, "AI # of channels": 0, "AI loop period (ticks)": 100, "Free run": False,
            "AO # of points per trigger": 0, "AO ticks between points": 0,
            "AO DMA Error": {"I/O Error": False, "Buffer Underflow": False, "DMA Timeout": False},
            "AI Error": {"I/O Error": False, "Buffer Underflow": False, "DMA Timeout": False},
            "AO Limit Max (counts)": {c: 32767 for c in AO_LIMIT_CHANNELS} | {"AOTF on?": True},
            "AO Limit Min (counts)": {c: -32767 for c in AO_LIMIT_CHANNELS} | {"AOTF on?": False},
            "Static AO to set": {c: 0 for c in AO_LIMIT_CHANNELS} | {"AOTF on?": False},
            "Int Cycle Trigger": False, "Int Cycle+Added Trigger": False, "SW Version": 3,
        }


def _coerce(value):
    if isinstance(value, dict):
        return {k: _coerce(v) for k, v in value.items()}
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return value
    return value


class FakeRegister:
    def __init__(self, session: "FakeSession", name: str):
        self._session = session
        self.name = name

    def read(self):
        with self._session.lock:
            v = self._session.values.get(self.name, 0)
            return dict(v) if isinstance(v, dict) else v

    def write(self, value):
        self._session.write_register(self.name, value)


class FakeRegisters:
    def __init__(self, session: "FakeSession"):
        self._session = session

    def __getitem__(self, name: str) -> FakeRegister:
        if name not in self._session.values:
            raise KeyError(name)
        return FakeRegister(self._session, name)

    def __contains__(self, name):
        return name in self._session.values


class FakeFifo:
    def __init__(self, session: "FakeSession", name: str, datatype: str):
        self._session = session
        self.name = name
        self.datatype = datatype
        self.depth = 16389
        self.started = False
        self.q: deque = deque()

    def configure(self, requested_depth: int) -> int:
        self.depth = int(requested_depth)
        return self.depth

    def start(self):
        self.started = True

    def stop(self):
        self.started = False
        with self._session.lock:
            self.q.clear()

    def write(self, data, timeout_ms=0) -> int:
        with self._session.lock:
            self.q.extend(int(v) for v in data)
            return max(0, self.depth - len(self.q))

    def read(self, number_of_elements: int, timeout_ms=0) -> ReadValues:
        with self._session.lock:
            if number_of_elements == 0:
                return ReadValues([], len(self.q))
            n = min(number_of_elements, len(self.q))
            out = [self.q.popleft() for _ in range(n)]
            return ReadValues(out, len(self.q))


class FakeSession:
    """Emulates the bitfile's behaviour on a background thread."""

    TICK_S = 0.001

    def __init__(self, bitfile: str = "fake", resource: str = "FAKE0"):
        self.bitfile = bitfile
        self.resource = resource
        self.lock = threading.RLock()
        self.values: dict = _load_defaults()
        self.registers = FakeRegisters(self)
        self.fifos = {"Wvfrm2": FakeFifo(self, "Wvfrm2", "I64"),
                      "Get Exp": FakeFifo(self, "Get Exp", "Bool"),
                      "AI data": FakeFifo(self, "AI data", "I16")}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        # trigger engine state
        self._armed_at: float | None = None
        self._next_trigger_t: float | None = None
        self._trigger_times: list[float] = []
        # AO engine state
        self._current_point = np.zeros(8, dtype=np.int64)   # [p0, p1, X, Z, ZP, Dither, Tiling, Filter]
        # AI stream state
        self._ai_phase = 0.0
        self.log: list[tuple[float, str, object]] = []      # register writes, for tests

    # -- nifpga.Session API -----------------------------------------------------
    def reset(self):
        with self.lock:
            self.values = _load_defaults()
            for f in self.fifos.values():
                f.stop()
            self._armed_at = None
            self._trigger_times.clear()

    def run(self):
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="fake-fpga", daemon=True)
            self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._thread = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # -- register semantics ----------------------------------------------------
    def write_register(self, name: str, value):
        with self.lock:
            if name not in self.values:
                raise KeyError(name)
            old = self.values[name]
            self.values[name] = dict(value) if isinstance(value, dict) else value
            self.log.append((time.perf_counter(), name, value))
            if name == "Trigger Enable?":
                if value and not old:
                    now = time.perf_counter()
                    self._armed_at = now
                    self._next_trigger_t = now                 # reset-on-enable: first edge immediately
                    self.values["# of triggers read"] = 0
                elif not value and old:
                    self._armed_at = None
                    self._next_trigger_t = None
                    self.values["# of triggers read"] = 0      # HHMI - Trigger count.vi resets on disable
            elif name == "AO Mode":
                if value == 0:                                  # Start/Run Wvfrm
                    self.values["AO wvfrm ready"] = len(self.fifos["Wvfrm2"].q) > 0 or True
                    self.values["AO Waveform State"] = 1
                elif value == 1:                                # Stop Wvfrm
                    self.values["AO wvfrm ready"] = False
                    self.values["AO Waveform State"] = 0
                    self.values["# AO generated"] = 0
                elif value == 3:                                # Clear AO DMA: wedges (measured)
                    self.values["AO wvfrm ready"] = False
                    self.values["AO Purging"] = True
            elif name in ("AOTF ch (V)", "AO Limit Max (counts)"):
                # Measured on the deployed bitfile (spikes/32): in the default
                # AOTF mode, 'AOTF ch out (V)' follows 'AOTF ch (V)' as a DC
                # level -- but only while the AO limits permit the gate
                # ('AOTF on?'). That permission now defaults to True, so the
                # AO clamp on its own no longer turns the AOTF off.
                self._recompute_aotf_out()

    def _recompute_aotf_out(self):
        """'AOTF ch out (V)' = 'AOTF ch (V)' while the AO limits permit the
        AOTF gate, else 0 (see the write_register note)."""
        levels = self.values.get("AOTF ch (V)", {})
        allowed = bool(self.values.get("AO Limit Max (counts)", {}).get("AOTF on?", False))
        keys = ("AOTF ch 0", "AOTF ch 1", "AOTF ch 2", "AOTF ch 3")
        self.values["AOTF ch out (V)"] = {k: (int(levels.get(k, 0)) if allowed else 0) for k in keys}

    # -- the emulation loop ------------------------------------------------------
    def _triggers_allowed(self) -> bool:
        v = self.values
        if not v.get("Trigger Enable?"):
            return False
        if v.get("Continuous Mode"):
            return True
        return v["# of triggers read"] < v["# of triggers"]

    def _fire_trigger(self, t: float):
        v = self.values
        v["# of triggers read"] += 1
        self._trigger_times.append(t)
        # AO block: consume 2 words per point, remember the last point
        pts = int(v.get("AO # of points per trigger", 0))
        q = self.fifos["Wvfrm2"].q
        for _ in range(pts):
            if len(q) >= 2:
                w0, w1 = q.popleft(), q.popleft()
                self._current_point = self._decode_point(w0, w1)
            else:
                err = dict(v["AO DMA Error"])
                err["Buffer Underflow"] = True
                v["AO DMA Error"] = err
                break
        v["# AO generated"] += pts
        v["AO # points left"] = 0

    def _decode_point(self, w0: int, w1: int) -> np.ndarray:
        def slot(w, k):
            x = (int(w) >> (16 * k)) & 0xFFFF
            return x - 0x10000 if x >= 0x8000 else x
        pt = np.array([slot(w0, 3), slot(w0, 2), slot(w0, 1), slot(w0, 0),
                       slot(w1, 3), slot(w1, 2), slot(w1, 1), slot(w1, 0)], dtype=np.int64)
        mx = self.values["AO Limit Max (counts)"]
        mn = self.values["AO Limit Min (counts)"]
        for i, c in enumerate(AO_LIMIT_CHANNELS):
            pt[2 + i] = min(int(mx[c]), max(int(mn[c]), int(pt[2 + i])))
        return pt

    def _emit_ai(self, t_from: float, t_to: float):
        v = self.values
        nch = int(v.get("AI # of channels", 0))
        period_ticks = int(v.get("AI loop period (ticks)", 100)) or 100
        if nch <= 0 or self._armed_at is None:
            return
        fs = TICKS_PER_S / period_ticks
        n = int((t_to - t_from) * fs + self._ai_phase)
        self._ai_phase = (t_to - t_from) * fs + self._ai_phase - n
        if n <= 0:
            return
        ts = t_from + np.arange(n) / fs
        frames = np.zeros((n, 29), dtype=np.int16)
        frames[:, :8] = np.random.default_rng().integers(-3, 4, size=(n, 8))
        frames[:, 8:14] = self._current_point[2:8]
        if self._trigger_times:
            last = np.searchsorted(np.asarray(self._trigger_times), ts, side="right") - 1
            valid = last >= 0
            dt = np.where(valid, ts - np.asarray(self._trigger_times)[np.clip(last, 0, None)], np.inf)
            up_s = int(v.get("Trigger up (ticks)", 0)) / TICKS_PER_S
            frames[:, 14] = np.where(dt < up_s, 4096, 0)
            frames[:, 15] = np.where(dt < DIO4_HIGH_TICKS / TICKS_PER_S, 4096, 0)
        self.fifos["AI data"].q.extend(int(x) for x in frames[:, :nch].ravel())

    def _loop(self):
        last = time.perf_counter()
        while not self._stop.is_set():
            time.sleep(self.TICK_S)
            now = time.perf_counter()
            with self.lock:
                v = self.values
                # trigger engine
                if self._next_trigger_t is not None:
                    cycle_s = (int(v.get("Cycle(Ticks)", 0)) + 1) / TICKS_PER_S
                    while self._next_trigger_t is not None and self._next_trigger_t <= now:
                        if self._triggers_allowed():
                            self._fire_trigger(self._next_trigger_t)
                        if cycle_s <= 0:
                            self._next_trigger_t = None
                        else:
                            self._next_trigger_t += cycle_s
                # AI stream
                if self.fifos["AI data"].started:
                    self._emit_ai(last, now)
            last = now


class FakeFpgaTriggerController(FpgaTriggerController):
    """FpgaTriggerController on a FakeSession. Same code paths, no RIO."""

    def connect(self):
        self.high_res_timers = _begin_high_resolution_timers()
        self._session = FakeSession()
        self._session.reset()
        self._session.run()
        self.safe_state()

    @property
    def fake(self) -> FakeSession:
        return self._session
