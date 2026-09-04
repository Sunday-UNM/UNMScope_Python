# Known issues

## Camera Connect crashes the whole app (dcamapi.dll access violation)

**Status: open, unfixed. Hit on 2026-09-03.**

Clicking **Connect** on the Camera in `python -m unmscope.gui` killed the
entire process. Windows Application Error log:

```
Faulting application name: python.exe, version: 3.11.9150.1013
Faulting module name: dcamapi.dll, version: 24.6.4321.6822
Exception code: 0xc0000005          (access violation)
Faulting module path: C:\WINDOWS\SYSTEM32\dcamapi.dll
```

The fault is inside Hamamatsu's own driver, not our Python. **An access
violation in a native DLL cannot be caught by `try/except`**, which is why
the app dies outright instead of showing "Connection failed" — the
`except Exception` in `on_connect_clicked()` never gets a chance to run.

### Prime suspect: two Hamamatsu cameras are registered

```
Status   Class      FriendlyName              InstanceId
Unknown  USBDevice  Hamamatsu C11440-42U      USB\VID_0661&PID_1426\101983
OK       USBDevice  Hamamatsu C11440-42U32    USB\VID_0661&PID_1452\102668
```

`102668` is our camera (matches the S/N in `SPIMProject.ini`'s
`[Cam1.Camera Settings]`). `101983` is a different unit in "Unknown"
(not-present) state.

`OrcaFlash4Camera.connect()` never says *which* camera to open — it just
does `loadDevice("Camera", "HamamatsuHam", "HamamatsuHam_DCAM")` and takes
whatever DCAM enumerates. Worth checking whether the adapter exposes a
pre-init property to select by serial/index, and pinning it to `102668`.

### Diagnostic ready to run

`scratchpad/probe_dcam.py` was written but never executed. It walks the
connect sequence step by step with flushed output and `faulthandler`
enabled, in its own process, so it identifies exactly which DCAM call
faults (enumeration vs `initializeDevice`) without taking down the GUI.
Recreate it if the scratchpad is gone — the useful part is checking
`getAvailableDevices("HamamatsuHam")` and the pre-init property list
*before* `initializeDevice`.

### Workarounds / notes

- Only one process may hold the camera at a time. Check `LouisXIV.exe` is
  not running before any camera test.
- The `SimulatedCamera` backend is unaffected — use it for GUI work.
- Longer-term fix: run the camera driver in a **subprocess**, so a DCAM
  crash can never take the app down. This also fits the eventual hardware
  process-boundary design.
