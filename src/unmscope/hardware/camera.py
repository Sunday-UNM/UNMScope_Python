"""Camera hardware abstraction.

An ABC (``Camera``) plus a ``SimulatedCamera`` backend (always available,
no hardware needed) and a real ``OrcaFlash4Camera`` backend for the
Hamamatsu Orca Flash 4.0 (S/N 102668, model C11440-42U32 -- USB3 V3, NOT
the older C11440-22CU Camera Link V2 that UNMScope_Source's docs describe;
see ../../docs/fpga_io_map.md) via ``pymmcore-plus``.

IMPORTANT: pymmcore-plus bundles its own MMCore build, which is NOT
binary-compatible with the device adapters shipped with the
already-installed Micro-Manager 1.4/2.0 on this machine. Run ``mmcore
install`` once (the ``mmcore`` CLI that ships with pymmcore-plus) to fetch
a matching adapter set; this module uses
``pymmcore_plus.find_micromanager()`` to locate it automatically.
"""
from __future__ import annotations

import abc
import time
from dataclasses import dataclass

import numpy as np

from unmscope.hardware.roi import (
    DEFAULT_POSITION_UNIT, DEFAULT_SIZE_UNIT, Roi, coerce_roi, full_roi, roi_from_subarray,
    subarray_from_roi,
)


#: Processes that are known to open the Hamamatsu camera through DCAM.
#: Two processes opening DCAM at once is the prime suspect for the
#: dcamapi.dll access violation in docs/known_issues.md.
KNOWN_CAMERA_HOLDERS = ("LouisXIV.exe", "LabVIEW.exe", "LouisXIV-RemoteAcquisitionNode.exe")


def other_camera_holders() -> list[str]:
    """Names of running processes that may already hold the camera.

    Best-effort (Windows `tasklist`); returns [] if that cannot be run.
    Meant for a warning before connect, not as a hard gate -- LouisXIV can
    be open without having connected to the camera.
    """
    import subprocess
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
                             text=True, timeout=5, creationflags=0x08000000).stdout
    except Exception:
        return []
    running = {line.split('","')[0].strip('"').lower() for line in out.splitlines() if line.startswith('"')}
    return [name for name in KNOWN_CAMERA_HOLDERS if name.lower() in running]


class CameraError(RuntimeError):
    """Raised for camera connect/acquire failures."""


@dataclass
class CameraInfo:
    name: str
    serial: str
    width: int
    height: int


class Camera(abc.ABC):
    """Abstract camera interface. All hardware backends (real or
    simulated) implement this so the rest of the app never talks to a
    vendor SDK directly."""

    #: True for a physical camera wired to the FPGA's DIO4: it sees every
    #: real edge, including the one an FPGA reset() emits. False for the
    #: SimulatedCamera, which is fed edges in software (external_trigger()).
    reacts_to_dio4: bool = False

    #: Fallback sensor readout time in ms, used only when the backend
    #: cannot report its own (see readout_ms()). In external EDGE-trigger
    #: mode the minimum frame period is exposure + readout: a trigger
    #: arriving sooner is silently ignored by the camera, so this is what
    #: the FPGA trigger period has to be derived from.
    READOUT_MS: float = 10.0

    def readout_ms(self) -> float:
        """Current sensor readout time in ms. Real backends query the
        camera (it depends on scan mode, binning, ROI); the default is the
        class constant."""
        return self.READOUT_MS

    # -- External-trigger "active" mode ------------------------------------
    # EDGE: each rising edge starts one exposure of the SET exposure time;
    #       the camera is busy for exposure + readout, so a trigger that
    #       lands sooner is silently ignored.
    # SYNCREADOUT (what LouisXIV uses for this Orca -- SPIMProject.ini
    #       'Sync Readout = TRUE'): each edge ENDS the running exposure,
    #       starts its readout and immediately starts the next exposure. The
    #       trigger interval IS the exposure; the exposure setting is ignored;
    #       frame time = period; and the FIRST trigger reads out whatever had
    #       been exposing since the sequence started -- a warm-up frame of
    #       undefined exposure that must be discarded.
    TRIGGER_EDGE = "EDGE"
    TRIGGER_SYNCREADOUT = "SYNCREADOUT"
    #: Orca Flash 4.0 manual (external trigger, standard scan): EDGE needs
    #: exposure + Vn/2*1H + 10*1H between triggers; SYNCREADOUT needs
    #: Vn/2*1H + 18*1H. LabVIEW's 'Orca4.0 - Calculate EdgeTrigger /
    #: SyncReadout Exposure Time.vi' use exactly these. Vn/2*1H is what the
    #: camera reports as ReadoutTime, so 1H = readout / (height/2).
    #: MEASURED 2026-09-03 (spikes/19): EDGE at +0 ms dropped every second
    #: trigger, +0.5 ms none (formula: +0.33); SYNCREADOUT at readout+0.5 ms
    #: dropped 7/20, +0.7 ms none (formula: +0.59). SAFETY_MARGIN_MS sits on
    #: top of the formula so both land above the values measured to work.
    EDGE_EXTRA_LINES = 10
    SYNCREADOUT_EXTRA_LINES = 18
    SAFETY_MARGIN_MS = 0.2

    def line_time_ms(self) -> float:
        """One sensor line time (1H). ReadoutTime covers height/2 lines
        (two readout ports, centre-out)."""
        info = self.info
        h = info.height if info is not None else 2048
        return self.readout_ms() / max(1, h // 2)

    def edge_margin_ms(self) -> float:
        return self.EDGE_EXTRA_LINES * self.line_time_ms() + self.SAFETY_MARGIN_MS

    def syncreadout_margin_ms(self) -> float:
        return self.SYNCREADOUT_EXTRA_LINES * self.line_time_ms() + self.SAFETY_MARGIN_MS

    def set_trigger_active(self, mode: str) -> None:
        """'EDGE' or 'SYNCREADOUT' (only meaningful with an EXTERNAL source)."""
        if mode not in (self.TRIGGER_EDGE, self.TRIGGER_SYNCREADOUT):
            raise ValueError(f"unknown trigger active mode {mode!r}")
        self._trigger_active = mode

    @property
    def trigger_active(self) -> str:
        return getattr(self, "_trigger_active", self.TRIGGER_EDGE)

    def min_frame_period_ms(self) -> float:
        """Shortest usable trigger-to-trigger period for the CURRENT mode
        and set exposure, without margin. EDGE: exposure + readout.
        SYNCREADOUT: readout (the interval sets the exposure)."""
        if self.trigger_active == self.TRIGGER_SYNCREADOUT:
            return self.readout_ms()
        return self.get_exposure_ms() + self.readout_ms()

    def trigger_period_ms(self, exposure_ms: float) -> float:
        """The FPGA trigger period to use for a desired exposure, in the
        current mode, margin included. In SYNCREADOUT a requested exposure
        below the floor is silently lengthened to the floor -- the actual
        exposure is whatever this returns."""
        if self.trigger_active == self.TRIGGER_SYNCREADOUT:
            return max(exposure_ms, self.readout_ms() + self.syncreadout_margin_ms())
        return exposure_ms + self.readout_ms() + self.edge_margin_ms()

    def closing_triggers(self) -> int:
        """Extra triggers a bounded acquisition of n frames needs on top
        of n. SYNCREADOUT: 1 -- each edge reads out the exposure the
        PREVIOUS edge started, so frame n only comes out on edge n+1
        (MEASURED, spikes/24: 5 triggers -> 4 frames from a fresh
        sequence). EDGE: 0."""
        return 1 if self.trigger_active == self.TRIGGER_SYNCREADOUT else 0

    def prepare_sequence(self) -> int:
        """Put the camera sequence into the state acquisition assumes and
        return how many LEADING frames the caller must discard. The base
        version just makes sure a sequence is running (0 to discard);
        backends override where the hardware needs more."""
        if not self.is_sequence_running():
            self.start_sequence(None)
        return 0

    def discard_buffered_frames(self) -> int:
        """Pop and drop everything currently buffered. Returns the count.
        Used right before arming the FPGA so a leftover frame from an
        earlier run cannot be counted as a triggered one."""
        n = 0
        while self.remaining_image_count() > 0:
            self.pop_image()
            n += 1
        return n

    def repair_exposure_if_lost(self) -> bool:
        """Backends with a known way to lose their exposure override this.
        Returns True if a repair was performed (and settings re-applied)."""
        return False

    # -- ROI / binning / sensor mode: the LouisXIV Camera tab -----------------
    #: LouisXIV's DCAM sensor modes (DCAM - Sensor Mode enum). "Normal Scan"
    #: is the panel default; "Split View" and "Rolling Bottom" are the cases
    #: seen in DCAM - Set Sensor Mode.vi. The enum typedef's full item list is
    #: stored compressed in the .ctl and was not readable this session.
    SENSOR_MODES = ("Normal Scan", "Split View", "Rolling Bottom")
    SENSOR_WIDTH = 2048
    SENSOR_HEIGHT = 2048

    def sensor_size(self) -> tuple[int, int]:
        """Full sensor (hmax, vmax) in unbinned pixels."""
        return (self.SENSOR_WIDTH, self.SENSOR_HEIGHT)

    def roi_units(self) -> tuple[int, int, int, int]:
        """(hposunit, vposunit, hunit, vunit) -- the DCAM subarray steps."""
        return (DEFAULT_POSITION_UNIT, DEFAULT_POSITION_UNIT, DEFAULT_SIZE_UNIT, DEFAULT_SIZE_UNIT)

    def get_roi(self) -> Roi:
        """Current ROI, 1-based inclusive, in unbinned sensor pixels."""
        roi = getattr(self, "_roi", None)
        return roi if roi is not None else full_roi(*self.sensor_size())

    def set_roi(self, roi: Roi) -> Roi:
        """DCAM - Set ROI: coerce (bounds, position units, size units rounded
        Down), apply, and return what was actually set."""
        hmax, vmax = self.sensor_size()
        hpu, vpu, hu, vu = self.roi_units()
        coerced = coerce_roi(roi, hmax, vmax, hposunit=hpu, vposunit=vpu, hunit=hu, vunit=vu)
        self._apply_roi(coerced)
        self._roi = coerced
        return coerced

    def _apply_roi(self, roi: Roi) -> None:
        """Backend hook; the base keeps state only."""

    def get_binning(self) -> int:
        return int(getattr(self, "_binning", 1))

    def set_binning(self, binning: int) -> int:
        """DCAM - Set Binning: only 1, 2 or 4 (symmetric)."""
        b = int(binning) if int(binning) in (1, 2, 4) else 1
        self._apply_binning(b)
        self._binning = b
        return b

    def _apply_binning(self, binning: int) -> None:
        """Backend hook; the base keeps state only."""

    def get_sensor_mode(self) -> str:
        return getattr(self, "_sensor_mode", self.SENSOR_MODES[0])

    def set_sensor_mode(self, mode: str) -> str:
        if mode not in self.SENSOR_MODES:
            raise ValueError(f"unknown sensor mode {mode!r}; one of {self.SENSOR_MODES}")
        self._apply_sensor_mode(mode)
        self._sensor_mode = mode
        return mode

    def _apply_sensor_mode(self, mode: str) -> None:
        """Backend hook; the base keeps state only."""

    def image_size(self) -> tuple[int, int]:
        """(width, height) of the frames the camera delivers now: the ROI
        divided by the binning."""
        roi, b = self.get_roi(), self.get_binning()
        return (max(1, roi.width // b), max(1, roi.height // b))

    @abc.abstractmethod
    def connect(self) -> None: ...

    @abc.abstractmethod
    def disconnect(self) -> None: ...

    @property
    @abc.abstractmethod
    def is_connected(self) -> bool: ...

    @property
    @abc.abstractmethod
    def info(self) -> CameraInfo | None: ...

    @abc.abstractmethod
    def set_exposure_ms(self, exposure_ms: float) -> None: ...

    @abc.abstractmethod
    def get_exposure_ms(self) -> float: ...

    @abc.abstractmethod
    def snap(self) -> np.ndarray:
        """Capture and return a single frame (2D uint16 array)."""
        ...


class SimulatedCamera(Camera):
    """No hardware needed -- generates synthetic frames. Used for GUI/dev
    work and automated tests without touching real hardware."""

    def __init__(self, width: int = 2048, height: int = 2048):
        self.SENSOR_WIDTH, self.SENSOR_HEIGHT = int(width), int(height)
        self._connected = False
        self._exposure_ms = 33.325
        self._width = width
        self._height = height
        self._rng = np.random.default_rng()
        self._trigger_source = "INTERNAL"
        self._trigger_polarity = "NEGATIVE"
        self._seq_running = False
        self._seq_target: int | None = None
        self._seq_count = 0
        self._last_frame_at = 0.0
        # "Simulate on FPGA": frames queued by external_trigger(), and the
        # SYNCREADOUT open-exposure state, mirroring the measured Orca.
        self._ext_pending = 0
        self._exposure_open = False
        self._base: np.ndarray | None = None
        self._frame_index = 0

    def connect(self) -> None:
        self._connected = True
        self._ext_pending = 0
        self._exposure_open = False      # a fresh device has nothing exposing

    def disconnect(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def info(self) -> CameraInfo | None:
        if not self._connected:
            return None
        w, h = self.image_size()
        return CameraInfo(name="Simulated Camera", serial="SIM-0000", width=w, height=h)

    def set_exposure_ms(self, exposure_ms: float) -> None:
        self._exposure_ms = exposure_ms

    def get_exposure_ms(self) -> float:
        return self._exposure_ms

    def snap(self) -> np.ndarray:
        if not self._connected:
            raise CameraError("Camera not connected")
        # Cheap synthetic frame: a Poisson-noise base computed ONCE per
        # size plus a per-frame offset. A fresh 2048x2048 Poisson draw per
        # frame cost ~150 ms and, at 10-30 fps in "simulate on FPGA" mode,
        # starved the GUI thread and the FPGA Scope reader of the GIL.
        w, h = self.image_size()
        shape = (h, w)
        if self._base is None or self._base.shape != shape:
            self._base = self._rng.poisson(lam=200, size=shape).astype(np.uint16)
        self._frame_index += 1
        return self._base + np.uint16(self._frame_index % 64)

    # -- Trigger handling. INTERNAL: frames free-run at the exposure pace
    # (GUI/dev work with no FPGA). EXTERNAL: frames appear ONLY when
    # external_trigger() is called -- in "simulate on FPGA" mode the GUI
    # feeds it from the real FPGA's '# of triggers read' counter, so the
    # whole acquisition flow runs on real hardware timing with no camera.
    def set_trigger_source(self, source: str) -> None:
        self._trigger_source = source

    def set_trigger_polarity(self, polarity: str) -> None:
        self._trigger_polarity = polarity

    def external_trigger(self, n_edges: int = 1) -> int:
        """Simulate n rising edges on the trigger input. Mirrors the Orca
        as measured (spikes/24): EDGE -> one frame per edge; SYNCREADOUT ->
        each edge reads out the exposure the PREVIOUS edge started, so a
        fresh camera gives n-1 frames and an exposure left open by an
        earlier run comes out first as a garbage frame. Returns the number
        of frames queued. Ignored unless a sequence runs in EXTERNAL mode."""
        if not self._seq_running or self._trigger_source != "EXTERNAL":
            return 0
        queued = 0
        for _ in range(int(n_edges)):
            if self.trigger_active == self.TRIGGER_SYNCREADOUT:
                if self._exposure_open:
                    queued += 1
                self._exposure_open = True
            else:
                queued += 1
        self._ext_pending += queued
        return queued

    def start_sequence(self, n_images: int | None = None) -> None:
        """n_images=None -> unbounded (until stop_sequence()). Like the real
        camera, a restart does NOT clear a SYNCREADOUT open exposure."""
        if not self._connected:
            raise CameraError("Camera not connected")
        self._seq_running = True
        self._seq_target = n_images
        self._seq_count = 0
        self._ext_pending = 0
        self._last_frame_at = time.monotonic()

    def remaining_image_count(self) -> int:
        """EXTERNAL: frames queued by external_trigger(). INTERNAL: pace
        simulated frames at the exposure rate -- deliberately NOT a
        constant 1, because the GUI drains in a `while remaining > 0`
        loop and would peg a core."""
        if not self._seq_running:
            return 0
        if self._trigger_source == "EXTERNAL":
            return self._ext_pending
        if self._seq_target is not None and self._seq_count >= self._seq_target:
            return 0
        elapsed_ms = (time.monotonic() - self._last_frame_at) * 1000.0
        return 1 if elapsed_ms >= self._exposure_ms else 0

    def pop_image(self) -> np.ndarray:
        if not self._seq_running:
            raise CameraError("No sequence running")
        if self._trigger_source == "EXTERNAL":
            if self._ext_pending <= 0:
                raise CameraError("No triggered frame pending")
            self._ext_pending -= 1
        self._seq_count += 1
        self._last_frame_at = time.monotonic()
        return self.snap()

    def is_sequence_running(self) -> bool:
        return self._seq_running

    def is_buffer_overflowed(self) -> bool:
        return False  # no real buffer to overflow

    def buffer_capacity(self) -> tuple[int, int]:
        return (0, 0)

    def stop_sequence(self) -> None:
        self._seq_running = False
        self._ext_pending = 0


class OrcaFlash4Camera(Camera):
    """Real Hamamatsu Orca Flash 4.0 (C11440-42U32) via pymmcore-plus +
    the Hamamatsu DCAM Micro-Manager device adapter."""

    reacts_to_dio4 = True
    DEVICE_LABEL = "Camera"
    ADAPTER_MODULE = "HamamatsuHam"
    ADAPTER_DEVICE = "HamamatsuHam_DCAM"

    #: Fallback only -- readout_ms() asks the camera. MEASURED 2026-09-03:
    #: this unit reports 'ReadoutTime' = 33.3 ms at full frame in its
    #: default scan mode (DCAM 'ScanMode' = 1), NOT the 9.7 ms datasheet
    #: figure that used to live here. With a 100 ms exposure and a
    #: 109.7 ms FPGA period the camera silently dropped every second
    #: trigger (47 frames from 92 pulses) -- see docs/trigger_free_run_plan.md.
    READOUT_MS = 33.3

    def readout_ms(self) -> float:
        """The camera's own 'ReadoutTime' (DCAM reports seconds). Depends
        on scan mode / binning / ROI, so always re-read after changing
        those. Falls back to READOUT_MS if the property is unavailable."""
        if self._mmc is None:
            return self.READOUT_MS
        try:
            return float(self._mmc.getProperty(self.DEVICE_LABEL, "ReadoutTime")) * 1000.0
        except Exception:
            return self.READOUT_MS

    def __init__(self):
        self._mmc = None
        self._connected = False
        # Last REQUESTED settings, so repair_exposure_if_lost() can
        # re-apply them after a device re-initialisation.
        self._exposure_requested_ms: float | None = None
        self._trigger_source_requested = "INTERNAL"
        self._trigger_polarity_requested = "NEGATIVE"
        self._trigger_active = self.TRIGGER_EDGE

    def connect(self) -> None:
        from pymmcore_plus import CMMCorePlus, find_micromanager

        adapter_dir = find_micromanager()
        if not adapter_dir:
            raise CameraError(
                "No pymmcore-plus-managed Micro-Manager install found. "
                "Run `mmcore install` first (see docs/fpga_io_map.md)."
            )

        mmc = CMMCorePlus()
        mmc.setDeviceAdapterSearchPaths([adapter_dir])
        try:
            mmc.loadDevice(self.DEVICE_LABEL, self.ADAPTER_MODULE, self.ADAPTER_DEVICE)
            mmc.initializeDevice(self.DEVICE_LABEL)
            mmc.setCameraDevice(self.DEVICE_LABEL)
        except Exception as e:
            raise CameraError(f"Failed to connect to Orca Flash 4.0: {e}") from e

        self._mmc = mmc
        self._connected = True

    def disconnect(self) -> None:
        if self._mmc is not None:
            # Tearing the device down while its acquisition thread is still
            # winding down produced an access violation in the next Qt
            # event-loop pass (2026-09-03, docs/known_issues.md). Stop and
            # wait first, then give the native side a moment.
            try:
                self.stop_sequence()
            except Exception:
                pass
            time.sleep(0.2)
            try:
                self._mmc.unloadAllDevices()
            except Exception:
                pass
        self._mmc = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def info(self) -> CameraInfo | None:
        if not self._connected or self._mmc is None:
            return None
        serial = self._mmc.getProperty(self.DEVICE_LABEL, "CameraID")
        name = self._mmc.getProperty(self.DEVICE_LABEL, "CameraName")
        width = self._mmc.getImageWidth()
        height = self._mmc.getImageHeight()
        return CameraInfo(name=name, serial=serial, width=width, height=height)

    def set_exposure_ms(self, exposure_ms: float) -> None:
        if self._mmc is None:
            raise CameraError("Camera not connected")
        self._exposure_requested_ms = float(exposure_ms)
        self._mmc.setExposure(exposure_ms)

    def get_exposure_ms(self) -> float:
        if self._mmc is None:
            raise CameraError("Camera not connected")
        return self._mmc.getExposure()

    #: DCAM "SENSOR MODE" values for LouisXIV's mode names. UNVERIFIED on
    #: hardware: the Orca Flash 4.0 reports AREA (normal) and PROGRESSIVE
    #: (rolling / light-sheet readout); split view is a separate DCAM
    #: feature on some firmware. Check with getAllowedPropertyValues.
    SENSOR_MODE_VALUES = {"Normal Scan": "AREA", "Rolling Bottom": "PROGRESSIVE", "Split View": "SPLIT VIEW"}

    def _apply_roi(self, roi: Roi) -> None:
        # MMCore's setROI is 0-based (x, y, w, h) in the current binning; the
        # subarray in DCAM - Set ROI is hpos, vpos, hsize, vsize likewise.
        hpos, vpos, hsize, vsize = subarray_from_roi(roi)
        b = self.get_binning()
        self._mmc.setROI(hpos // b, vpos // b, hsize // b, vsize // b)

    def get_roi(self) -> Roi:
        try:
            x, y, w, h = self._mmc.getROI()
            b = self.get_binning()
            return roi_from_subarray(x * b, y * b, w * b, h * b)
        except Exception:
            return super().get_roi()

    def _apply_binning(self, binning: int) -> None:
        self._mmc.setProperty(self.DEVICE_LABEL, "Binning", f"{binning}x{binning}")

    def _apply_sensor_mode(self, mode: str) -> None:
        self._mmc.setProperty(self.DEVICE_LABEL, "SENSOR MODE", self.SENSOR_MODE_VALUES[mode])

    def repair_exposure_if_lost(self, tolerance: float = 0.01) -> bool:
        """Work around a Micro-Manager Hamamatsu adapter quirk.

        MEASURED 2026-09-03 (spikes/21-23): after stopSequenceAcquisition
        the camera's real exposure sometimes drops to 0 and then 3.021 ms,
        the adapter's 'Exposure' property keeps reporting the old value,
        and every later setExposure()/setProperty('Exposure') is silently
        ignored -- a nudge to another value, a snap, and trigger-mode
        toggles all fail to bring it back. The ONLY thing that repairs it
        is unloading and re-initialising the device (~0.5 s). If the
        readback disagrees with the last requested exposure, do exactly
        that, then re-apply exposure + trigger source + polarity.
        Callers avoid needing this by never stopping the sequence between
        acquisitions; it is the safety net for when one was stopped.
        """
        want = self._exposure_requested_ms
        if self._mmc is None or want is None:
            return False
        if self._trigger_active == self.TRIGGER_SYNCREADOUT:
            # The interval sets the exposure; the setting is ignored and
            # may legitimately read back stale. Nothing to repair.
            return False
        got = self.get_exposure_ms()
        if abs(got - want) <= tolerance * want:
            return False
        try:
            if self._mmc.isSequenceRunning():
                self._mmc.stopSequenceAcquisition()
        except Exception:
            pass
        self.disconnect()
        self.connect()
        self._mmc.setExposure(want)
        self._mmc.setProperty(self.DEVICE_LABEL, "TRIGGER SOURCE", self._trigger_source_requested)
        self._mmc.setProperty(self.DEVICE_LABEL, "TriggerPolarity", self._trigger_polarity_requested)
        self._mmc.setProperty(self.DEVICE_LABEL, "TRIGGER ACTIVE", self._trigger_active)
        return True

    def snap(self) -> np.ndarray:
        if self._mmc is None:
            raise CameraError("Camera not connected")
        self._mmc.snapImage()
        return self._mmc.getImage()

    def set_trigger_source(self, source: str) -> None:
        """source: 'INTERNAL' or 'EXTERNAL' (DCAM 'TRIGGER SOURCE' property).
        Default out of the box is INTERNAL; real FPGA-triggered acquisition
        needs EXTERNAL with TRIGGER ACTIVE=EDGE (already the default) --
        see docs/fpga_io_map.md."""
        if self._mmc is None:
            raise CameraError("Camera not connected")
        self._trigger_source_requested = source
        self._mmc.setProperty(self.DEVICE_LABEL, "TRIGGER SOURCE", source)

    def set_trigger_polarity(self, polarity: str) -> None:
        """polarity: 'POSITIVE' or 'NEGATIVE' (DCAM property, exposed by
        the adapter as 'TriggerPolarity'). Default out of the box is
        NEGATIVE; DIO4 ("Cam Ext Trigger Out DO") idles low and pulses
        high, so this needs to be POSITIVE for FPGA-triggered acquisition
        -- matches what UNMScope_Source's DCAM - Set Trigger.vi sets."""
        if self._mmc is None:
            raise CameraError("Camera not connected")
        self._trigger_polarity_requested = polarity
        self._mmc.setProperty(self.DEVICE_LABEL, "TriggerPolarity", polarity)

    def set_trigger_active(self, mode: str) -> None:
        """DCAM 'TRIGGER ACTIVE': 'EDGE' or 'SYNCREADOUT' (adapter also
        offers 'LEVEL', not used). Matches LabVIEW's DCAM - Set Trigger.vi
        "External (Sync)" case: polarity POSITIVE, then TRIGGER ACTIVE =
        SYNCREADOUT (DCAMPROP_TRIGGERACTIVE__SYNCREADOUT = 3)."""
        if self._mmc is None:
            raise CameraError("Camera not connected")
        current = self._mmc.getProperty(self.DEVICE_LABEL, "TRIGGER ACTIVE")
        if current != mode and self._mmc.isSequenceRunning():
            # DCAM refuses to change TRIGGER ACTIVE while capturing
            # ("Cannot set property", spikes/24 E5). The caller's
            # prepare_sequence() restarts the sequence afterwards.
            self.stop_sequence()
        super().set_trigger_active(mode)
        if current != mode:
            self._mmc.setProperty(self.DEVICE_LABEL, "TRIGGER ACTIVE", mode)

    # NOTE on SYNCREADOUT and "warm-up" frames (MEASURED, spikes/24 + GUI):
    # each edge reads out the exposure the previous edge started. From a
    # freshly CONNECTED camera the first trigger only starts an exposure
    # (T triggers -> T-1 frames, no garbage). But an exposure left open by
    # a previous run's last trigger -- or by the DIO4 edge an FPGA reset()
    # produces while the camera is armed -- is handed back by the first
    # trigger as a frame of undefined exposure. Stopping and restarting the
    # sequence does NOT clear it (and once left the camera free-running),
    # so the caller tracks "an exposure is open" itself and discards one
    # leading frame when it is. prepare_sequence() therefore only makes
    # sure the sequence runs (base-class behaviour).

    def get_property(self, name: str) -> str:
        if self._mmc is None:
            raise CameraError("Camera not connected")
        return self._mmc.getProperty(self.DEVICE_LABEL, name)

    # -- Non-blocking arm/wait, for external-trigger verification -------
    # NOTE: do NOT call snap()/snapImage() from a background thread to
    # "wait" for an external trigger on the main thread -- this caused a
    # real access-violation crash (VCRUNTIME140.dll, 0xc0000005) here,
    # almost certainly from the native DCAM/MMCore layer being touched
    # from two threads at once. Use start_sequence()/poll/stop_sequence()
    # instead -- single-threaded, non-blocking, no crash risk.
    def start_sequence(self, n_images: int | None = None) -> None:
        """n_images=None -> MMCore's continuous sequence (until
        stop_sequence()). In EXTERNAL trigger mode frames only come when
        the FPGA fires, so leaving this running between acquisitions is
        free -- and it avoids the exposure-loss quirk described in
        repair_exposure_if_lost()."""
        if self._mmc is None:
            raise CameraError("Camera not connected")
        # Re-assert the requested trigger settings right before capture
        # starts: after a stop/start the adapter has been seen to leave the
        # camera free-running (2026-09-03, SYNCREADOUT, GUI run 3).
        self._mmc.setProperty(self.DEVICE_LABEL, "TRIGGER SOURCE", self._trigger_source_requested)
        self._mmc.setProperty(self.DEVICE_LABEL, "TriggerPolarity", self._trigger_polarity_requested)
        self._mmc.setProperty(self.DEVICE_LABEL, "TRIGGER ACTIVE", self._trigger_active)
        if n_images is None:
            self._mmc.startContinuousSequenceAcquisition(0)
        else:
            self._mmc.startSequenceAcquisition(n_images, 0, True)

    def remaining_image_count(self) -> int:
        if self._mmc is None:
            raise CameraError("Camera not connected")
        return self._mmc.getRemainingImageCount()

    def pop_image(self) -> np.ndarray:
        if self._mmc is None:
            raise CameraError("Camera not connected")
        return self._mmc.popNextImage()

    def is_sequence_running(self) -> bool:
        if self._mmc is None:
            return False
        return self._mmc.isSequenceRunning()

    def is_buffer_overflowed(self) -> bool:
        """True once MMCore's circular buffer has filled.

        This matters: when the GUI can't drain frames as fast as the FPGA
        triggers them, the buffer fills and MMCore STOPS the sequence. No
        exception is raised -- frames simply stop arriving -- so without
        checking this the app just silently freezes mid-acquisition.
        """
        if self._mmc is None:
            return False
        return self._mmc.isBufferOverflowed()

    def buffer_capacity(self) -> tuple[int, int]:
        """(free, total) circular-buffer slots, for diagnostics."""
        if self._mmc is None:
            return (0, 0)
        return (self._mmc.getBufferFreeCapacity(), self._mmc.getBufferTotalCapacity())

    def stop_sequence(self, settle_timeout_s: float = 2.0) -> None:
        """Stop the sequence and wait until the adapter agrees it stopped.
        NOTE: on this adapter a stop can lose the exposure -- see
        repair_exposure_if_lost(). The GUI therefore only stops at
        Disconnect."""
        if self._mmc is None:
            return
        if self._mmc.isSequenceRunning():
            self._mmc.stopSequenceAcquisition()
            deadline = time.monotonic() + settle_timeout_s
            while self._mmc.isSequenceRunning() and time.monotonic() < deadline:
                time.sleep(0.01)
