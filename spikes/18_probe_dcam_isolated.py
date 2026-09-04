"""Isolated DCAM camera probe -- see docs/known_issues.md.

Clicking Camera Connect in the GUI on 2026-09-03 killed python.exe with an
access violation (0xc0000005) inside C:\WINDOWS\SYSTEM32\dcamapi.dll. A
native AV cannot be caught by try/except, so the GUI just dies.

This walks the exact connect() sequence step by step IN ITS OWN PROCESS
with flushed output and faulthandler enabled, so whichever DCAM call
faults is identified without taking a GUI down with it. It also lists the
adapter's PRE-init properties -- if one of them selects the camera by
index/serial, that is how to pin the real unit (S/N 102668) instead of
whatever DCAM enumerates first (a ghost C11440-42U, S/N 101983, is also
registered on this PC).

USAGE
-----
    python -u spikes/18_probe_dcam_isolated.py
Nothing else may hold the camera (LouisXIV.exe, the GUI) while it runs.
"""
import faulthandler
import sys

faulthandler.enable()


def step(msg):
    print(f"[STEP] {msg}", flush=True)


step("import pymmcore_plus")
from pymmcore_plus import CMMCorePlus, find_micromanager  # noqa: E402

step("find_micromanager()")
adapter_dir = find_micromanager()
print(f"  adapter_dir = {adapter_dir}", flush=True)

step("CMMCorePlus()")
mmc = CMMCorePlus()
print(f"  MMCore version = {mmc.getVersionInfo()}", flush=True)
print(f"  MMCore API     = {mmc.getAPIVersionInfo()}", flush=True)

step("setDeviceAdapterSearchPaths")
mmc.setDeviceAdapterSearchPaths([adapter_dir])

step("getAvailableDevices('HamamatsuHam')   <-- loads the adapter DLL")
devs = mmc.getAvailableDevices("HamamatsuHam")
print(f"  available devices = {list(devs)}", flush=True)
try:
    print(f"  descriptions      = {list(mmc.getAvailableDeviceDescriptions('HamamatsuHam'))}", flush=True)
except Exception as e:
    print(f"  (descriptions failed: {e})", flush=True)

step("loadDevice('Camera','HamamatsuHam','HamamatsuHam_DCAM')")
mmc.loadDevice("Camera", "HamamatsuHam", "HamamatsuHam_DCAM")

step("pre-init properties (these are what can select WHICH camera)")
for p in mmc.getDevicePropertyNames("Camera"):
    try:
        allowed = list(mmc.getAllowedPropertyValues("Camera", p))
    except Exception:
        allowed = []
    try:
        val = mmc.getProperty("Camera", p)
    except Exception as e:
        val = f"<{e}>"
    print(f"    {p!r} = {val!r}  allowed={allowed}", flush=True)

step("initializeDevice('Camera')   <-- prime suspect; opens DCAM")
mmc.initializeDevice("Camera")

step("setCameraDevice + identity")
mmc.setCameraDevice("Camera")
print(f"  CameraID   = {mmc.getProperty('Camera', 'CameraID')}", flush=True)
print(f"  CameraName = {mmc.getProperty('Camera', 'CameraName')}", flush=True)
print(f"  size       = {mmc.getImageWidth()}x{mmc.getImageHeight()}", flush=True)
print(f"  exposure   = {mmc.getExposure()} ms", flush=True)
for p in ("TRIGGER SOURCE", "TriggerPolarity", "TRIGGER ACTIVE", "Binning"):
    try:
        print(f"  {p:<14} = {mmc.getProperty('Camera', p)!r}", flush=True)
    except Exception as e:
        print(f"  {p:<14} = <{e}>", flush=True)

step("snapImage() + getImage()")
mmc.snapImage()
img = mmc.getImage()
print(f"  frame {img.shape} {img.dtype} min={img.min()} max={img.max()} mean={img.mean():.1f}", flush=True)

step("unloadAllDevices")
mmc.unloadAllDevices()
print("[DONE] probe completed with no crash", flush=True)
