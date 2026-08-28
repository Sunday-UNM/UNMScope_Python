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
