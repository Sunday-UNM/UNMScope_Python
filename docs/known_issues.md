# Known issues

## Camera Connect crashes the whole app (dcamapi.dll access violation)

**Status: hit once on 2026-09-03; NOT reproducible on 2026-09-03 (later
session) -- six consecutive clean opens, see below. Left open because a
one-off native crash with no reproduction is not "fixed".**

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

### Follow-up, 2026-09-03 (later session): could not reproduce

Ran with the camera and FPGA physically connected and nothing else
holding them (no `LouisXIV.exe`, no LabVIEW):

| Test | Result |
|---|---|
| `spikes/18_probe_dcam_isolated.py` (bare pymmcore-plus, step by step) x4 | all clean: opens S/N 102668, snaps a 2048x2048 frame |
| `spikes/18b_gui_connect_path_isolated.py` (the real `MainWindow.on_connect_clicked()`, Qt loaded) x2 | clean |
| same, with the FPGA connected first (nifpga + DCAM in one process) | clean |

Two things learned:

- The `HamamatsuHam_DCAM` adapter exposes **no pre-init property that
  selects a camera** (only the four `Transpose*` flags), so "pin it to
  S/N 102668" is not possible through this adapter. DCAM enumerated the
  real camera first every time regardless of the ghost `101983` entry.
- Because the exact same code path is clean today, the most likely cause
  on 2026-09-03 was **contention**: the user had been running
  `LouisXIV.exe` for GUI comparison that day, and a second process
  opening DCAM while LabVIEW held (or had not fully released) the camera
  is the classic way to get an AV inside `dcamapi.dll`. Not proven.

Practical rule until the driver moves to a subprocess: **make sure
`LouisXIV.exe` is closed, or at least not connected to the camera, before
pressing Connect** in the Python GUI. The GUI now logs a warning when it
sees `LouisXIV.exe` running at connect time.

### Workarounds / notes

- Only one process may hold the camera at a time. Check `LouisXIV.exe` is
  not running before any camera test.
- The `SimulatedCamera` backend is unaffected — use it for GUI work.
- Longer-term fix: run the camera driver in a **subprocess**, so a DCAM
  crash can never take the app down. This also fits the eventual hardware
  process-boundary design.

## Camera Connect crash: a second click during a slow DCAM open (2026-09-04)

**Status: FIXED in the repo 2026-09-04. Regression tests:
`tests/test_connect_reentrancy.py`.**

The 2026-09-03 "dcamapi.dll access violation on Connect" happened again,
and this time the log shows how: the first Connect click blocked the GUI
thread inside the DCAM initialisation for ~10 s with no result; a second
Connect click was dispatched RE-ENTRANTLY on the same thread from inside
that call (a native message pump inside the driver lets Qt deliver the
queued click); the nested open finished and logged "Camera connected",
control returned into the first, half-trampled initialisation, and the
process died there (`camera.py connect()` / `initializeDevice`).

**The guard** (`MainWindow._begin_blocking` / `_end_blocking`). One
`self._blocking_op` flag names the blocking driver call that owns the GUI
thread, or None. All four handlers -- camera and FPGA, Connect and
Disconnect -- claim it first and return immediately (with a log line) if
it is already held, so a re-entered call never reaches any hardware. The
flag, not the greyed button, is what closes the hole: disabling a
`QPushButton` stops that button's own click, but the flag also covers the
other three buttons, a queued event, a window close, and a script calling
a handler directly. Alongside it:

- every connection control is disabled and repainted, and the wait cursor
  set, BEFORE the blocking call -- a live-looking button for ten seconds
  is what makes people click twice;
- the camera object is built locally and published to `self.camera` only
  after `connect()` returns, so nothing re-entrant can ever find a
  half-initialised one on the window;
- `_end_blocking()` runs in a `finally` and restores every button from the
  real state, so a failed connect re-enables itself;
- `closeEvent` refuses a close while a blocking call is in flight --
  tearing the hardware down from inside a nested driver call is the same
  fault.

`repaint()` is used rather than `processEvents()` on purpose: it paints
synchronously without dispatching input, so it cannot re-deliver the very
click being guarded.

**The same guard on Acquire/Stop.** `_start_acquisition` runs its own
chain of blocking DCAM calls -- `set_trigger_source`, `set_exposure_ms`,
`repair_exposure_if_lost` (which re-initialises the device in place: the
very call that faults) and `prepare_sequence` -- and only sets
`self.acquiring` at the very end, so a second click during the arm used
to re-enter it and arm the camera and the FPGA twice. The two
`QMessageBox.warning` calls in that path each run a nested event loop,
which is a re-entry vector on its own. `on_acquire_clicked` now takes the
same flag.

`_stop_acquisition` needed a second, separate flag (`_stopping`). It is
reachable from the Stop click, the FPGA error signal, the camera poll
timer's buffer-state check, both disconnect paths and `closeEvent` -- and
a driver pump can deliver any of those from inside another. A nested stop
would disarm an already-disarmed FPGA and re-run the whole teardown, so
it is now a no-op. Measured before the fix: an Acquire click delivered
during a camera Disconnect restarted the run mid-teardown and left
`acquiring` True with `self.camera` set to None.

**What an adversarial review of the guard then turned up** (all fixed, all
with tests):

- **The camera poll timer was still live inside `camera.disconnect()`.**
  `_stop_acquisition` deliberately leaves `camera_poll_timer` running for a
  `2*period + 200 ms` grace window to count in-flight frames, and
  `disconnect()` pumps the native queue -- so the 30 ms tick fired into a
  half-closed device that still reported `is_connected`. This is the most
  likely mechanism for the one-off "access violation right after camera
  Disconnect" recorded below. Fixed on both sides: `_poll_camera_for_frame`
  returns early while `_blocking_op` is set, and `_disconnect_camera` stops
  the timer and un-publishes `self.camera` *before* the blocking close --
  the mirror of publishing only after a successful open.
- **A refused close was lost forever.** Nothing re-issued it, so the user's
  X did nothing and they would force-kill the process mid-driver. The
  request is now remembered and re-issued from `_end_blocking`.
- **`_update_connection_buttons` ignored `self.acquiring`**, so the Acquire
  guard's own `_end_blocking` re-enabled the Disconnect buttons that
  `_start_acquisition` had greyed -- letting the camera or the FPGA be
  yanked out from under moving galvos and an open AOTF. All four now stay
  dead for the whole run; only Stop stays live.
- **A raise inside `_begin_blocking` wedged the flag permanently**, which
  with the `closeEvent` guard also made the window unclosable with the
  hardware live. The setup is now `try`/`except`, clearing the flag before
  re-raising.
- **Pumped events reached the driver from other widgets**:
  `on_exposure_changed` (a wheel or arrow on the still-enabled spin box
  wrote to a device being unloaded), `_on_simulation_toggled` (which only
  checked `self.camera is not None`, now always None during an open), and
  the Waveforms tab's "Scope streaming" checkbox (`ScopePanel.set_interactive`).
- A failed open dropped a half-opened handle without closing it, and
  `blockSignals(True)` leaked if `get_exposure_ms()` raised.

Not done: moving the connect off the GUI thread entirely. The window is
still frozen for the ~10 s of a cold DCAM open; it is now merely frozen
safely, with a wait cursor and dead buttons instead of a crash.

## Orca exposure silently lost after stopSequenceAcquisition (adapter quirk)

**Status: worked around 2026-09-03.** Reproduced with
`spikes/21_exposure_after_sequence.py` / `22` / `23`: after a sequence is
stopped (intermittent — happens when no exposure write occurred during the
sequence), `getExposure()` reads 0 then 3.021 ms, the camera really runs
at ~3 ms, the adapter's `Exposure` property still reports the old value,
and every later `setExposure()` / `setProperty('Exposure')` is ignored.
Nudging the value, snapping in INTERNAL mode and toggling `EXPOSURE FULL
RANGE` do nothing; only unload + re-initialise (0.5 s) repairs it.

Workaround in code: the GUI starts the camera sequence once and never
stops it until Disconnect (exposure changes while a sequence runs are
fine), pushes the exposure at every Acquire, and
`OrcaFlash4Camera.repair_exposure_if_lost()` re-initialises the device if
the readback still disagrees.

## Access violation right after camera Disconnect (once, 2026-09-03)

`spikes/20_gui_free_run_headless.py` printed `Windows fatal exception:
access violation` from the Qt event pump immediately after
`on_disconnect_clicked()` + `on_fpga_disconnect_clicked()`, after a run in
which a continuous sequence had been stopped and the device unloaded back
to back. All acquisition results before it were correct. Mitigation:
`OrcaFlash4Camera.disconnect()` now stops the sequence, waits until the
adapter reports it stopped, and sleeps 0.2 s before `unloadAllDevices()`.
Two further full runs after that change were clean. Same family as the
connect-time crash above: a native fault in the DCAM / MMCore layer, not
catchable from Python. The subprocess-driver design remains the real fix.

## Stale frame at the start of an EXTERNAL-trigger sequence

If the FPGA is connected (reset/run) while the Orca is already armed, one
frame lands ~10 ms after the FPGA is armed — no real exposure can do that.
The GUI flushes the buffer before arming and discards anything arriving
within half a trigger period of the arm.

## FPGA `session.reset()` + `run()` puts an edge on DIO4

Measured 2026-09-03 (`spikes/24`, E3): a camera armed in SYNCREADOUT
before `FpgaTriggerController.connect()` afterwards holds an open
exposure; in EDGE mode the same edge showed up as the "stale frame" ~10 ms
after arming (2026-09-03, earlier). Connect the FPGA BEFORE arming the
camera (the GUI's normal order); if the FPGA is (re)connected while the
camera sequence runs, the GUI marks the exposure as open and discards one
leading frame in sync mode.

## `TRIGGER ACTIVE` cannot be changed while the camera sequence runs

DCAM raises `Cannot set property "TRIGGER ACTIVE"` while capturing.
`OrcaFlash4Camera.set_trigger_active()` stops the sequence first when the
mode really changes; the GUI's `prepare_sequence()` restarts it.

## Restarting the sequence in SYNCREADOUT can leave the camera free-running

Seen once (headless GUI run, third acquisition after two stop/start
cycles): frames arrived at ~30 fps with no triggers. A stop/start also
does NOT clear an open exposure. The GUI therefore never restarts the
sequence between acquisitions, and `start_sequence()` re-asserts
TRIGGER SOURCE / polarity / TRIGGER ACTIVE right before capture starts.

## `achieved_hz` under-reads on short bounded runs (poll lag, not the FPGA)

`FreeRunStatus.achieved_hz` divides the trigger count by the host time
between the first and the last observed count change; the last one lags by
up to a status-poll period. Seen 2026-09-05: a 10-trigger run at 100 ms
reported 8.9 Hz while the FPGA's own `int_cycle_mismatches` stayed 0 and 50
triggers averaged 100.6-100.8 ms between count callbacks (the ~0.6 ms is the
same poll bias). Judge the cycle from `int_cycle_mismatches` or the FPGA
scope (Waveforms tab), or over 50+ triggers, not from `achieved_hz` on a
10-trigger run.

## Z stack came up short: the trigger period was the exposure (fixed 2026-09-06)

**Symptom.** Fewer images than the Slices field says, in Z stack mode, on
the real camera. The simulated camera never showed it.

**Reproduced on the Orca, 2026-09-06 (`spikes/36`, `spikes/37`).** It needs
a **sub-array ROI**. With a 512x512 ROI, SYNCREADOUT, 100 ms exposure, 51
slices and the period = exposure (100 ms): **25 of 51 frames -- exactly
half**, the every-second-trigger signature. Same case with the period at
127 ms: 51 of 51. At **full frame** the count did NOT come up short at
100 ms -- 51/51 and 201/201 -- nor at 30 ms or 10 ms exposures (where the
camera's floor raises the period), nor in EDGE mode. The full sweep is in
`spikes/37_zstack_count_sweep_real_orca.result.txt`.

**Cause.** The FPGA trigger period was taken from the camera:
`trigger_period_ms()` = `max(exposure, readout + margin)` in SYNCREADOUT,
which for any realistic exposure is the exposure itself. So the next trigger
landed the instant the previous exposure ended -- zero time for the X galvo
to fly back to its resting position, and no slack at all around the camera.
The 100 ms exposure the panel defaults to gave a 100.000 ms period: 0% slack.
Why the sub-array is what tips it into dropping every other frame, when the
full frame survives the same zero-slack period, is NOT yet established --
the readout of a 512-row sub-array is several times shorter, and the
mechanism is under investigation. What is established is that the cycle
time cures it.

**Separate anomaly seen in the same sweep, open.** The FIRST run after a
mode or ROI change (EDGE 100 ms; the 512x512 ROI at 127 ms) took ~1.3 s per
trigger of wall time for a 127-134 ms period -- about a minute for 51
slices -- yet delivered every frame. Later runs at the same settings ran at
the expected rate. Looks like a wait or timeout in the settings-change path
rather than lost frames; not chased yet.

**What LouisXIV does.** The period is the **Cycle time**, a field the operator
SETS, and it is exposure + flyback. The user's own working values, read off
LouisXIV: Cam exp 0.1 s, Cycle time 0.127 s -- 27 ms of flyback, inside the
10-30% they had found empirically for this hardware. `Time Per Trigger` on
Scan Setup simply follows it. (The 200.084 ms first read off the panel was
Cycle time left at 0.2 against a 0.1 exposure, not a structural factor.)

**Fix.** The period is now the cycle time, with Cycle time editable on the
Low-Level Waveform Config page behind LouisXIV's Custom Cycle Time tick.
Untitcked it computes as exposure x (1 + `DEFAULT_FLYBACK_FRACTION`), 0.27.
The camera's own minimum is kept as a floor, logged when it bites, so a
too-short typed value cannot re-trigger the camera inside its readout.

**History.** The same failure once came from a hard-coded 9.7 ms readout,
which dropped every second trigger (see the comment that used to sit above
this code). Different constant, same mistake: deriving the period from the
camera instead of from the cycle.

## A second way to lose a slice: the stale filter ate the warm-up frame (fixed 2026-09-06)

Found by an adversarially verified code hunt (39 agents, three independent
lenses converging on the same lines) after the cycle-time fix, and
reproduced on the fake backends before it was fixed.

**Mechanism.** `_poll_camera_for_frame` ran two discards in series on each
popped frame: first the *stale pre-trigger* test (any pop less than half a
trigger period after the FPGA was armed), then the *warm-up* count (the
frame `Camera.prepare_sequence` says to drop). In SYNCREADOUT, every run
after the first has an exposure left open by the previous run's closing
edge; the first edge of the new run reads it out as a garbage frame, and the
warm-up count is 1 for exactly that frame. If that garbage frame popped
inside the stale window it was discarded as *stale* -- without touching the
warm-up count -- so the next frame, **real slice 1, was then discarded as
warm-up**. N-1 frames, the completion test never passed, the run ended on
the timeout, and `_on_stack_finished` saw an incomplete stack and silently
did not save it.

**When.** SYNCREADOUT, second or later stack since Connect, and the garbage
frame arriving within half a period of the arm. At 100 ms / 127 ms full
frame the window is 63.5 ms against a ~60-100 ms arrival -- marginal, which
is why the hardware sweep passed. It becomes likely with a longer cycle
time (0.2 s -> 100 ms window) or a sub-array ROI (readout ~8 ms instead of
33, so the garbage frame arrives much sooner).

**Fix.** One physical frame, one discard: a pop classified stale while a
warm-up is pending is charged to the warm-up count. Stale discards are now
counted (`_stale_discarded`) and reported on the Final line, so a short run
can be attributed instead of guessed at.

**Three siblings fixed at the same time, same hunt:**

- The stale window was half the *period*. With a 20 ms exposure and a
  0.6 s Custom Cycle Time that is 300 ms, and the first *real* slice --
  arriving ~exposure + readout after the arm -- was thrown away. The window
  is now capped at half of exposure + readout, which no real frame can beat.
- `repair_exposure_if_lost` re-initialises the camera in place, which leaves
  no exposure open, but `_sync_exposure_open` stayed True, so the next
  SYNCREADOUT run discarded real slice 1 as a warm-up that never came. The
  flag is now cleared on that path.
- `on_exposure_changed` pushed a new exposure to the camera mid-run. The
  trigger period is fixed for the run, so a longer exposure under it makes
  the camera ignore edges. Mid-run edits are now noted and applied at the
  next Acquire.

**Still open from the same hunt:** a multi-second stall of the GUI thread
during a full-frame stack (Calc, a native file dialog) can overflow MMCore's
circular buffer (~31 frames at full frame) and lose frames silently. Not
addressed yet.

## Open: is our cycle time computed the way LouisXIV computes it?

The fix above (period = cycle time; default exposure x 1.27) is verified on
the hardware -- it cures the halved sub-array count. Whether it is LouisXIV's
*rule* is a separate question, and a source read on 2026-09-06 suggests it is
not: LouisXIV appears to use `max(exposure, camera_cycle - 500 ns)` from the
camera's own frame period, with no flyback fraction anywhere, and to write
Cam exp FROM the camera rather than the other way round.

That read is **unverified** -- its adversarial pass died on a usage limit --
so nothing was changed on it. It is written up in full, with frame citations
and the tension it does not yet explain, in `docs/louisxiv_cycle_time_semantics.md`.
Re-run the verify pass before touching the widgets.
