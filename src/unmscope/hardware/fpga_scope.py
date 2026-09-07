"""FPGA Scope -- the internal digital oscilloscope.

Reads the FPGA's ``AI data`` DMA FIFO, the same stream LouisXIV's
``HHMI - AI buffer.vi`` plots. Every ``AI loop period (ticks)`` the FPGA's
AI loop (``HHMI - FPGA AI Loop.vi``) samples the eight real analog inputs
AND a set of internal signals into ONE I16 array, and
``HHMI - Send AI data to DMA.vi`` streams the first ``AI # of channels``
elements of it. So a frame is ``channels`` consecutive I16 words, and the
column order is fixed by the FPGA build.

DECODED ON HARDWARE 2026-09-03 (spikes/25_ai_fifo_decode.py, deployed
bitfile, 10 Hz free-run trigger as the reference): the layout matches the
source diagrams --

    col  0..7   Connector0/AI0..AI7        analog, counts (+-10 V / 16 bit)
    col  8..13  AO values the engine is putting out: X Galvo, Z Galvo,
                Z Piezo, Dither Galvo, AOTF0, Filter desired
    col 14      Int Sync (Cycle Only)      digital, 0 / 4096
    col 15      Cam Ext Trigger Out DO     DIO4 read back, 0 / 4096
    col 16..28  AOTF0..6, Perfusion?, Shutter, Channel Shutter 0..3
                (digital; all constant 0 in the decode run)

Digital signals are bit-shifted by 12 in the FPGA ("so they display ok on
the buffer graph"), hence 4096 for True. With the trigger at 100 ms the
DIO4 column showed a 0.100 ms high every 100.002 ms (sd 9 us = one 50 us
sample of quantisation) -- that measurement IS the software oscilloscope.

Rates (measured): 20 kS/s x 29 (580k I16/s, backlog 725), 100 kS/s x 29
(2.9M I16/s, backlog 3335) and 200 kS/s x 16 (3.2M I16/s, backlog 3440)
all streamed with no AI error; columns beyond 28 read constant 0, so the
array really is 29 long. The AI loop's own overrun flags are in the ``AI
Error`` register (I/O Error / Buffer Underflow = Late? / DMA Timeout),
read by the reader thread.

Usage (shares the trigger controller's nifpga session):

    ctrl = FpgaTriggerController(); ctrl.connect()
    scope = FpgaScope(ctrl); scope.start()
    ctrl.start_free_run(0.1, 0.08)          # keeps the scope's AI settings
    ...
    snap = scope.snapshot(seconds=1.0)
    stats = scope.trigger_stats()           # period/jitter of DIO4 from the FPGA's own clock
    scope.stop()
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import numpy as np

from unmscope.hardware.fpga_trigger import TICKS_PER_S, FpgaTriggerController

#: Column names of the FPGA's per-sample AI array. Columns 0-15 confirmed on
#: the deployed bitfile; 16-28 in the order of HHMI - FPGA AI Loop.vi's Build
#: Array (every wire pixel-traced in the diagram, 2026-09-05) and of the
#: 'Active Channels' ring of HHMI - AI buffer.vi (LouisXIV's FPGA Scope):
#: 16 AOTF 0, 17 AOTF 1, 18 Perfusion, 19-23 AOTF 2..6, 24 Shutter,
#: 25-28 Channel Shutter 0-3. AOTF 0-6 here are the AO-DMA level fields
#: (numeric), not 0/4096 flags; Perfusion / Shutter / Channel Shutter are.
AI_CHANNEL_NAMES: tuple[str, ...] = (
    "AI0", "AI1", "AI2", "AI3", "AI4", "AI5", "AI6", "AI7",
    "X Galvo (AO)", "Z Galvo (AO)", "Z Piezo (AO)", "Dither Galvo (AO)", "AOTF0 (AO)", "Filter desired",
    "Int Sync (Cycle Only)", "Cam Ext Trigger Out (DIO4)",
    "AOTF0", "AOTF1", "Perfusion?", "AOTF2", "AOTF3", "AOTF4", "AOTF5", "AOTF6",
    "Shutter", "Channel Shutter 0", "Channel Shutter 1", "Channel Shutter 2", "Channel Shutter 3",
)
IDX_INT_SYNC = 14
IDX_DIO4 = 15
#: Digital flags are bit-shifted by 12 in the FPGA: True == 4096.
DIGITAL_TRUE = 4096
DIGITAL_THRESHOLD = DIGITAL_TRUE / 2
#: PCIe-7852R analog input: +-10 V over 16 bits.
AI_VOLTS_PER_COUNT = 10.0 / 32768.0
#: How many of the 29 columns to stream. 16 was chosen from the 2026-09-03
#: decode run, where "columns 16..28 were constant 0" -- but that run was a
#: bare free-run trigger with no AOTF written. In a real acquisition the
#: engine writes the AOTF level registers at arm, and they live at 16, 17
#: and 19..23, so at 16 channels no AOTF signal could EVER reach the scope:
#: they were ticked in the legend and silently drew nothing. 24 covers every
#: column this port names and uses (through AOTF 6, Perfusion included);
#: 24 x 100 kS/s = 2.4M I16/s, against the 2.9M (100 kS/s x 29) that was
#: measured on this hardware with no AI error.
DEFAULT_CHANNELS = 24
#: 400 ticks = 100 kS/s per channel (10 us timing resolution). MEASURED
#: 2026-09-03: the host kept up with 100 kS/s x 29 channels (2.9M I16/s,
#: max backlog 3335 of a 4M-word buffer) and with 200 kS/s x 16 (3.2M
#: I16/s) for 2 s each, no AI errors; at 10 us the 100 ms trigger period
#: measured 100.0000 ms with zero spread.
DEFAULT_PERIOD_TICKS = 400
FIFO_DEPTH_ELEMENTS = 4_000_000


class FpgaScopeError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Pure helpers (unit-tested without hardware)
# --------------------------------------------------------------------------
def digital_edges(col: np.ndarray, threshold: float = DIGITAL_THRESHOLD) -> tuple[np.ndarray, np.ndarray]:
    """Indices of rising and falling edges of a digital column."""
    hi = np.asarray(col) > threshold
    rising = np.flatnonzero(~hi[:-1] & hi[1:]) + 1
    falling = np.flatnonzero(hi[:-1] & ~hi[1:]) + 1
    return rising, falling


@dataclass
class PeriodStats:
    edges: int
    period_ms: float
    period_sd_ms: float
    period_min_ms: float
    period_max_ms: float
    high_ms: float          # mean high time (nan if no complete pulse)
    duty: float
    sample_ms: float        # one AI sample period -- the timing resolution

    @property
    def hz(self) -> float:
        return 1000.0 / self.period_ms if self.period_ms > 0 else 0.0


def measure_period(col: np.ndarray, fs_hz: float, threshold: float = DIGITAL_THRESHOLD) -> PeriodStats | None:
    """Period / high-time statistics of a digital column sampled at fs_hz.
    None if fewer than two rising edges are present."""
    col = np.asarray(col)
    rising, falling = digital_edges(col, threshold)
    if len(rising) < 2:
        return None
    dt_ms = 1000.0 / fs_hz
    per = np.diff(rising) * dt_ms
    highs = []
    for r in rising:
        f = falling[falling > r]
        if len(f):
            highs.append((f[0] - r) * dt_ms)
    return PeriodStats(
        edges=int(len(rising)),
        period_ms=float(per.mean()),
        period_sd_ms=float(per.std()),
        period_min_ms=float(per.min()),
        period_max_ms=float(per.max()),
        high_ms=float(np.mean(highs)) if highs else float("nan"),
        duty=float(np.mean(col > threshold)),
        sample_ms=dt_ms,
    )


class FrameRing:
    """Fixed-capacity ring of frames (rows) with a running total."""

    def __init__(self, capacity: int, channels: int):
        self._buf = np.zeros((capacity, channels), dtype=np.int16)
        self._cap = capacity
        self._head = 0          # next write row
        self.total = 0          # frames ever written
        self._lock = threading.Lock()

    @property
    def channels(self) -> int:
        return self._buf.shape[1]

    @property
    def capacity(self) -> int:
        return self._cap

    def append(self, frames: np.ndarray) -> None:
        n = len(frames)
        if n == 0:
            return
        if n >= self._cap:                       # keep only the newest capacity rows
            frames = frames[-self._cap:]
            n = self._cap
        with self._lock:
            end = self._head + n
            if end <= self._cap:
                self._buf[self._head:end] = frames
            else:
                first = self._cap - self._head
                self._buf[self._head:] = frames[:first]
                self._buf[: n - first] = frames[first:]
            self._head = end % self._cap
            self.total += n

    def latest(self, n: int | None = None) -> np.ndarray:
        """The newest n frames (all buffered if n is None), oldest first."""
        with self._lock:
            have = min(self.total, self._cap)
            if n is None or n > have:
                n = have
            if n == 0:
                return self._buf[:0].copy()
            start = (self._head - n) % self._cap
            if start + n <= self._cap:
                return self._buf[start:start + n].copy()
            return np.concatenate((self._buf[start:], self._buf[: (start + n) % self._cap]))


@dataclass
class ScopeSnapshot:
    frames: np.ndarray          # (n, channels) int16, oldest first
    fs_hz: float
    names: tuple[str, ...]
    end_frame_index: int        # ring.total at snapshot time (for aligning consecutive snapshots)

    @property
    def t_s(self) -> np.ndarray:
        """Time axis in seconds, 0 at the oldest frame."""
        return np.arange(len(self.frames)) / self.fs_hz

    def column(self, name_or_index) -> np.ndarray:
        idx = name_or_index if isinstance(name_or_index, int) else self.names.index(name_or_index)
        return self.frames[:, idx]

    def analog_volts(self, index: int) -> np.ndarray:
        return self.frames[:, index].astype(np.float64) * AI_VOLTS_PER_COUNT

    def digital(self, index: int) -> np.ndarray:
        return self.frames[:, index] > DIGITAL_THRESHOLD


# --------------------------------------------------------------------------
# The scope
# --------------------------------------------------------------------------
class FpgaScope:
    """Streams the FPGA's 'AI data' FIFO into a ring buffer on a
    background thread. Shares the FpgaTriggerController's session; sets the
    controller's ai_* attributes so start_free_run() keeps the stream
    configured across arms."""

    def __init__(self, controller: FpgaTriggerController, channels: int = DEFAULT_CHANNELS,
                 period_ticks: int = DEFAULT_PERIOD_TICKS, buffer_seconds: float = 5.0):
        if channels < 1 or channels > len(AI_CHANNEL_NAMES):
            raise ValueError(f"channels must be 1..{len(AI_CHANNEL_NAMES)}")
        self._ctrl = controller
        self.channels = int(channels)
        self.period_ticks = int(period_ticks)
        self.fs_hz = TICKS_PER_S / self.period_ticks
        self.names = AI_CHANNEL_NAMES[: self.channels]
        self.ring = FrameRing(max(1, int(self.fs_hz * buffer_seconds)), self.channels)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._fifo = None
        # diagnostics
        self.backlog_max = 0
        self.ai_error: dict = {}
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        session = self._ctrl._session
        if session is None:
            raise FpgaScopeError("FPGA not connected")
        if self.running:
            return
        regs = session.registers
        # Tell the controller, so start_free_run() writes the same values.
        self._ctrl.ai_channels = self.channels
        self._ctrl.ai_period_ticks = self.period_ticks
        self._ctrl.ai_free_run = True
        regs["AI # of channels"].write(self.channels)
        regs["AI loop period (ticks)"].write(self.period_ticks)
        regs["Free run"].write(True)
        regs["Set F.P. (T)"].write(True)
        fifo = session.fifos["AI data"]
        fifo.stop()
        fifo.configure(FIFO_DEPTH_ELEMENTS)
        fifo.start()
        self._fifo = fifo
        self.backlog_max = 0
        self.last_error = None
        self._stop.clear()
        self._thread = threading.Thread(target=self._reader, name="fpga-scope-reader", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None
        session = self._ctrl._session
        try:
            if self._fifo is not None:
                self._fifo.stop()
        except Exception:
            pass
        self._fifo = None
        self._ctrl.ai_channels = 0
        self._ctrl.ai_free_run = False
        if session is not None:
            try:
                regs = session.registers
                regs["AI # of channels"].write(0)
                regs["Free run"].write(False)
                regs["Set F.P. (T)"].write(True)
            except Exception:
                pass

    # -- reader thread -------------------------------------------------------
    def _reader(self) -> None:
        fifo = self._fifo
        regs = self._ctrl._session.registers
        n = self.channels
        chunk = n * max(1, int(self.fs_hz * 0.05))      # ~50 ms of frames per read
        carry = np.zeros(0, dtype=np.int16)              # partial frame between reads
        last_err_check = 0.0
        while not self._stop.is_set():
            try:
                avail = fifo.read(0, timeout_ms=0).elements_remaining
                if avail == 0:
                    time.sleep(0.005)
                    continue
                take = min(avail, chunk)
                r = fifo.read(take, timeout_ms=100)
            except Exception as e:
                if not self._stop.is_set():
                    self.last_error = f"FIFO read failed: {e!r}"
                return
            self.backlog_max = max(self.backlog_max, r.elements_remaining)
            words = np.asarray(r.data, dtype=np.int16)
            if len(carry):
                words = np.concatenate((carry, words))
            whole = len(words) - (len(words) % n)
            if whole:
                self.ring.append(words[:whole].reshape(-1, n))
            carry = words[whole:]
            now = time.perf_counter()
            if now - last_err_check > 0.5:
                last_err_check = now
                try:
                    self.ai_error = dict(regs["AI Error"].read())
                except Exception:
                    pass

    def clear(self) -> None:
        """Drop everything buffered (the reader keeps streaming into the
        new ring; swapping the attribute is atomic for the reader)."""
        self.ring = FrameRing(self.ring.capacity, self.channels)

    # -- data access -----------------------------------------------------------
    def snapshot(self, seconds: float | None = None) -> ScopeSnapshot:
        n = None if seconds is None else int(seconds * self.fs_hz)
        frames = self.ring.latest(n)
        return ScopeSnapshot(frames=frames, fs_hz=self.fs_hz, names=self.names, end_frame_index=self.ring.total)

    def trigger_stats(self, seconds: float | None = None, column: int = IDX_DIO4) -> PeriodStats | None:
        """Period/jitter of a digital column (default DIO4, the camera
        trigger) measured on the FPGA's own sample clock."""
        if column >= self.channels:
            raise FpgaScopeError(f"column {column} not streamed (channels={self.channels})")
        snap = self.snapshot(seconds)
        return measure_period(snap.frames[:, column], self.fs_hz)

    def seconds_buffered(self) -> float:
        return min(self.ring.total, self.ring.capacity) / self.fs_hz
