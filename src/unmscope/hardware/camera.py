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
from dataclasses import dataclass

import numpy as np


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

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def info(self) -> CameraInfo | None:
        if not self._connected:
            return None
        return CameraInfo(name="Simulated Camera", serial="SIM-0000", width=self._width, height=self._height)

    def set_exposure_ms(self, exposure_ms: float) -> None:
        self._exposure_ms = exposure_ms

    def get_exposure_ms(self) -> float:
        return self._exposure_ms

    def snap(self) -> np.ndarray:
        if not self._connected:
            raise CameraError("Camera not connected")
        base = self._rng.poisson(lam=200, size=(self._height, self._width)).astype(np.uint16)
        return base

    # -- No real trigger input to wait on (no hardware) -- these just
    # record the requested state so GUI code paths that call them don't
    # blow up with SimulatedCamera selected. start_sequence()/pop_image()
    # free-run at whatever pace the GUI polls them, independent of any
    # real trigger -- fine for exercising the GUI/app flow, NOT a stand-in
    # for real FPGA-trigger timing (there is no simulated FPGA).
    def set_trigger_source(self, source: str) -> None:
        self._trigger_source = source

    def set_trigger_polarity(self, polarity: str) -> None:
        self._trigger_polarity = polarity

    def start_sequence(self, n_images: int = 1) -> None:
        if not self._connected:
            raise CameraError("Camera not connected")
        self._seq_running = True
        self._seq_target = n_images
        self._seq_count = 0

    def remaining_image_count(self) -> int:
        if not self._seq_running:
            return 0
        if self._seq_target is not None and self._seq_count >= self._seq_target:
            return 0
        return 1

    def pop_image(self) -> np.ndarray:
        if not self._seq_running:
            raise CameraError("No sequence running")
        self._seq_count += 1
        return self.snap()

    def is_sequence_running(self) -> bool:
        return self._seq_running

    def stop_sequence(self) -> None:
        self._seq_running = False


class OrcaFlash4Camera(Camera):
    """Real Hamamatsu Orca Flash 4.0 (C11440-42U32) via pymmcore-plus +
    the Hamamatsu DCAM Micro-Manager device adapter."""

    DEVICE_LABEL = "Camera"
    ADAPTER_MODULE = "HamamatsuHam"
    ADAPTER_DEVICE = "HamamatsuHam_DCAM"

    def __init__(self):
        self._mmc = None
        self._connected = False

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
        self._mmc.setExposure(exposure_ms)

    def get_exposure_ms(self) -> float:
        if self._mmc is None:
            raise CameraError("Camera not connected")
        return self._mmc.getExposure()

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
        self._mmc.setProperty(self.DEVICE_LABEL, "TRIGGER SOURCE", source)

    def set_trigger_polarity(self, polarity: str) -> None:
        """polarity: 'POSITIVE' or 'NEGATIVE' (DCAM property, exposed by
        the adapter as 'TriggerPolarity'). Default out of the box is
        NEGATIVE; DIO4 ("Cam Ext Trigger Out DO") idles low and pulses
        high, so this needs to be POSITIVE for FPGA-triggered acquisition
        -- matches what UNMScope_Source's DCAM - Set Trigger.vi sets."""
        if self._mmc is None:
            raise CameraError("Camera not connected")
        self._mmc.setProperty(self.DEVICE_LABEL, "TriggerPolarity", polarity)

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
    def start_sequence(self, n_images: int = 1) -> None:
        if self._mmc is None:
            raise CameraError("Camera not connected")
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

    def stop_sequence(self) -> None:
        if self._mmc is None:
            return
        if self._mmc.isSequenceRunning():
            self._mmc.stopSequenceAcquisition()
