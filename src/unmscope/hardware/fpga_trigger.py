"""FPGA trigger controller -- the DIO4 camera-trigger side of the
hardware abstraction layer, analogous to Camera for the imaging side.

Wraps the corrected, oscilloscope-verified register sequence (see
docs/fpga_io_map.md) PLUS the real LabVIEW architecture for anything
longer than a single quick pulse: a persistent background thread that
continuously refills the "Wvfrm2" AO DMA FIFO for as long as an
acquisition runs, mirroring `HHMI - AO Host generation loop.vi` /
`HHMI - Duplicate AO array to fill empty spaces in DMA buffer.vi` in
`FPGA code\\Host to FPGA\\DMA\\AO\\`. A single upfront fifo.write() (what
earlier spikes did) only survives until that data is consumed -- fine for
one quick pulse, but underflows for a real burst or continuous run, which
faults the whole AO waveform engine (confirmed via AO DMA Error / Buffer
Underflow, requiring session.reset() to recover).

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

TRIGGER_UP_TICKS = 4_000_000  # ~100ms @ 40MHz -- oscilloscope-verified pulse width
CYCLE_S = TRIGGER_UP_TICKS / 40e6
AO_TICKS_BETWEEN_POINTS = 4000
AO_POINTS_PER_TRIGGER = 1

REFILL_BLOCK = [0] * 4000       # zero-filled block written repeatedly by the refill thread
REFILL_WRITE_TIMEOUT_MS = 1000  # write() blocks until there's room -- this paces the thread
FIFO_REQUESTED_DEPTH = 16000     # close to the FPGA-side max (16389, from the .lvproj FIFO def);
                                  # matches LabVIEW's explicit Wvfrm2.Configure > Requested Depth step
STATIC_ZERO = {
    "X Galvo": 0, "Z Galvo": 0, "Z Piezo": 0,
    "Dither Galvo": 0, "Tiling": 0, "Filter": 0, "AOTF on?": False,
}


class FpgaTriggerError(RuntimeError):
    pass


class FpgaTriggerController:
    """Owns one nifpga.Session. Not thread-safe to call from multiple
    threads yourself -- the internal refill thread is the only concurrent
    access, which is the same concurrent-access-to-one-session pattern
    LabVIEW's own parallel FPGA host loops use."""

    def __init__(self):
        self._session: nifpga.Session | None = None
        self._refill_thread: threading.Thread | None = None
        self._refill_stop = threading.Event()

    # -- lifecycle --------------------------------------------------------
    def connect(self):
        self._session = nifpga.Session(bitfile=BITFILE, resource=RESOURCE)
        # Always reset+run at connect -- a stuck/underflowed AO DMA state
        # can otherwise silently persist across sessions in the same test.
        self._session.reset()
        time.sleep(0.3)
        self._session.run()
        time.sleep(0.3)
        self.safe_state()

    def close(self):
        self.stop()
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

    def clear_ao_dma_error(self):
        regs = self._regs()
        regs["AO Mode"].write(AO_MODE_CLEAR_AO_DMA)
        regs["Set F.P. (T)"].write(True)
        time.sleep(0.05)

    # -- internal: refill thread ---------------------------------------
    def _refill_loop(self, fifo):
        while not self._refill_stop.is_set():
            try:
                fifo.write(REFILL_BLOCK, timeout_ms=REFILL_WRITE_TIMEOUT_MS)
            except Exception:
                # FIFO stopped/closed underneath us, or a real timeout --
                # either way, stop trying rather than spin/crash.
                return

    def _start_refill(self, fifo):
        self._refill_stop.clear()
        self._refill_thread = threading.Thread(target=self._refill_loop, args=(fifo,), daemon=True)
        self._refill_thread.start()

    def _stop_refill(self):
        self._refill_stop.set()
        if self._refill_thread is not None:
            self._refill_thread.join(timeout=2.0)
        self._refill_thread = None

    # -- configuration ----------------------------------------------------
    def _configure_registers(self, continuous: bool, cam_trigger_delay_ticks: int = 0):
        regs = self._regs()
        regs["Cam Trigger delay (ticks)"].write(cam_trigger_delay_ticks)
        regs["# of triggers"].write(1)
        regs["Continuous Mode"].write(continuous)
        regs["Cycle(Ticks)"].write(TRIGGER_UP_TICKS)
        regs["Trigger up (ticks)"].write(TRIGGER_UP_TICKS)
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

    def _arm(self, continuous: bool) -> bool:
        """Clear any latched error, start the refill thread, start the AO
        waveform engine, wait for ready. Returns True if armed OK."""
        regs = self._regs()
        self.clear_ao_dma_error()

        fifo = self._session.fifos["Wvfrm2"]
        fifo.stop()
        fifo.configure(FIFO_REQUESTED_DEPTH)
        fifo.start()
        # Pre-seed generously before the refill thread takes over, so
        # there's a real cushion (not just one block) before the first
        # scheduled refill needs to land.
        fifo.write(REFILL_BLOCK, timeout_ms=2000)
        fifo.write(REFILL_BLOCK, timeout_ms=2000)
        fifo.write(REFILL_BLOCK, timeout_ms=2000)
        self._start_refill(fifo)

        self._configure_registers(continuous=continuous)
        regs["AO Mode"].write(AO_MODE_START_RUN_WVFRM)
        regs["Set F.P. (T)"].write(True)
        t0 = time.time()
        ready = False
        while time.time() - t0 < 3.0:
            ready = regs["AO wvfrm ready"].read()
            if ready:
                break
            time.sleep(0.01)
        if not ready:
            self._stop_refill()
            fifo.stop()
        return ready

    def _disarm(self, fifo):
        regs = self._regs()
        regs["Trigger Enable?"].write(False)
        regs["Set F.P. (T)"].write(True)
        self._stop_refill()
        fifo.stop()
        self.safe_state()

    # -- public: burst (Z-stack building block) --------------------------
    def fire_burst(self, n_triggers: int, hold_margin_cycles: float = 1.5) -> bool:
        """Fire a burst of n_triggers pulses. Holds Trigger Enable? for
        n_triggers cycles (+ margin) while the background thread keeps the
        AO DMA fed, matching the real LabVIEW architecture. Returns True on
        success (armed + fired without an AO DMA fault)."""
        if not self._arm(continuous=False):
            return False
        fifo = self._session.fifos["Wvfrm2"]
        regs = self._regs()
        hold_s = CYCLE_S * (n_triggers + hold_margin_cycles)
        regs["Trigger Enable?"].write(True)
        regs["Set F.P. (T)"].write(True)
        time.sleep(hold_s)
        ok = not regs["AO DMA Error"].read()["Buffer Underflow"]
        self._disarm(fifo)
        return ok

    # -- public: continuous mode -----------------------------------------
    def start_continuous(self) -> bool:
        if not self._arm(continuous=True):
            return False
        regs = self._regs()
        regs["Trigger Enable?"].write(True)
        regs["Set F.P. (T)"].write(True)
        return True

    def stop_continuous(self):
        fifo = self._session.fifos["Wvfrm2"]
        self._disarm(fifo)

    def stop(self):
        """Best-effort disarm regardless of current state."""
        self._stop_refill()
        if self._session is not None:
            try:
                regs = self._regs()
                regs["Trigger Enable?"].write(False)
                regs["Set F.P. (T)"].write(True)
                self._session.fifos["Wvfrm2"].stop()
            except Exception:
                pass
