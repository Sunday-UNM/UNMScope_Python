"""FPGA trigger controller -- the DIO4 camera-trigger side of the
hardware abstraction layer, analogous to Camera for the imaging side.

PRAGMATIC DESIGN DECISION (2026-08-29): the FPGA's native multi-trigger
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

import threading
import time

import nifpga

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


class FpgaTriggerError(RuntimeError):
    pass


class FpgaTriggerController:
    """Owns one nifpga.Session."""

    def __init__(self):
        self._session: nifpga.Session | None = None

    # -- lifecycle --------------------------------------------------------
    def connect(self):
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
        regs = self._regs()
        regs["AO Mode"].write(AO_MODE_SET_AO)
        regs["Static AO to set"].write(STATIC_ZERO)
        regs["Set F.P. (T)"].write(True)

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
        for i in range(n_triggers):
            cycle_start = time.monotonic()
            ok = self.fire_single_trigger()
            if not ok:
                break
            fired_count += 1
            if on_progress is not None:
                on_progress(fired_count, n_triggers)
            if i < n_triggers - 1:
                remaining = period_s - (time.monotonic() - cycle_start)
                if remaining > 0:
                    time.sleep(remaining)
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
            window_start = time.monotonic()
            window_count = 0
            while not self._continuous_stop.is_set():
                cycle_start = time.monotonic()
                ok = self.fire_single_trigger()
                if not ok:
                    break
                count += 1
                window_count += 1
                if on_frame is not None:
                    on_frame(count)

                now = time.monotonic()
                if on_rate is not None and now - window_start >= 1.0:
                    on_rate(window_count / (now - window_start))
                    window_start, window_count = now, 0

                # Sleep only the REMAINDER of the period. If firing
                # already overran the period we don't sleep at all --
                # and the achieved rate reported above will show it.
                remaining = period_s - (time.monotonic() - cycle_start)
                if remaining > 0:
                    self._continuous_stop.wait(remaining)

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
        self.stop_continuous()
        if self._session is not None:
            try:
                regs = self._regs()
                regs["Trigger Enable?"].write(False)
                regs["Set F.P. (T)"].write(True)
                self._session.fifos["Wvfrm2"].stop()
            except Exception:
                pass
