"""FPGA trigger controller -- the DIO4 camera-trigger side of the
hardware abstraction layer, analogous to Camera for the imaging side.

TWO WAYS TO MAKE A PULSE TRAIN LIVE HERE:

1. ``start_free_run()`` / ``stop_free_run()`` -- FPGA-TIMED (2026-09-03).
   Arms the FPGA ONCE and lets its 40 MHz cycle counter free-run the whole
   train, exactly as LabVIEW's ``HHMI - SPIM Start scan.vi`` does. Period
   jitter is then +-1 tick (25 ns) instead of Windows scheduling noise.
   Needs a persistent Wvfrm2 refill thread (the AO engine consumes DMA
   words per trigger and faults on underflow) and a monitor thread that
   reads the FPGA's own trigger counter. See docs/trigger_free_run_plan.md.

2. ``fire_single_trigger()`` looped by ``start_continuous()`` /
   ``fire_burst()`` -- HOST-TIMED (2026-08-29). One full arm->fire->disarm
   cycle per pulse. Fully proven on the oscilloscope and end-to-end with
   the camera, but every pulse edge is placed by a Python thread, so the
   period carries ~0.5-1 ms of host jitter. Kept as the fallback.

RESOLVED 2026-09-03: the native mechanism works -- verified on hardware
(spikes/19_free_run_trigger.py, spikes/20_gui_free_run_headless.py):
bounded bursts exact (20/20, 10/10), continuous 92 triggers in 10 s at
109.7 ms, zero ignored, camera frames == triggers. The earlier failures
had three real causes, none of them a hardware fault: (a) the
Cycle(Ticks) == Trigger up (ticks) race, (b) 'Trigger blast #s' = {1,0}
marks every trigger as a blast trigger, which makes the AO engine use the
'... PB' timing registers that default to 0 -- so its first point was
"Late", which the FPGA reports as 'Buffer Underflow' (it is NOT a FIFO
underflow; see HHMI - AO Check if Error or done.vi), and (c) 'Clear AO
DMA' (AO Mode=3) leaves the engine stuck in 'AO Purging' on this bitfile.

HISTORY of why (2) existed first, kept for context: the FPGA's native multi-trigger
burst mechanism (# of triggers / Trigger #s clusters / Continuous Mode)
was investigated at length -- see docs/fpga_io_map.md "Multi-trigger: the
real mechanism" and "still unresolved" -- and never produced a reliable
N-pulse burst on real hardware, despite finding and fixing several real
bugs along the way (a Cycle(Ticks)/Trigger up (ticks) race condition,
among others). The internal `# of triggers read` counter never advanced
past 0 even when a real pulse fired, and the chain that would explain that
goes deeper than what's captured in the exported VI diagrams.

What IS fully validated, reliable, and proven end-to-end (including
triggering the real camera, confirmed via oscilloscope): a SINGLE trigger
pulse, fired via the sequence in fire_single_trigger() below. So instead
of depending on hardware behavior we don't yet trust, N-frame acquisition
(Z-stack, Continuous) is built as a Python-side LOOP of that one proven
primitive, once per frame -- less elegant than a hardware-native burst,
but only uses the mechanism we actually trust. Revisit the native burst
mechanism later if throughput becomes a real constraint.

Z Galvo/Z Piezo/X Galvo etc. are still held at a fixed all-zero value --
real per-slice waveform content (actual Z stepping, beam sweep) is
separate, not-yet-implemented future work. This module only proves the
trigger-count/continuous mechanism.
"""
from __future__ import annotations

import ctypes
import threading
import time
from dataclasses import dataclass

import nifpga
import numpy as np

BITFILE = r"H:\UNM_Lightsheet\UNMScope_Source\bin\data\SPIMFPGAProject_SPIM_MAIN_VI.lvbitx"
RESOURCE = "RIO0"

AO_MODE_START_RUN_WVFRM = 0
AO_MODE_SET_AO = 2
AO_MODE_CLEAR_AO_DMA = 3

# CORRECTED UNDERSTANDING (found in source -- see docs/fpga_io_map.md):
#   - DIO4 pulse WIDTH is a hardcoded ~100us inside the FPGA's
#     "Generate External Camera Trigger.vi" -- not controlled by any
#     register. This is just the holdoff/repeat-rate config we still set
#     even though we're only ever asking for a single pulse per call.
CYCLE_TICKS = 4_000_000        # ~100ms @ 40MHz
HOLDOFF_TICKS = 400_000        # ~10ms -- must stay well under CYCLE_TICKS
AO_TICKS_BETWEEN_POINTS = 4000
AO_POINTS_PER_TRIGGER = 1
ENABLE_HOLD_S = 0.01           # validated: short hold, one clean pulse

WVFRM_SEED_WORDS = [0] * 48    # validated size for a single-trigger fire
STATIC_ZERO = {
    "X Galvo": 0, "Z Galvo": 0, "Z Piezo": 0,
    "Dither Galvo": 0, "Tiling": 0, "Filter": 0, "AOTF on?": False,
}

DEFAULT_WAIT_READY_TIMEOUT_S = 3.0

# -- Free-run (FPGA-timed) trigger train -------------------------------------
TICKS_PER_S = 40_000_000
AO_MODE_STOP_WVFRM = 1
#: Host-side DMA buffer depth requested via Wvfrm2.configure(). The FPGA
#: side is 16389 deep (from the .lvproj); LabVIEW's Setup AO DMA buffer.vi
#: does the same explicit Configure step.
FIFO_REQUESTED_DEPTH = 16_000
#: Zero words written before AO Mode=0 so the engine never starts empty.
PRESEED_WORDS = 4096
#: The refill thread writes small blocks so a full buffer never blocks it
#: for long; write() blocks until a whole block fits, or this timeout.
REFILL_BLOCK_WORDS = 256
REFILL_WRITE_TIMEOUT_MS = 100
MONITOR_INTERVAL_S = 0.05
#: Minimum Int-Sync high time. DIO4's own pulse is a hardcoded 4000 ticks
#: (100 us) inside the FPGA; the sync pulse must be at least as wide for
#: the edge detector, and must stay well below the cycle.
MIN_TRIGGER_UP_TICKS = 4000
# LabVIEW's 'sync rdout offset' for the Orca is 165 us (6600 ticks) in
# HHMI - Generate trigger settings for FPGA.vi; not used here -- every
# verified pulse used 0 (see docs/trigger_free_run_plan.md).
#: 'AO DMA Timeout (ticks per read)'. Compile-time default is 40 (1 us);
#: LabVIEW's Setup AO DMA buffer.vi writes 100. See start_free_run().
AO_DMA_TIMEOUT_TICKS = 100
U32_MAX = 0xFFFF_FFFF

# -- AO clamp ("simulate on FPGA": nothing may move) --------------------------
#: Channels of the 'AO Limit Max/Min (counts)' clusters (plus the bool
#: 'AOTF on?'). The FPGA range-checks every DMA point and every static
#: value against them (Output DMA.vi / Range Check A0 Values.vi), so
#: 0/0 forces every AO output to 0 V regardless of waveform content.
AO_LIMIT_CHANNELS = ("X Galvo", "Z Galvo", "Z Piezo", "Dither Galvo", "Tiling", "Filter")
AO_LIMIT_FULL_SCALE = 32767          # the compile-time default (docs/fpga_reset_defaults.json)

# -- AOTF (docs/aotf.md) ------------------------------------------------------
#: 'AOTF ch (V)' control / 'AOTF ch out (V)' indicator cluster keys. The
#: four analog AOTF channels leave the breakout box on AO5, AO6, AO7, AO3
#: (docs/fpga_io_map.md pinout); values are DAC counts like every AO.
AOTF_LEVEL_KEYS = ("AOTF ch 0", "AOTF ch 1", "AOTF ch 2", "AOTF ch 3")
AOTF_ZERO = {k: 0 for k in AOTF_LEVEL_KEYS}


def aotf_level_cluster(levels: dict | None) -> dict:
    """'AOTF ch (V)' cluster from {channel index: counts}; unset channels 0."""
    out = dict(AOTF_ZERO)
    for ch, counts in (levels or {}).items():
        key = AOTF_LEVEL_KEYS[int(ch)]
        out[key] = int(max(-32767, min(32767, int(counts))))
    return out


def ao_limits(clamp: bool, limit_counts: int = 0, allow_aotf_gate: bool | None = None) -> tuple[dict, dict]:
    """(AO Limit Max, AO Limit Min) cluster values. clamp=True limits every
    channel to +-limit_counts (0 = nothing can move at all; a small value
    such as 328 = +-100 mV lets waveform SHAPE be observed on the FPGA
    Scope with nothing connected); False = full scale.

    The clusters also carry the 'AOTF on?' permission: the FPGA range-
    checks every point's AOTF gate against Max/Min, so Max=False forces
    the AOTF off whatever the stream says. allow_aotf_gate defaults to
    ``not clamp``; a clamped run may pass True to let the gate through
    with every AOTF level at 0 V (the gate is then visible on the scope
    and nothing can light up)."""
    if allow_aotf_gate is None:
        allow_aotf_gate = not clamp
    if clamp:
        lim = int(abs(limit_counts))
        mx = {c: lim for c in AO_LIMIT_CHANNELS}
        mn = {c: -lim for c in AO_LIMIT_CHANNELS}
    else:
        mx = {c: AO_LIMIT_FULL_SCALE for c in AO_LIMIT_CHANNELS}
        mn = {c: -AO_LIMIT_FULL_SCALE for c in AO_LIMIT_CHANNELS}
    mx["AOTF on?"] = bool(allow_aotf_gate)
    mn["AOTF on?"] = False
    return mx, mn


def pack_ao_word(ch0: int, ch1: int, ch2: int, ch3: int) -> int:
    """Pack four I16 values into one Wvfrm2 I64 word, ch0 in the low 16
    bits (the FPGA splits a word with 'Split I64 into 4xI16.vi'; which
    slot lands on which AO channel is established in spikes/28)."""
    out = 0
    for i, v in enumerate((ch0, ch1, ch2, ch3)):
        out |= (int(v) & 0xFFFF) << (16 * i)
    if out & (1 << 63):
        out -= 1 << 64
    return out


def free_run_timing(period_s: float, exposure_s: float) -> tuple[int, int]:
    """(Cycle(Ticks), Trigger up (ticks)) for a trigger-to-trigger period.

    Cycle(Ticks) is THE period control: the FPGA's Internal Trigger Sync
    counter rolls over every Cycle(Ticks)+1 ticks. Trigger up (ticks) is
    the sync pulse HIGH time; LabVIEW sets it to (cycle - exposure)/2.
    Clamped so 4000 <= up <= cycle - 4000: equal values were the original
    "1-2 pulses then nothing" race (docs/fpga_io_map.md).
    """
    cycle = int(round(period_s * TICKS_PER_S))
    if cycle <= 2 * MIN_TRIGGER_UP_TICKS:
        raise FpgaTriggerError(f"period {period_s*1e3:.3f} ms too short for the FPGA sync counter")
    up = int(round((period_s - exposure_s) / 2.0 * TICKS_PER_S))
    up = max(MIN_TRIGGER_UP_TICKS, min(up, cycle - MIN_TRIGGER_UP_TICKS))
    return cycle, up


@dataclass
class FreeRunStatus:
    """One snapshot from the monitor thread. All FPGA-side numbers are the
    hardware's own indicators, read while armed."""
    t: float                    # seconds since Trigger Enable? went True
    t_last_trigger: float       # t when the monitor last saw the count change (<= 50 ms late)
    triggers_read: int          # '# of triggers read' -- accepted triggers
    triggers_ignored: int
    ao_generated: int           # '# AO generated' -- DMA points consumed
    ao_points_left: int
    ao_waveform_state: int
    ao_wvfrm_ready: bool
    ao_dma_error: dict
    int_cycle_mismatches: int   # samples where Int Cycle Trigger != Int Cycle+Added Trigger
    int_cycle_samples: int
    fifo_empty_remaining: int   # host-buffer free elements at the last refill write
    refill_words_written: int
    refill_timeouts: int

    @property
    def achieved_hz(self) -> float:
        """Rate from the FPGA's own counter. The first edge fires at arm,
        so n triggers span n-1 periods -- measured to the last count
        change, not to now (that would include the wait for the next
        one, and keeps falling after a bounded burst has finished)."""
        if self.triggers_read < 2 or self.t_last_trigger <= 0:
            return 0.0
        return (self.triggers_read - 1) / self.t_last_trigger


class FpgaTriggerError(RuntimeError):
    pass


# -- Timing precision on Windows ------------------------------------------
# MEASURED on this machine (Win 11 26200, CPython 3.11.9), overshoot beyond
# a requested 109.7 ms delay (= 100 ms exposure + Orca readout), 120 reps:
#
#     time.sleep()              mean +0.41 ms   spread  0.7 ms
#     threading.Event.wait()    mean +10.2 ms   spread 15.6 ms
#
# 15.6 ms is the coarse Windows timer tick. Since Windows 10 2004 the timer
# resolution is PER PROCESS: NtQueryTimerResolution can report 1.0 ms
# system-wide while our process still gets the coarse tick for waitable
# objects, because some *other* process is what raised it. Confirmed by
# experiment -- calling timeBeginPeriod(1) ourselves took Event.wait from
# +10.2 ms/15.6 ms spread down to +0.76 ms/1.19 ms spread, and releasing it
# put it straight back.
#
# This mattered: start_continuous() used Event.wait() for its inter-pulse
# delay, so up to ~15.6 ms of quantization landed directly in the
# trigger-to-trigger period -- visible on an oscilloscope as pulse spacing
# that shifts around, and INTERMITTENT because it depends on what else is
# running on the machine.
#
# time.sleep() is immune because CPython 3.11+ implements it with
# CREATE_WAITABLE_TIMER_HIGH_RESOLUTION on Windows. So we wait on a
# deadline using time.sleep() and poll the stop flag, instead of blocking
# in Event.wait(). We also raise our own process's timer resolution, which
# additionally helps Qt's timers in the GUI process.

_TIMER_PERIOD_MS = 1


def _begin_high_resolution_timers() -> bool:
    """Raise THIS process's timer resolution to 1 ms. Idempotent-ish:
    Windows refcounts timeBeginPeriod/timeEndPeriod, and we deliberately
    never release it -- the controller is process-lifetime scoped, and the
    cost (slightly higher idle power) is irrelevant on an instrument PC."""
    try:
        return ctypes.WinDLL("winmm").timeBeginPeriod(_TIMER_PERIOD_MS) == 0
    except Exception:
        return False  # not Windows, or winmm unavailable -- harmless


def _wait_until(deadline: float, stop_event: threading.Event | None = None,
                check_interval_s: float = 0.020) -> bool:
    """Sleep until ``deadline`` (a time.perf_counter() value), staying
    responsive to ``stop_event``. Returns False if stopped early.

    Each iteration recomputes the remaining time from the ABSOLUTE
    deadline, so per-sleep overshoot does not accumulate across chunks --
    the final short sleep lands within ~0.4 ms of the deadline regardless
    of how many chunks preceded it.
    """
    while True:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return True
        if stop_event is not None and stop_event.is_set():
            return False
        time.sleep(min(remaining, check_interval_s))


class FpgaTriggerController:
    """Owns one nifpga.Session."""

    def __init__(self):
        self._session: nifpga.Session | None = None
        #: Whether we successfully raised this process's timer resolution
        #: (set in connect()). False means pulse timing will be coarser.
        self.high_res_timers = False
        # -- AO clamp policy (written at every arm; see set_ao_clamp) --
        self.clamp_ao = False
        self.clamp_counts = 0                    # +-limit while clamped (0 = frozen)
        self.ao_clamped: bool | None = None      # read back after the last write
        self.aotf_gate_allowed = False           # 'AOTF on?' permission in the AO limits
        self.aotf_levels: dict = dict(AOTF_ZERO)  # 'AOTF ch (V)' as last read back
        # -- Wvfrm2 content: a repeating pattern of I64 words (default all-zero) --
        self._pattern = np.zeros(1, dtype=np.int64)
        self._pattern_pos = 0
        # -- AI stream (FPGA Scope) settings, written at every arm --
        self.ai_channels = 0
        self.ai_period_ticks = AO_TICKS_BETWEEN_POINTS
        self.ai_free_run = False
        # -- free-run state --
        self._free_run_active = False
        self._free_run_t0 = 0.0
        self._last_triggers_read = 0
        self._t_last_trigger = 0.0
        self._refill_thread: threading.Thread | None = None
        self._refill_stop = threading.Event()
        self._refill_words = 0
        self._refill_timeouts = 0
        self._refill_error: Exception | None = None
        self._fifo_empty_remaining = -1
        self._monitor_thread: threading.Thread | None = None
        self._monitor_stop = threading.Event()
        self.last_status: FreeRunStatus | None = None
        self.last_error: str | None = None

    # -- lifecycle --------------------------------------------------------
    def connect(self):
        # Do this before any timing-sensitive work -- see the notes above
        # _begin_high_resolution_timers(). Without it this process gets the
        # coarse ~15.6 ms Windows tick and pulse spacing visibly jitters.
        self.high_res_timers = _begin_high_resolution_timers()
        self._session = nifpga.Session(bitfile=BITFILE, resource=RESOURCE)
        # Always reset+run at connect -- a stuck/underflowed AO DMA state
        # can otherwise silently persist across sessions.
        self._session.reset()
        time.sleep(0.3)
        self._session.run()
        time.sleep(0.3)
        self.safe_state()

    def close(self):
        if self._session is not None:
            try:
                self.stop()
            except Exception:
                pass
            try:
                self.safe_state()
            except Exception:
                pass
            self._session.close()
        self._session = None

    @property
    def is_connected(self) -> bool:
        return self._session is not None

    def _regs(self):
        if self._session is None:
            raise FpgaTriggerError("Not connected")
        return self._session.registers

    def safe_state(self):
        """Static AO at 0 on every channel, AOTF gate off, every AOTF level
        at 0 V (a laser can only be lit through set_aotf_levels() + the
        per-point gate of a running waveform)."""
        regs = self._regs()
        regs["AO Mode"].write(AO_MODE_SET_AO)
        regs["Static AO to set"].write(STATIC_ZERO)
        regs["AOTF ch (V)"].write(dict(AOTF_ZERO))
        regs["AOTF level to set"].write(False)
        regs["Set F.P. (T)"].write(True)
        self.aotf_levels = dict(AOTF_ZERO)

    def _write_ao_limits(self, clamp: bool, limit_counts: int, allow_aotf_gate: bool | None = None) -> bool:
        regs = self._regs()
        mx, mn = ao_limits(clamp, limit_counts, allow_aotf_gate)
        regs["AO Limit Max (counts)"].write(mx)
        regs["AO Limit Min (counts)"].write(mn)
        regs["Set F.P. (T)"].write(True)
        self.clamp_ao = bool(clamp)
        self.clamp_counts = int(abs(limit_counts))
        self.aotf_gate_allowed = bool(mx["AOTF on?"])
        got_mx = dict(regs["AO Limit Max (counts)"].read())
        got_mn = dict(regs["AO Limit Min (counts)"].read())
        lim = self.clamp_counts
        self.ao_clamped = bool(clamp) and all(
            got_mx[c] <= lim and got_mn[c] >= -lim for c in AO_LIMIT_CHANNELS) and (
            bool(got_mx["AOTF on?"]) == self.aotf_gate_allowed)
        self._last_ao_limits = (got_mx, got_mn)
        return self.ao_clamped

    def set_ao_clamp(self, clamp: bool, limit_counts: int = 0, allow_aotf_gate: bool | None = None) -> bool:
        """Write the AO output limits: clamp=True limits every AO channel to
        +-limit_counts (default 0: nothing can move, "simulate on FPGA")
        whatever the waveform or static value says, and (unless
        allow_aotf_gate=True) forces the AOTF gate off; False restores full
        scale. Reads the registers back and returns whether the clamp is
        in effect."""
        return self._write_ao_limits(clamp, limit_counts, allow_aotf_gate)

    # -- AOTF levels ----------------------------------------------------------
    def set_aotf_levels(self, levels: dict | None) -> dict:
        """Write 'AOTF ch (V)' = {channel index: counts} (unset channels 0)
        and read it back. The level only reaches the output while the
        per-point 'AOTF on?' gate of the running waveform is True AND the
        AO limits allow the gate (docs/aotf.md)."""
        regs = self._regs()
        cluster = aotf_level_cluster(levels)
        regs["AOTF ch (V)"].write(cluster)
        regs["Set F.P. (T)"].write(True)
        got = dict(regs["AOTF ch (V)"].read())
        self.aotf_levels = got
        return got

    def read_aotf_out(self) -> dict:
        """The FPGA's own 'AOTF ch out (V)' indicator (counts per channel)."""
        return dict(self._regs()["AOTF ch out (V)"].read())

    # -- Wvfrm2 content ----------------------------------------------------------
    def _set_pattern(self, words) -> None:
        pattern = np.asarray([0] if words is None else words, dtype=np.int64).ravel()
        if len(pattern) == 0:
            pattern = np.zeros(1, dtype=np.int64)
        self._pattern = pattern
        self._pattern_pos = 0

    def _next_words(self, n: int) -> list[int]:
        """The next n words of the repeating pattern (advances the cursor)."""
        p = self._pattern
        idx = (self._pattern_pos + np.arange(n)) % len(p)
        self._pattern_pos = int((self._pattern_pos + n) % len(p))
        return p[idx].tolist()

    # -- the one proven primitive: a single trigger pulse ------------------
    def fire_single_trigger(self, wait_ready_timeout_s: float = DEFAULT_WAIT_READY_TIMEOUT_S) -> bool:
        """Fire exactly ONE validated trigger pulse on DIO4. Full
        arm -> fire -> disarm cycle each call (matches the sequence
        confirmed on the oscilloscope and end-to-end with the real
        camera). Returns True if the waveform engine armed OK and the
        pulse was sent; False if it never became ready (not fired)."""
        regs = self._regs()

        regs["Cam Trigger delay (ticks)"].write(0)
        regs["# of triggers"].write(1)
        regs["Continuous Mode"].write(False)
        regs["Cycle(Ticks)"].write(CYCLE_TICKS)
        regs["Trigger up (ticks)"].write(HOLDOFF_TICKS)
        regs["AO Trigger delay (ticks)"].write(0)
        regs["Free run"].write(False)
        regs["AI # of channels"].write(0)
        regs["AI loop period (ticks)"].write(AO_TICKS_BETWEEN_POINTS)
        regs["AO # of points per trigger"].write(AO_POINTS_PER_TRIGGER)
        regs["AO ticks between points"].write(AO_TICKS_BETWEEN_POINTS)
        regs['Shutter Ticks "on" (Ticks)'].write(0)
        on_off_one = {"# on": 1, "# off": 0}
        regs["Trigger #s"].write(on_off_one)
        regs["Trigger stack #s"].write(on_off_one)
        regs["Trigger blast #s"].write(on_off_one)
        regs["Set F.P. (T)"].write(True)

        fifo = self._session.fifos["Wvfrm2"]
        fifo.stop()
        fifo.start()
        fifo.write(WVFRM_SEED_WORDS, timeout_ms=2000)

        regs["AO Mode"].write(AO_MODE_START_RUN_WVFRM)
        regs["Set F.P. (T)"].write(True)
        t0 = time.time()
        ready = False
        while time.time() - t0 < wait_ready_timeout_s:
            ready = regs["AO wvfrm ready"].read()
            if ready:
                break
            time.sleep(0.01)

        fired = False
        if ready:
            regs["Trigger Enable?"].write(True)
            regs["Set F.P. (T)"].write(True)
            time.sleep(ENABLE_HOLD_S)
            regs["Trigger Enable?"].write(False)
            regs["Set F.P. (T)"].write(True)
            fired = True

        fifo.stop()
        regs["AO Mode"].write(AO_MODE_SET_AO)
        regs["Static AO to set"].write(STATIC_ZERO)
        regs["Set F.P. (T)"].write(True)
        return fired

    # =====================================================================
    # FREE-RUN: arm once, FPGA times the train (docs/trigger_free_run_plan.md)
    # =====================================================================
    @property
    def free_run_active(self) -> bool:
        return self._free_run_active

    def _clear_ao_dma(self):
        """AO Mode = 'Clear AO DMA'. NOT used by start_free_run(): on this
        bitfile it leaves the engine stuck in 'AO Purging' (see
        spikes/19b_arm_variants.py). Kept only for manual recovery
        experiments."""
        regs = self._regs()
        regs["AO Mode"].write(AO_MODE_CLEAR_AO_DMA)
        regs["Set F.P. (T)"].write(True)
        time.sleep(0.05)

    # -- refill thread: keeps Wvfrm2 topped up for the whole run ------------
    def _refill_loop(self, fifo):
        block = None
        while not self._refill_stop.is_set():
            if block is None:
                block = self._next_words(REFILL_BLOCK_WORDS)   # keep the same block until it lands
            try:
                remaining = fifo.write(block, timeout_ms=REFILL_WRITE_TIMEOUT_MS)
            except nifpga.FifoTimeoutError:
                # Buffer full -- that is the goal, not a fault. Try again.
                self._refill_timeouts += 1
                continue
            except Exception as e:
                # FIFO stopped underneath us (normal at stop) or a real
                # fault: record and get out rather than spin.
                if not self._refill_stop.is_set():
                    self._refill_error = e
                return
            self._refill_words += REFILL_BLOCK_WORDS
            block = None
            self._fifo_empty_remaining = remaining
            if remaining < 2 * REFILL_BLOCK_WORDS:
                time.sleep(0.02)  # nearly full; no need to hammer the driver

    def _start_refill(self, fifo):
        self._refill_stop.clear()
        self._refill_words = 0
        self._refill_timeouts = 0
        self._refill_error = None
        self._fifo_empty_remaining = -1
        self._refill_thread = threading.Thread(target=self._refill_loop, args=(fifo,),
                                               name="fpga-ao-refill", daemon=True)
        self._refill_thread.start()

    def _stop_refill(self):
        self._refill_stop.set()
        if self._refill_thread is not None:
            self._refill_thread.join(timeout=2.0 * REFILL_WRITE_TIMEOUT_MS / 1000.0 + 1.0)
        self._refill_thread = None

    # -- monitor thread: read-only, never in the pulse path -------------------
    def read_free_run_status(self) -> FreeRunStatus:
        regs = self._regs()
        mism = 0
        n = 20
        for _ in range(n):
            if regs["Int Cycle Trigger"].read() != regs["Int Cycle+Added Trigger"].read():
                mism += 1
        now = time.perf_counter() - self._free_run_t0
        triggers_read = regs["# of triggers read"].read()
        if triggers_read != self._last_triggers_read:
            self._last_triggers_read = triggers_read
            self._t_last_trigger = now
        return FreeRunStatus(
            t=now,
            t_last_trigger=self._t_last_trigger,
            triggers_read=triggers_read,
            triggers_ignored=regs["# of triggers ignored"].read(),
            ao_generated=regs["# AO generated"].read(),
            ao_points_left=regs["AO # points left"].read(),
            ao_waveform_state=regs["AO Waveform State"].read(),
            ao_wvfrm_ready=regs["AO wvfrm ready"].read(),
            ao_dma_error=dict(regs["AO DMA Error"].read()),
            int_cycle_mismatches=mism,
            int_cycle_samples=n,
            fifo_empty_remaining=self._fifo_empty_remaining,
            refill_words_written=self._refill_words,
            refill_timeouts=self._refill_timeouts,
        )

    def _monitor_loop(self, on_status, on_trigger_count, on_error):
        last_count = None
        error_reported = False
        while not self._monitor_stop.is_set():
            tick = time.perf_counter()
            try:
                st = self.read_free_run_status()
            except Exception as e:
                if not self._monitor_stop.is_set():
                    self.last_error = f"monitor read failed: {e}"
                    if on_error is not None and not error_reported:
                        error_reported = True
                        on_error(self.last_error)
                return
            self.last_status = st
            if on_trigger_count is not None and st.triggers_read != last_count:
                last_count = st.triggers_read
                on_trigger_count(st.triggers_read)
            fault = [k for k, v in st.ao_dma_error.items() if v]
            if fault and not error_reported:
                error_reported = True
                self.last_error = f"AO DMA Error {fault} at t={st.t:.2f}s (triggers read {st.triggers_read})"
                if on_error is not None:
                    on_error(self.last_error)
            if self._refill_error is not None and not error_reported:
                error_reported = True
                self.last_error = f"refill thread died: {self._refill_error!r}"
                if on_error is not None:
                    on_error(self.last_error)
            if on_status is not None:
                on_status(st)
            _wait_until(tick + MONITOR_INTERVAL_S, self._monitor_stop)

    # -- public ------------------------------------------------------------------
    def start_free_run(self, period_s: float, exposure_s: float, n_triggers: int | None = None,
                       cam_trigger_delay_ticks: int = 0, on_status=None, on_trigger_count=None,
                       on_error=None, wait_ready_timeout_s: float = DEFAULT_WAIT_READY_TIMEOUT_S,
                       ao_points_per_trigger: int = AO_POINTS_PER_TRIGGER,
                       ao_ticks_between_points: int = AO_TICKS_BETWEEN_POINTS,
                       write_pb_registers: bool = True,
                       trigger_blast: dict | None = None,
                       ao_dma_timeout_ticks: int = AO_DMA_TIMEOUT_TICKS,
                       ai_channels: int | None = None, ai_period_ticks: int | None = None,
                       ai_free_run: bool | None = None, clamp_ao: bool | None = None,
                       clamp_counts: int | None = None, ao_words=None,
                       trigger_up_ticks: int | None = None,
                       allow_aotf_gate: bool | None = None, aotf_levels: dict | None = None) -> bool:
        """Arm the FPGA once and let it free-run DIO4 at ``period_s``.

        n_triggers=None -> Continuous Mode (until stop_free_run()).
        n_triggers=N    -> bounded: the FPGA stops accepting triggers by
                           itself after N ('Triggers Enabled.vi'), the
                           counter keeps its value until we disarm.

        aotf_levels={channel: counts} sets 'AOTF ch (V)' for the run (the
        output follows the waveform's per-point gate, docs/aotf.md); a
        clamped run always writes 0 V levels. allow_aotf_gate is the
        'AOTF on?' permission in the AO limits (default: not clamp_ao).

        Callbacks run on the monitor thread: on_trigger_count(n) whenever
        '# of triggers read' changes, on_status(FreeRunStatus) every
        MONITOR_INTERVAL_S, on_error(msg) once on the first fault. Returns
        False (and leaves the FPGA in safe state) if the AO engine never
        reported ready.
        """
        if self._free_run_active:
            raise FpgaTriggerError("free run already active")
        regs = self._regs()
        session = self._session
        cycle, up = free_run_timing(period_s, exposure_s)
        if trigger_up_ticks is not None:
            # The Int-Sync HIGH time also gates the AO block after the LAST
            # trigger of a bounded run: once triggers are disabled the block
            # is aborted when Int Sync drops (measured, spikes/29b: 101
            # points = 10.1 ms at Trigger up = 10 ms). Callers streaming a
            # waveform pass block length + margin here.
            up = int(max(MIN_TRIGGER_UP_TICKS, min(int(trigger_up_ticks), cycle - MIN_TRIGGER_UP_TICKS)))
        self.trigger_up_ticks = up
        self.cycle_ticks = cycle
        continuous = n_triggers is None
        self.last_error = None
        self.last_status = None

        # ---- Phase 1: configure everything, then ONE Set F.P. ---------------
        regs["Trigger Enable?"].write(False)
        regs["Cam Trigger delay (ticks)"].write(int(cam_trigger_delay_ticks))
        regs["# of triggers"].write(U32_MAX if continuous else int(n_triggers))
        regs["Continuous Mode"].write(continuous)
        regs["Cycle(Ticks)"].write(cycle)
        regs["Trigger up (ticks)"].write(up)
        regs["AO Trigger delay (ticks)"].write(0)
        # AI (FPGA Scope) stream settings. 'Free run' is the AI flag,
        # unrelated to trigger repetition. Defaults come from the
        # controller's ai_* attributes, which FpgaScope.start() sets, so an
        # arm never silently switches a running scope off.
        if ai_channels is None:
            ai_channels = self.ai_channels
        if ai_period_ticks is None:
            ai_period_ticks = self.ai_period_ticks
        if ai_free_run is None:
            ai_free_run = self.ai_free_run
        regs["Free run"].write(bool(ai_free_run))
        regs["AI # of channels"].write(int(ai_channels))
        regs["AI loop period (ticks)"].write(int(ai_period_ticks))
        regs["AO # of points per trigger"].write(int(ao_points_per_trigger))
        regs["AO ticks between points"].write(int(ao_ticks_between_points))
        if write_pb_registers:
            # The deployed build carries a second "PB" (perfusion-blast)
            # set of AO timing registers that default to 0. A 0 'ticks
            # between points' makes the AO loop's Late? check fire on the
            # very first point (reported as 'Buffer Underflow'). Mirror the
            # normal values so whichever set the engine consults is sane.
            regs["AO # of points per trigger PB"].write(int(ao_points_per_trigger))
            regs["AO ticks between points PB"].write(int(ao_ticks_between_points))
        # LabVIEW's Setup AO DMA buffer.vi writes 100 here (default is 40).
        regs["AO DMA Timeout (ticks per read)"].write(int(ao_dma_timeout_ticks))
        regs['Shutter Ticks "on" (Ticks)'].write(0)
        pass_all = {"# on": 1, "# off": 0}
        regs["Trigger #s"].write(pass_all)
        regs["Trigger stack #s"].write(pass_all)
        regs["Trigger blast #s"].write(dict(trigger_blast) if trigger_blast else pass_all)
        regs["Perfusion stack #s"].write({"# on": 0, "# off": 0})
        # Registers only the DEPLOYED build has. docs/fpga_reset_defaults.json
        # shows these are the compile-time defaults; written explicitly so a
        # previous session cannot leave anything else behind.
        regs["Trigger skips"].write(0)
        regs["Trigger Look for Arm?"].write(False)
        regs["Trigger Check Filter?"].write(False)
        regs["Added time (Ticks)"].write(0)
        regs["Frame index to add time to"].write(0)
        # AO output limits: clamped to 0 in "simulate on FPGA" mode, full
        # scale otherwise -- written EVERY arm so a previous mode cannot
        # leak into this one.
        if clamp_ao is None:
            clamp_ao = self.clamp_ao
        if clamp_counts is None:
            clamp_counts = self.clamp_counts
        regs["Set F.P. (T)"].write(True)
        self._write_ao_limits(clamp_ao, clamp_counts, allow_aotf_gate)
        if clamp_ao and not self.ao_clamped:
            # Refuse to arm: the user's rule for this mode is that nothing
            # may move, and we cannot prove the clamp is in place.
            self.safe_state()
            self.last_error = f"AO clamp did not take: Max/Min={self._last_ao_limits}"
            return False
        # AOTF levels for the run: 0 V whenever clamped (nothing may light
        # up), otherwise what the caller asked for. Read back.
        self.set_aotf_levels({} if clamp_ao else aotf_levels)
        # Waveform content: a repeating pattern of I64 words (all-zero by
        # default = every AO channel at 0 V). See pack_ao_word().
        self._set_pattern(ao_words)

        # ---- Phase 2: AO engine, started once, refilled for the whole run ---
        # NOTE: deliberately NO 'Clear AO DMA' (AO Mode=3) here. Bisected on
        # hardware (spikes/19b_arm_variants.py, 2026-09-03): after that mode
        # write the engine sits with 'AO Purging'=1 and 'AO wvfrm ready'
        # never goes True, refill thread or not. reset()+run() at connect is
        # our recovery path instead.
        fifo = session.fifos["Wvfrm2"]
        fifo.stop()
        fifo.configure(FIFO_REQUESTED_DEPTH)
        fifo.start()
        fifo.write(self._next_words(PRESEED_WORDS), timeout_ms=2000)
        self._start_refill(fifo)
        regs["AO Mode"].write(AO_MODE_START_RUN_WVFRM)
        regs["Set F.P. (T)"].write(True)
        deadline = time.perf_counter() + wait_ready_timeout_s
        ready = False
        while time.perf_counter() < deadline:
            ready = regs["AO wvfrm ready"].read()
            if ready:
                break
            time.sleep(0.001)
        if not ready:
            self._stop_refill()
            fifo.stop()
            self.safe_state()
            self.last_error = "AO wvfrm ready never went True"
            return False

        # ---- Phase 3: arm ONCE, then never touch it again ---------------------
        self._last_triggers_read = 0
        self._t_last_trigger = 0.0
        self._free_run_t0 = time.perf_counter()
        regs["Trigger Enable?"].write(True)
        regs["Set F.P. (T)"].write(True)
        self._free_run_active = True

        # ---- Phase 4: monitor, read-only -------------------------------------
        self._monitor_stop.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, args=(on_status, on_trigger_count, on_error),
            name="fpga-freerun-monitor", daemon=True)
        self._monitor_thread.start()
        return True

    def stop_free_run(self) -> int:
        """Disarm once (LabVIEW's 'Stop all FPGA device waveform' order),
        return the FPGA's final accepted-trigger count."""
        if not self._free_run_active:
            return 0
        regs = self._regs()
        self._monitor_stop.set()
        if self._monitor_thread is not None:
            self._monitor_thread.join(timeout=2.0)
        self._monitor_thread = None
        # The counter resets the moment Trigger Enable? drops -- read first.
        try:
            final = regs["# of triggers read"].read()
        except Exception:
            final = -1
        regs["Trigger Enable?"].write(False)
        regs["Set F.P. (T)"].write(True)
        regs["AO Mode"].write(AO_MODE_STOP_WVFRM)
        regs["Set F.P. (T)"].write(True)
        time.sleep(0.05)
        self._stop_refill()
        try:
            self._session.fifos["Wvfrm2"].stop()
        except Exception:
            pass
        self.safe_state()
        self._free_run_active = False
        return final

    # -- public: N-frame burst (Z-stack), as a loop of the proven primitive
    def fire_burst(self, n_triggers: int, period_s: float = 0.15,
                    on_progress=None) -> int:
        """Fire n_triggers single pulses at a true period of period_s.

        period_s is trigger-to-trigger and must exceed the camera's
        exposure + readout, or the camera drops the ones that arrive
        while it is still busy. Like start_continuous(), this subtracts
        the fire call's own duration rather than sleeping on top of it.
        Returns how many actually fired.
        """
        fired_count = 0
        next_cycle = time.perf_counter()
        for i in range(n_triggers):
            ok = self.fire_single_trigger()
            if not ok:
                break
            fired_count += 1
            if on_progress is not None:
                on_progress(fired_count, n_triggers)
            if i < n_triggers - 1:
                # Absolute schedule, same rationale as start_continuous().
                next_cycle += period_s
                now = time.perf_counter()
                if next_cycle < now:
                    next_cycle = now
                _wait_until(next_cycle)
        return fired_count

    # -- public: continuous mode, as a repeating loop of the same primitive
    def start_continuous(self, period_s: float = 0.15, on_frame=None,
                         on_rate=None):
        """Fire single pulses at a true PERIOD of period_s until stopped.

        period_s is trigger-to-trigger, NOT the gap between calls. That
        distinction was a real bug: fire_single_trigger() itself costs
        ENABLE_HOLD_S plus a pile of PCIe register writes, and the old
        code slept the full delay ON TOP of that. With a 100ms exposure
        the requested period was 150ms but the achieved period was ~165ms
        (~6Hz instead of the expected ~10Hz) -- and because the overhead
        is fixed, the shorter the exposure the worse the error, which is
        why the frame rate looked like it barely responded to exposure.

        on_frame(count) is called after each fire; on_rate(achieved_hz)
        is called periodically so callers can display/verify the REAL
        rate against a scope instead of trusting the requested one.
        """
        self._continuous_stop = threading.Event()

        def _loop():
            count = 0
            window_start = time.perf_counter()
            window_count = 0
            # ABSOLUTE schedule rather than "sleep the remainder each
            # time". Advancing a deadline by exactly period_s per cycle
            # means a late cycle is followed by a correspondingly shorter
            # wait, so timing error does not accumulate over a long run.
            next_cycle = time.perf_counter()
            while not self._continuous_stop.is_set():
                ok = self.fire_single_trigger()
                if not ok:
                    break
                count += 1
                window_count += 1
                if on_frame is not None:
                    on_frame(count)

                now = time.perf_counter()
                if on_rate is not None and now - window_start >= 1.0:
                    on_rate(window_count / (now - window_start))
                    window_start, window_count = now, 0

                next_cycle += period_s
                # If firing overran the period, resync rather than trying
                # to catch up -- catching up would fire a burst of
                # back-to-back triggers the camera cannot accept, and the
                # achieved rate reported via on_rate already shows the
                # overrun.
                if next_cycle < now:
                    next_cycle = now
                if not _wait_until(next_cycle, self._continuous_stop):
                    break

        self._continuous_thread = threading.Thread(target=_loop, daemon=True)
        self._continuous_thread.start()

    def stop_continuous(self):
        if getattr(self, "_continuous_stop", None) is not None:
            self._continuous_stop.set()
        thread = getattr(self, "_continuous_thread", None)
        if thread is not None:
            thread.join(timeout=5.0)
        self._continuous_thread = None
        self.safe_state()

    def stop(self):
        """Best-effort disarm regardless of current state."""
        if self._free_run_active:
            try:
                self.stop_free_run()
            except Exception:
                pass
        self.stop_continuous()
        if self._session is not None:
            try:
                regs = self._regs()
                regs["Trigger Enable?"].write(False)
                regs["Set F.P. (T)"].write(True)
                self._session.fifos["Wvfrm2"].stop()
            except Exception:
                pass
