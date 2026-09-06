# UNMScope Python Roadmap

## Status (updated 2026-08-28 — read this first)

Plan below was written camera-first-skeleton-first. In practice, after
this document was written, the user asked to pull real hardware
validation forward instead of finishing the core skeleton first —
**Phase 2's camera item and part of the FPGA item are done, ahead of
Phase 1.** Concretely, as of the end of the 2026-08-28 session:

- **Camera (Hamamatsu Orca Flash 4.0, real hardware) — working.** Connect,
  snap, live view, exposure, external-trigger mode, all through a real
  `Camera` ABC (`src/unmscope/hardware/camera.py`, `pymmcore-plus`-backed)
  and a minimal real GUI (`python -m unmscope.gui`).
- **FPGA trigger pulse (DIO4) — working and verified**, both on an
  oscilloscope and end-to-end triggering the real camera (confirmed frame
  arrival 230ms after the trigger, matching the configured exposure). See
  `docs/fpga_io_map.md` for the corrected register sequence — it's
  substantially more involved than the naive "toggle Trigger Enable?"
  approach this roadmap's Phase 2 item implied.
- **Not done**: the Phase 1 core skeleton itself (asyncio task/queue
  architecture, state machine, pydantic config) — none of that exists yet.
  The camera/FPGA code above is still spike-quality
  (`spikes/`, `tools/fpga_live_panel.py`), not yet integrated into a
  proper architecture. Also not done: static analog voltage output
  verification on a galvo channel (the plan's original "Stage B").
- Full narrative of how we got here: `docs/SESSION_LOG_2026-08-28.md`.

Practical implication for whoever picks this up: don't assume Phase 1
must happen before more hardware work — that assumption already changed
once. Reasonable next steps are either (a) finish Stage B, (b) build the
real Phase 1 skeleton now with two working hardware subsystems already
proven to fold in, or (c) keep going hardware-first (motion stages next).
No decision has been made on which.

## Status update (2026-08-29)

Big milestone: **a real, working Continuous Scan / Z-stack GUI**
(`python -m unmscope.gui`), scoped-down but functionally modeled on
`SPIM MAIN.vi`'s real layout (top bar: Acquire/Stop, mode dropdown,
Status, Exit; left Scan Setup panel; right image display). Confirmed
working end-to-end on real hardware by the user: Continuous Scan free-runs
frames until Stop; Z stack fires exactly the configured number of
triggers, captures that many frames, and auto-returns to Idle. Each
displayed frame has its number burned directly into the image; the
display clears to black when acquisition stops.

**How multi-frame acquisition actually works** (a real pivot from the
original plan): the FPGA's native multi-trigger burst mechanism
(`# of triggers`, `Trigger #s` clusters, `Continuous Mode`) was
investigated at length and never produced a reliable N-pulse burst — see
`docs/fpga_io_map.md` for the full story (a real Cycle(Ticks)/Trigger up
(ticks) race-condition bug was found and fixed along the way, but even
after that, native bursts stayed unreliable). Rather than keep
reverse-engineering it, `src/unmscope/hardware/fpga_trigger.py`'s
`FpgaTriggerController` builds N-frame acquisition as a Python-side loop
of the one primitive that IS fully validated end-to-end (real oscilloscope
+ real camera): `fire_single_trigger()`, called once per frame, with
`start_continuous()`/`fire_burst()` as thin wrappers (continuous = loop
until stopped; Z-stack = loop until a target count, decided on the GUI
thread from a Qt-signal callback — see the module's and `main_window.py`'s
docstrings for the threading discipline this relies on).

There was also a real scare mid-session: several re-tests showed nothing
on the oscilloscope despite internal FPGA diagnostics looking fine. Root
cause turned out to be an external oscilloscope/probe issue (most likely
the wrong BNC on a labeled panel), not the code — resolved by switching to
a sustained DC-voltage test (removes timing sensitivity) and simultaneous
two-channel confirmation. Full resolution story in `docs/fpga_io_map.md`'s
"Oscilloscope discrepancy, resolved" section — worth reading before
trusting or distrusting a future "nothing on the scope" report.

**Discovered along the way, not yet used**: `FPGA code\Host to FPGA\DMA\AI\HHMI - AI buffer.vi`
is a genuine internal software oscilloscope — it reads the FPGA's `AI data`
DMA FIFO, which carries real analog inputs (Connector0/AI0-7) bit-shifted
together with internal digital signals including `D4 Cam Ext Trig Out`
(DIO4 itself), `Int Cycle Trigger`, AOTF states, etc. Confirmed live by
the user (running the VI directly in LabVIEW) showing exactly the expected
clean, synchronized trigger+galvo waveform. This would let Python read a
live internal trigger/waveform trace with **zero external hardware** — a
genuinely valuable future diagnostic tool, but the exact bit-packing
wasn't reverse-engineered (partial lead: `Active Channels` numeric indices
`8,14,15,16,17,9,10,11` were confirmed live via LabVIEW COM automation)
and building it was deliberately deferred in favor of shipping the GUI.

**Still not done**: real Z motion (galvo/piezo stay at a fixed safe value
throughout — Z-stack currently only proves the trigger/frame-count
mechanism, not actual stage stepping), file saving, the Phase 1 core
skeleton (asyncio/state machine/pydantic config), motion stages, adaptive
optics, and everything else in Phase 2+ below.

## Context

`UNMScope_Source` (LabVIEW, ~2,329 relevant VIs) is the current control
software for the "LouisXIV" light-sheet microscope: cameras (Andor /
Hamamatsu Orca), motion stages (Thorlabs / PI), adaptive optics (Imagine
Optics WaveKit), and FPGA/DAQ-driven scanning (NI PCIe-7852R + breakout
box). Full architecture notes and VI block-diagram/front-panel exports for
reference live in `H:\UNM_Lightsheet\VI_Diagrams\` (see its `index.html`).

Goal: replace LouisXIV with a Python application covering the same scope.
The NI FPGA board and breakout box keep being used as-is — Python talks to
the existing compiled bitfile (`UNMScope_Source\bin\data\SPIMFPGAProject_SPIM_MAIN_VI.lvbitx`)
over NI-RIO rather than reimplementing the FPGA logic.

## Ground rules (agreed 2026-08-28)

1. **Incremental, subsystem-by-subsystem rollout.** `UNMScope_Source` /
   LouisXIV stays the daily-driver and stays untouched throughout. Nothing
   in Python replaces a real workflow until it's been validated against
   real hardware. Python and LabVIEW coexist for as long as it takes.
2. **Custom hardware abstraction layer**, tailored to this exact hardware
   set, rather than adopting a general microscopy framework (e.g.
   Pycro-Manager) wholesale — matches the current architecture most
   closely and avoids fighting someone else's abstractions for the unusual
   pieces (adaptive optics, custom FPGA scanning).
3. **Hands-on hardware access is available** for validation as we go —
   each subsystem gets tested on real hardware before being considered
   done, not just simulated.
4. **GUI is PyQt6/PySide6**, but deferred — no GUI work until hardware
   subsystems exist and it's clear what the UI actually needs to expose.
5. This repo (`UNMScope_Python`) is separate from `UNMScope_Source` and
   from `VI_Diagrams` — sibling folders under `H:\UNM_Lightsheet\`, never
   nested inside one another.

## Phase 1 — Core skeleton (no hardware)

**Superseded in part, 2026-09-05.** This was the plan on day one. What
actually got built follows the hardware-abstraction and testing bullets
closely, and deliberately did not follow the other three: there is no
asyncio task/queue layer (Qt's own signals and a worker thread carry the
GUI/acquisition split), no explicit state-machine framework, and config is
plain dataclasses over `configparser` rather than pydantic. The empty
`messaging/` and `state_machine/` packages left over from this plan were
deleted; nothing had ever imported them. Kept below as the record of what
was originally intended.

Port the *architecture*, not any device logic yet, so every later phase
plugs into a working, tested skeleton instead of bolting concurrency and
config handling on after the fact.

- **Concurrency model**: `asyncio` tasks standing in for LabVIEW's parallel
  loops (GUI, Engine, Image Acq, File I/O, Debug), communicating over
  `asyncio.Queue`s — the direct analog of `Common/Main MsgQ` and
  `SPIM LV8.6 VIs/SPIM Inter Loop Messaging`.
- **Hardware abstraction layer**: an ABC per device category (camera,
  stage, filter wheel, AO mirror, FPGA scan engine) *now*, each with a
  `Simulated*` backend, so the skeleton is fully testable with zero
  hardware. Real backends are added per-subsystem in later phases.
- **Config**: typed config (`pydantic`) able to load the existing
  `SPIMProject.ini` / `SPIMProject.cfg` format, so Python reads real
  instrument configs from day one instead of a new format.
- **State machine**: replaces `Common/State Machines/` (queued state
  machine) with an explicit Python state machine per loop/task.
- **Logging + state history**: structured logging replacing
  `Log States with Error.vi` / `State History.vi`.
- **Testing**: `pytest`, run entirely against simulated backends.

## Phase 2+ — Hardware subsystems, one at a time

Each subsystem: build against its `Simulated*` backend first, validate on
real hardware, only then consider it done. Proposed order (lowest-risk /
highest-leverage first, FPGA last since it's the highest-risk integration):

1. Motion / stage control (Thorlabs, PI, Sutter, filter wheels)
2. Camera acquisition (Andor, Hamamatsu Orca / DCAM)
3. Adaptive optics (Imagine Optics WaveKit)
4. FPGA / DAQ scanning (via `nifpga` against the existing bitfile)
5. Distributed multi-node acquisition (if still needed — revisit whether
   the multi-PC/multi-camera architecture is still required)
6. GUI (PyQt6/PySide6)

## Open questions / revisit later

- Whether the distributed (multi-PC) acquisition architecture is still
  needed, or whether a single-PC design now suffices.
- OME-TIFF / data-format compatibility requirements for downstream
  analysis pipelines.
- Final Python package name/structure (currently a placeholder skeleton).

## Status update (2026-09-03) — FPGA-timed free run is in

The trigger train is now timed by the FPGA, not by Python:
`FpgaTriggerController.start_free_run()` arms once and the board's 40 MHz
cycle counter fires every DIO4 edge (LabVIEW's own scheme). Verified on
hardware and through the real GUI: bounded Z-stacks are exact (10/10,
20/20), continuous runs deliver one frame per trigger (39/39, 92 in 10 s
at 109.7 ms), zero ignored triggers, no AO faults. Full story and the
three real causes of the earlier "native bursts are unreliable" verdict:
`docs/trigger_free_run_plan.md` ("Hardware verification, 2026-09-03").

Camera-side corrections that came out of it: the Orca's readout is 33.3 ms
(queried from the camera now, not assumed), the period gets a 0.5 ms
margin, the camera sequence stays armed between acquisitions because
stopping it can silently lose the exposure (adapter quirk, worked around),
and a Stop waits two periods for in-flight frames.

Next candidates: SYNCREADOUT trigger mode (what LouisXIV uses, per
`SPIMProject.ini`), real waveform content on the galvo/piezo channels
(the `Wvfrm2` packing is 4×I16 per I64), the camera driver in a subprocess
(two native AVs seen so far, one at connect, one at disconnect).

## Status update (2026-09-03, later) — SYNCREADOUT trigger mode is in

The camera now runs in DCAM SYNCREADOUT trigger mode by default (the mode
LouisXIV uses per `SPIMProject.ini`): the FPGA trigger interval is the
exposure, so a 100 ms exposure gives 10 fps instead of 7.5, and the floor
is 34.1 ms (29 fps). Period formulas are the Orca manual's line-time ones
(matched by measurement), bounded stacks fire N+1 triggers, and the one
garbage frame the camera hands back when an exposure was left open is
tracked and discarded deterministically. EDGE mode stays available via
the "Sync readout" checkbox. Details: `docs/trigger_free_run_plan.md`,
"SYNCREADOUT trigger mode".

---

## NEXT PHASE (planned 2026-09-03): FPGA-only — "Simulate on FPGA" mode + FPGA Scope

**Hard constraint (user):** the ONLY hardware in the loop is the NI
PCIe-7852R FPGA. No camera, no galvos/piezos, no other devices. Every AO
output stays clamped; nothing may move.

### A. FPGA Scope — an internal digital oscilloscope

LouisXIV's `HHMI - AI buffer.vi` reads the FPGA's `AI data` DMA FIFO
(I16, FPGA→host), which carries the real analog inputs AI0–7 packed
together with internal digital signals — DIO4 `D4 Cam Ext Trig Out`
itself, `Int Cycle Trigger`, AOTF states. The user has seen it show a
clean, synchronised trigger + galvo trace. Reading and decoding that
stream in Python gives a software oscilloscope with the FPGA's own sample
clock, and replaces the physical scope for all timing verification.

**Reference signal for decoding:** the free-run trigger — the one signal
we can generate deterministically with the FPGA alone. Known period
(`Cycle(Ticks)`), known width (4000 ticks = 100 µs), known count.

Steps:
1. Read the FPGA AI VIs (`HHMI - FPGA AI Loop`, `HHMI - Send AI data to
   DMA`) and the host side (`HHMI - AI buffer`, `Setup AI DMA buffer`,
   `HHMI - FPGA AI buffer settings functional global`) for the packing
   HYPOTHESIS — the deployed bitfile differs from the source, so the
   hypothesis is tested, never trusted. Known lead: `Active Channels`
   read live from LabVIEW = (8, 14, 15, 16, 17, 9, 10, 11).
2. Spike: write `AI # of channels`, `AI loop period (ticks)`, `Free run`
   (the AI flag), start the `AI data` FIFO, run the free-run trigger at
   10 Hz with all AO at zero, capture a few seconds, and do a bit-plane /
   interleave-slot analysis: the slot+bit that toggles once per
   `Cycle(Ticks)` with a 100 µs high time is DIO4; the once-per-cycle bit
   is `Int Cycle Trigger`; slots with noise around zero are AI0–7.
   Also check `AI Error` (I/O, Buffer Underflow, DMA Timeout) and the
   sustainable sample rate (default `AI loop period` = 100 ticks =
   400 kS/s × channels — probably too much for the host; find the rate).
3. Verify: the decoded DIO4 edge spacing must equal `Cycle(Ticks)+1`
   ticks to within one AI sample period — the software-scope version of
   the oscilloscope check, and the jitter measurement the user asked for
   originally.
4. `src/unmscope/hardware/fpga_scope.py`: `FpgaScope` — start/stop the
   AI stream on the existing `nifpga` session, decode into named channels
   (pinout names from `docs/fpga_io_map.md`), ring buffer, thread-safe
   snapshot for a GUI. Unit tests on synthetic packed buffers.
5. GUI: the Waveforms tab (still a placeholder) gets the live traces,
   modelled on LouisXIV's X Waveform / Full Waveform plots.

### B. "Simulate on FPGA" mode

The GUI's Simulated camera + the REAL FPGA: the FPGA free-runs the
trigger train; the simulated camera emits one synthetic frame per FPGA
trigger (fed from the controller's `# of triggers read` callback, with
the EDGE / SYNCREADOUT accounting), so the whole acquisition flow runs on
real hardware timing without a camera. AO outputs are clamped by writing
`AO Limit Max/Min (counts)` to 0 for every channel at arm (the FPGA
range-checks every DMA point against them — `Range Check A0 Values.vi`)
on top of the all-zero waveform, so nothing can move even when waveform
content is added later. The FPGA Scope is the observer.

Steps:
1. `SimulatedCamera.external_trigger(n)` / trigger-source EXTERNAL:
   frames only when triggered; GUI feeds `on_trigger_count` into it.
2. The existing "Simulation" checkbox (switches the backend) + FPGA
   connected = simulate-on-FPGA mode; log it clearly in the status bar.
3. AO clamp policy in `FpgaTriggerController.start_free_run()`
   (`clamp_ao=True`), written every arm, verified by reading back.
4. Headless test on the FPGA only: Z stack + Continuous with the
   simulated camera; counts exact; FPGA Scope shows the trigger train.

### C. After that

Real waveform content (X galvo sweep, Z step) generated into `Wvfrm2`
words (4×I16 per I64), first observed on the FPGA Scope with AO clamped,
before any galvo is ever connected.

### Status (2026-09-03, evening): A steps 1–4 DONE — the FPGA Scope works

The `AI data` stream decoded on the first capture, exactly as the source
diagrams predict: 29 I16 columns per sample (AI0–7, the six AO values,
`Int Sync`, DIO4 read back, thirteen digital flags), digital signals as
0/4096. At 100 kS/s the DIO4 trigger period reads 100.0000 ms with zero
spread — the trigger is locked to the FPGA clock. `FpgaScope` (ring
buffer, reader thread, shared session, `trigger_stats()`),
`tools/fpga_scope_monitor.py`, unit tests. Details: `docs/fpga_scope.md`.
Step 5 (Waveforms tab) is still open; B (simulate on FPGA) is next.

### Status (2026-09-03, night): B DONE — "simulate on FPGA" works

Simulated camera driven by the real FPGA's trigger counter
(`SimulatedCamera.external_trigger()`, EDGE/SYNCREADOUT accounting as
measured on the Orca), AO clamped to 0 by `AO Limit Max/Min (counts)` at
connect and at every arm with readback verification (arm refused if the
clamp does not take). Headless GUI on the FPGA alone: sync 10/11, 65/66,
10/11; edge 10/10, 62/62, 10/10; limits read 0 throughout. Details:
`docs/simulate_on_fpga.md`. Remaining in this phase: A step 5, the
Waveforms tab fed by `FpgaScope`.

### Status (2026-09-03, night): A step 5 DONE — the phase is complete

The Waveforms tab is LouisXIV's FPGA Scope fed by `FpgaScope`: black grid
graph in volts vs time, per-channel legend, seconds-to-buffer, Clear,
points counter, DIO4 period/jitter status line. Starts with the FPGA
connection, verified headlessly on the FPGA alone
(`spikes/27_gui_waveforms_scope_headless.py`, rendered tab in
`docs/waveforms_tab_fpga_scope.png`). Not pixel-matched to the live
LabVIEW panel (reference not running).

**Next: C — real waveform content, FPGA only.** Generate the AO words
(4×I16 per I64, X galvo sweep / Z step) into `Wvfrm2`, observe them on the
FPGA Scope's AO columns. Caveat found while planning: the AI loop samples
`AO DMA` after the range check, so under the 0-count clamp the columns
read 0 regardless — verification needs an explicit small clamp (e.g.
±100 mV) with nothing connected to the AO BNCs, kept OFF by default.

### Status (2026-09-04, early): C DONE — real waveform content, verified on the scope

`Wvfrm2` decoded (2 I64 words per point, high slot first:
`[prefix0, prefix1, X Galvo, Z Galvo | Z Piezo, Dither Galvo, Tiling,
Filter]`, prefixes drive nothing observable), `waveform.py` builds the
LouisXIV-style scan block (X sweep over the exposure + flyback, Z
galvo/piezo stepping per slice, fitted to the trigger period), the GUI
builds it from Scan Setup at every Acquire using `SPIMProject.ini`'s um/V
calibrations, and the FPGA Scope confirmed X and Z to 0 counts. Found and
fixed on the way: the last block of a bounded run is aborted when the
Int-Sync pulse drops, so `Trigger up` now covers the block. Details:
`docs/wvfrm2_packing.md`. Under the default simulate-on-FPGA clamp (0) the
AO columns read 0 whatever the waveform says; the Waveforms tab has a
"Scope test clamp" (mV) to see the shape with nothing connected.

### Hardware-free regression tests (2026-09-04)

`src/unmscope/hardware/fake_fpga.py` emulates the measured bitfile
behaviour (reset-on-enable counter, bounded stop, count reset on disarm,
AO engine consuming two words per point with the AO limits applied, the
AI stream with Int Sync / DIO4 / AO columns while armed) behind the same
nifpga-shaped session, so `FpgaTriggerController`, `FpgaScope` and the
whole GUI flow run unchanged on it. `MainWindow.fpga_controller_factory`
is the injection point. `tests/test_gui_flow_fake_fpga.py` runs Z stack /
Continuous / Z stack in both trigger modes plus simulate-on-FPGA and an
arm-failure path, offscreen, in seconds. The hardware spikes remain the
truth; the fake is for not breaking what they proved.

### Dither galvo triangle (2026-09-04)

The Dither box now drives a real triangle on AO4: `triangle_points()` /
`smooth_turnarounds()` reproduce LabVIEW's segment construction (fractional
sweeps included) and the GUI feeds Range / # Sweeps / Fract. Flyback into
every scan waveform. Verified on the FPGA Scope (column 11) at 400 mV
pk-pk × 5.5 sweeps with the X sweep unaffected. See
`docs/wvfrm2_packing.md`, "Dither galvo".

Still open after this: AOTF registers (`AOTF ch (V)`, `AOTF Mode`,
per-channel shutters), file saving, the Phase 1 core skeleton, motion
stages, adaptive optics, the camera driver in a subprocess, and pixel-
matching the Waveforms tab against the live LabVIEW panel.

### AOTF excitation levels (2026-09-04)

The Excitation rows drive the FPGA's AOTF now: the one enabled row's
Power % becomes its channel's `AOTF ch (V)` level (0..5 V from the ini
limits, row N -> channel N -> AO5/AO6/AO7/AO3) at Acquire, 0 V at Stop,
forced off in simulate-on-FPGA. Measured on the bitfile: the level is a
DC output for the whole run and the FPGA reads it back on `AOTF ch out
(V)`; per-frame blanking would need the AOTF clock engine, which gave no
output from Python; the AOTF is not on the scope stream. `docs/aotf.md`.

Still open: flyback blanking via the AOTF clock engine, the analog check
of the AOTF pins (meter or loopback into AI0), the per-channel power
calibration table, file saving, the Phase 1 core skeleton, motion stages,
adaptive optics, the camera driver in a subprocess, pixel-matching the
Waveforms tab.

### Connect re-entrancy guard (2026-09-04)

The one known crasher is fixed. A second Connect click delivered by the
DCAM driver's own message pump, while the first open still blocked the
GUI thread, used to re-enter the handler and fault inside the
half-initialised first open. A single `_blocking_op` flag now owns the
GUI thread for the duration of any blocking driver call: all four
connect/disconnect handlers refuse re-entry, every connection control is
greyed and repainted before the call, the camera is published to
`self.camera` only once it is open, and `closeEvent` refuses a close
while a call is in flight. Seven regression tests inject the re-entrant
click from inside `connect()` -- all of them fail against the previous
code, one by unbounded recursion.

Acquire had the same hole and now takes the same flag: `_start_acquisition`
blocks in DCAM (including `repair_exposure_if_lost`, which re-initialises
the device) and set `self.acquiring` only at the end, and its modal
warnings run nested event loops. `_stop_acquisition` gained its own
`_stopping` flag because it is reachable from five async paths as well as
the Stop click. Before the fix, an Acquire click during a Disconnect
restarted the run mid-teardown.
`tests/test_connect_reentrancy.py`, `docs/known_issues.md`.

An adversarial review of the guard then found six more holes of the same
class, all now fixed with tests: the camera poll timer still firing inside
`camera.disconnect()` (very likely the cause of the one-off post-Disconnect
access violation); a refused window close being lost instead of re-issued;
`_update_connection_buttons` re-enabling the Disconnect buttons during a
run, so the hardware could be yanked out from under moving galvos; a raise
in `_begin_blocking` wedging the GUI unclosable; and pumped events reaching
the driver through the exposure spin box, the Simulation checkbox and the
Waveforms tab's streaming checkbox.

Still open: the connect is still ON the GUI thread, so a cold Orca open
freezes the window for ~10 s (safely now, not fatally); moving the driver
into a worker thread or a subprocess is the real cure. Plus the AOTF
items above, file saving, the Phase 1 core skeleton, motion stages,
adaptive optics, pixel-matching the Waveforms tab.

### Waveforms tab reworked into an oscilloscope (2026-09-04)

The FPGA Scope view was an auto-scaled plot whose x axis was labelled from a
free-running clock: ticks at `span * i/10` printed values like 2.106, 2.166,
2.226 and changed ten times a second, so nothing could be read off it. It is
now driven like a digital scope.

- A fixed **10 x 8 division graticule**. `Time/div` and `Volts/div` come from
  1-2-5 sequences, so every gridline is a round value by construction and the
  labels stop moving. Times are measured from the SCREEN CENTRE, which keeps
  them short at every zoom (referencing "now" printed -165900 us once zoomed
  and panned).
- **Trigger alignment** on a chosen digital column (default D4 cam trig): the
  centre pins to a rising edge so the waveform stands still and times read
  against the trigger. `Hold` freezes the buffer to explore a capture.
- Wheel zooms the timebase about the pointer, Shift+wheel the volts/div.
  There is deliberately **no drag-pan** -- position is the trigger's and Fit's
  job, not something to shove around and have to find again.
- **Hover** any trace for a callout: channel, its volts/div, the value there
  (a RANGE when the column is decimated -- one pixel of a 200 ms window holds
  20 000 samples) and the time from the trigger.
- **Click a trace to pin a tag.** The tag names the channel, states that
  channel's own volts/div and carries +/- to change it and x to remove it; it
  can be dragged aside and keeps a leader to its point. Anchored by TIME, so
  it survives zooming and rolling.
- **Per-channel volts/div** (the default), because the digital flags swing
  1.25 V while the X galvo is +-0.125 V and no single gain shows both. `Fit`
  gives each channel its own gain so waveforms of very different amplitude
  compare shape-for-shape. Once gains differ the vertical axis switches to
  **Divisions** -- a Volts axis would be lying about every trace it does not
  belong to -- and each tag states its own scale. `Per-ch V/div` off ganges
  them back onto the coarsest gain in play.

Two real bugs fixed underneath, both time-measurement errors that were
invisible while the axis was decorative:

- `envelope()` used `per = n // columns` and folded the leftover samples into
  the last column, drawing a mid-window feature at 63% across: a **13% time
  error**. Bin edges now come from `linspace`.
- `_draw_traces` laid m samples over m-1 intervals while `x_left` was derived
  from `len(seg)/fs` (an exclusive right edge), stretching every trace by
  m/(m-1): **0.556 of a division at 10 us/div**, in the trigger-centring path.
  Found by adversarial review; all 102 tests of the day passed with it present
  because the hover/tag tests probe using coordinates read from the very cache
  under test. `tests/test_scope_view.py` now also places a feature at a chosen
  time and checks the pixel it lands on, across ten timebases.

The panel's control rows moved into a horizontally scrollable strip: their
LouisXIV labels report full width as a MINIMUM, which had the panel demanding
1072 px. Panel minimum 1214 -> 627 px, and the main window 1938 -> 1675.

Still open: the window is *already* wider than its 1381 px target because of a
different tab (955 px) -- pre-existing, untouched here. Plus ~25 lower-severity
review findings, mostly test quality; tags slide in free run when zoomed; hover
repaints every trace on each pixel of pointer movement.

### Action items from the user (2026-09-04 evening) -- "just the way LouisXIV does"

Directive: match LouisXIV's behaviour and look; take real math and file
formats from the LabVIEW source (H:\UNM_Lightsheet\UNMScope_Source), never
invent them. Foundation landed first: a Z-stack is now retained in memory
(`MainWindow.acquired_stack()`), which items 2 and 3 both need.

1. **Camera tab** -- port the controls the Python tab lacks: Actual read-back
   (exposure / rate / # exposures), Sensor Mode, Dual View + Split pix#, ROI
   (Left/Right/Top/Bottom; Center ROI, Use all pixels, 1024x1024, 512x512,
   Center ROI at), # of pixels X/Y, FOV X/Y (um), SubROIs/Full ROI, ROI center.
   **DONE 2026-09-05** -- `gui/camera_tab.py` (layout measured box-for-box
   against the live panel, 0 px delta on every box), `hardware/roi.py` (DCAM
   Coerce ROI / Coerce ROI size / Adjust ROI / Set Binning, ported from the
   VIs), ROI + binning + sensor mode on both camera backends;
   `docs/camera_tab.md`. Still to confirm on hardware: the DCAM "SENSOR MODE"
   value strings, the driver's real subarray units (assumed 4 px), and the
   ASSUMED semantics of the five convenience buttons (their handlers live in
   SPIM MAIN.vi's compressed event structure). Greyed, not wired: SubROIs /
   Full ROI, Dual View mode, Split pix # -- keep-or-cut is the user's call.
2. **Calc (Stack Projections)** -- XY / YZ / XZ max-intensity projections with
   DeSkew, from the retained stack. LabVIEW: `SPIM/.../Image/PSF/HHMI - Calc XZ
   and YZ Max Projection from slanted stack.vi`, `HHMI - Deskew Stack data into
   XY Max projection.vi`, `HHMI - Get Max Projection of slanted stack.vi`.
   **DONE 2026-09-04** -- `analysis/projections.py`, Calc/DeSkew and the per-view
   save buttons wired; `docs/stack_save_and_projections.md`.
3. **Save TIFF stacks** the way LouisXIV does (format + naming from
   `Common/File IO/Image/Tiff/*`, e.g. `Convert tiff filename to
   channel-time-and-z.vi`). Needs a TIFF writer dependency (tifffile).
   **DONE 2026-09-04** -- `fileio/tiff_stack.py`; Save Files writes
   `<data>/Cell<N>/img_CH%02d_%06d.tif` (U16, uncompressed, OME-XML) + `AcqInfo.txt`;
   Save Image button; `docs/stack_save_and_projections.md`.
4. **Low-Level Waveform Config** -- port from LouisXIV's Adv Setup, but place it
   under **Utilities** (a deliberate move). Waveform type, Pixel/ms, Updates/Pix,
   Fractional Flyback/Smoothing, per-axis Excitation Size/Pixels (X, Xwvfrm, Z,
   Spiezo, Zpiezo, Dither), AOTF / galvo / piezo delays, sweep period, duty,
   # integrations, cam exp, cycle time, Z motion, DOE beams, X wave, Z
   bidirectional, virtual confocal, custom cycle time, Z piezo selector, AOTF
   cycle / sweep mode / pulse width / pulse duty, dither triangle pulses, etc.
   **Panel DONE 2026-09-05, functionality partial** -- `gui/waveform_config_panel.py`
   + `config/waveform_config.py` (the cluster 1:1, LouisXIV defaults, persisted in
   `~/.unmscope/waveform_config.json`), under Utilities. Live: Fractional Flyback,
   Dither Triangle Pulses, Dither Fract. Flyback (in step with the Scan Setup
   Dither box); indicators: Pixel/ms, Cam exp, Cycle time, the axes. The rest is
   greyed. **"Calculate Waveforms" PORTED 2026-09-05** (`hardware/louisxiv_waveform.py`,
   VI-for-VI from the hidden-frames export of `DAQ/Waveform/*`): cubic-accel /
   linear / cubic-decel fast-axis line + flyback, AO rate computed from exposure
   + cycle time and capped at 1000 kHz, DMA tick rounding, S-curve slow-axis
   steps. The scan now uses it; Updates/Pix, Fract. Smoothing and X Single
   Direction are live too. See `docs/utilities_and_waveform_config.md`.
5. **Utilities tab** -- "most of" LouisXIV's 18 launcher tools; which subset is
   the user's call. Mapped: Align Laser; View TIF stack (pairs with 3); Image
   Reviewer; **um per V calibration** (the per-channel calibration the user wants
   in its own tab); Calculate PSF (`Image/PSF/*`); View Z Lookup Table
   (`Calibration/Z Lookup/*`); Sample Stage Control; Shift Vslit calibration
   (`Calibration/Vslit Lookup/*`); Resave OME-XML TIFs (external exe); X&Z Galvo
   offsets per AOTF ch (`Calibration/X and Z Galvo offsets calibration GUI.vi`);
   Camera Debug Panel (`GUI/Camera Debug Panel.vi`); **FPGA Scope (done: the
   Waveforms tab)**; Auto Background (greyed in LouisXIV too); FPGA Monitor;
   Reset HW (= safe_state/reset); X Galvo Z Corrections (`Calibration/X Galvo/`);
   HW Config (`GUI/HW Configuration GUI.vi`); Imagine Optics (adaptive optics).
   **Grid DONE 2026-09-05** (`gui/utilities_tab.py`, 18 buttons measured 0 px off
   the rendered panel); wired: View TIF stack (loads a stack as if acquired),
   FPGA Scope. The other 16 are greyed; their SPIM MAIN event cases are indexed
   in `VI_Diagrams/.../SPIM MAIN/hidden_frames` for whichever the user picks.
   **User's pick (2026-09-05), to port or build:** FPGA Scope (LouisXIV's
   `HHMI - AI buffer.vi` panel -- compare with our Waveforms tab), HW Config
   (`GUI/HW Configuration GUI.vi`), Sample Stage Control (`Motion/Sample Stage
   Control (All Axes)/Sample Stage Control GUI.vi`, MP285 stage), Camera Debug
   Panel (`GUI/Camera Debug Panel.vi`), um per V calibration (the per-channel
   calibration, own tab). List may grow.
   **Decisions 2026-09-05:** MP-285 stage is NOT connected -> simulated backend
   + panel only; HW Config writes a UNMScope-owned copy of SPIMProject.ini (not
   LouisXIV's); FPGA Scope = the Waveforms tab (fix channel names 18-23: Perfusion,
   AOTF 2-6); build order: scope fix, Camera Debug Panel, HW Config, stage, um/V.
   **DONE:** scope names (`b525ff4`), Camera Debug Panel (`931b97d`,
   `gui/camera_debug_panel.py`, remote block dropped). HW Config / stage / um/V
   are being built on branches tool/hw-config, tool/sample-stage, tool/um-per-volt.
   Research maps (panel controls, event cases, sub-VIs, hardware) are in the
   session workflow journals; digest in the scratchpad `tools_research_digest.txt`.
6. **GUI cleanup DONE 2026-09-05** (user's decisions, recorded in CLAUDE.md):
   eight empty tabs, the Images tab's unwired furniture, Perfusion, and the
   Camera tab's SubROIs / Dual View / Split pix # removed; Timepoints,
   Multi-location and the full Utilities grid kept.

Scale note: items 1, 4 and 5 are a multi-week surface (each Utilities tool is
its own sub-GUI). The one-laser-at-a-time rule stays as LouisXIV has it.

## Action items for the user's review (2026-09-05, not started -- decide first)

Collected from the tool builds, their reviews and the day's findings. Nothing
below is being implemented until the user picks.

**Decisions needed**
1. HW Config > Apply: today it only writes UNMScope's ini copy and logs; LouisXIV
   does a full Reset HW. Options: (a) leave "takes effect on next Connect", (b)
   stop the run + disconnect/reconnect camera + FPGA reset. Which camera keys
   should Connect honour: Sync Readout (now hard-coded on), Binning, Serial
   number check (the DCAM adapter cannot select by serial), Simulate?
2. um/V Cal: the live ini says X galvo = 2000 um/V while LouisXIV's panel
   default is 53 -- which is right? Needs a bench measurement. Also LouisXIV's X
   galvo path adds (Xmin+Xmax)/2 and, with "Enable X Galvo Correction LUT",
   runs the 19-point XGalvo_LUT -- neither is ported.
3. Sample piezo voltage limits: calibration.py uses +-10 V; LouisXIV's constants
   are 0..10 V (HHMI - Z Piezo AOTF voltage limits.vi). X tile's +-10 V is a guess.
4. Camera Debug Panel: "Exposures Acqd" shows FPGA triggers fired (no DCAM-side
   count through pymmcore); refresh 500 ms assumed. Keep the five camera rows?
5. Images tab: the Gradient palette is NI's description, not NI's LUT -- compare
   on a real image. FOV shows 443.7 um where LouisXIV shows 444.0 (pixel-size
   rounding). Drawing tools / Cam selectors stay greyed.
6. **DONE** um/V Cal is no longer a left tab: it opens as its own window from
   the Utilities grid (user, 2026-09-05), so the 466 px width is moot. Its
   Revert button is still an addition (LouisXIV has Close).
7. Sample Stage: **defaults CONFIRMED 2026-09-05** -- read out of the VI with
   `vi.GetControlValue` over COM, which reports a loaded VI's control defaults
   and so recovers a hidden control's default when no render can show it.
   Auto Referesh MP = True, Wait for Moves = False (both guesses right),
   Stage Velocity 2500, Simulate/Enable Stage False, COM Port ("", 0). The VI's
   Settling Time default of 0.0 is overridden at load by the ini's 300 ms, so
   300 is the effective default and ours. Only their on-screen POSITION is
   still assumed. The rest of the item stands: the
   window drops LouisXIV's error clusters (701x735 vs 701x805); Gen. Grid
   Sequence greyed; multi-position acquisition coupling deferred. When the MP-285
   is cabled: COM8, 9600 8-N-1, low-resolution mode at 2500 um/s to confirm.
8. Low-Level Waveform Config: still greyed -- delays, Duty, DOE, AOTF pulse /
   sweep / cycle fields, Z wave Sweep, Wait for Zsettle, Sine; Z motion enum
   items unknown beyond the default.
9. Scope extras from LouisXIV declined for now (ordered channel list, two
   cursors, 29 ch at 200 kS/s) -- revisit if wanted.
20. **DONE 2026-09-05.** AcqInfo.txt now matches LouisXIV's, plus our own
    block. `Companion Metadata Cluster to String.vi` was read across all 26
    hidden case frames: 25 fields in the enum's order, formats varying per
    field (`%d`; `%.3f` sizes and positions; `%.4f` StageAngle_deg alone;
    `%f` the two time fields; quoted `"%s"` strings; comma-joined arrays,
    quoted for strings and bare `%d` for wavelengths; TRUE/FALSE booleans),
    and `skip?` wired to NOT Multi-positionAcq for the four position fields.
    Ours had shared not one key name with it. **User chose option (b)**:
    LouisXIV's keys and order first, then a `[UNMScope]` block for what its
    format cannot carry (Mode, Trigger mode, excitation %, Z start/end,
    camera model + serial). Fields whose panel controls were never ported
    are written empty, as LouisXIV writes them unconditionally. See
    `docs/stack_save_and_projections.md`.

**Verification still owed (hardware / real data)**
10. **DONE 2026-09-05** DCAM "SENSOR MODE" values and the subarray units, on
    S/N 102668. Hidden frames of `DCAM - Set/Get Sensor Mode.vi` give the real
    six-item enum and the numbers LouisXIV writes (Normal Scan 1, Light Sheet
    12, Split View 14, Dual LS 16 on SENSOR MODE; Rolling Top 1, Rolling Bottom
    2 on READOUT DIRECTION). Measured on the camera (`spikes/34`, `spikes/35`):
    the adapter accepts only AREA and SPLIT VIEW, so **Light Sheet and Dual LS
    are unreachable through pymmcore** and now raise a `CameraError` saying so;
    READOUT DIRECTION is DIVERGE-only in AREA and FORWARD/BACKWARD in SPLIT
    VIEW. Subarray units are 4 and 4, confirmed, matching `coerce_roi`. Still
    open: which DCAM number the adapter's "SPLIT VIEW" writes (probably 9,
    where LouisXIV writes 14). See `docs/camera_tab.md`.
11. Deskew shift direction on a real slanted stack (point feature must collapse).
12. Z piezo (AO2) / Wvfrm2 DMA path at a pin (0/1/2.5/5 V staircase).
13. The bench serial protocol of the MP-285 (fake transport only so far).

**Code hygiene**
14. **DONE 2026-09-05** The three ini writers are now one,
    `config/ini_text.py` (`IniText` + `write_keys`), used by spim_ini,
    hw_config and um_per_volt alike. They had disagreed: one worked on bytes,
    one on latin-1 text, and spim_ini's decoded the file as UTF-8 with
    `errors="replace"`, which would have replaced any non-UTF-8 byte with
    U+FFFD -- latent only because the rig's ini is pure ASCII today.
    Verified against the real SPIMProject.ini and locked in by
    `tests/test_ini_text.py`: a save that changes no value leaves the file
    byte-identical, changing one key alters exactly one line, and key
    spacing, mixed line endings and the file's missing final newline all
    survive. Also MEASURED while there: LouisXIV's ini has no blank line
    between sections and no final newline (we still put a blank line before
    a section we append, for readability; it is inert).
15. **DONE** Agents' renders copied into VI_Diagrams (`GUI/HW Configuration GUI/hidden_and_pages`,
    `GUI/Microns per Volt Settings GUI/hidden_and_pages`, `Motion/.../Sample Stage Control GUI/hidden_and_pages_stage`).
16. **DONE** Worktrees and tool/* branches pruned after the merges.
17. Timepoints and Multi-location boxes remain unwired (kept on request).
18. **DONE** Stack Projections "Calc" (user, 2026-09-05: "looks grayed out"): ours was
    enabled only while a Z-stack is in memory (Z-stack run completed, or View
    TIF stack); Continuous runs do not retain frames. LouisXIV's [56] "Max
    Projs" latch button is ALWAYS enabled and projects whatever stack the image
    window holds. Decide: keep the guard, or match LouisXIV (always enabled, log
    "no stack" when empty).
19. **DONE** Utilities grid: REMOVED (user, 2026-09-05) Align Laser, Image Reviewer,
    Calculate PSF, Auto Background, View TIF stack; then (same day) Resave
    OME-XML TIFs and Shift Vslit calibration. Kept (11): um per V
    calibration, View Z Lookup Table, Sample Stage Control, X&Z Galvo offsets per AOTF ch, Camera
    Debug Panel, FPGA Scope, FPGA Monitor, Reset HW, X Galvo Z Corrections, HW
    Config, Imagine Optics).
    **Grid re-measure: nothing to do, checked 2026-09-05.** The layout is
    index-driven (`COL_X[i % 2]`, `ROW_Y0 + (i // 2) * ROW_PITCH`), so
    dropping tools closed the gaps by itself: the 11 buttons sit in reading
    order over 6 rows at a uniform 72 px pitch, 16 px apart, still at
    LouisXIV's measured button size of 152x56.
    **`load_stack_from_file` is now unreferenced** -- item 18 settled Calc as
    "always enabled, project whatever is in memory", so it no longer needs
    it, and "View TIF stack" was the only caller. Deliberately KEPT rather
    than deleted as dead code: it is the working, tested body of the View TIF
    stack feature, and removing it would quietly foreclose re-adding that
    button. Delete it if the user confirms the feature is gone for good.

## Code cleanup pass (2026-09-05, adversarially-verified audit)

A three-lens audit (dead code / duplication / stale text) found 84 confirmed
issues (each independently re-verified by a second agent against the actual
source before being trusted); commits `4a23173` `fdb0090` `2665f39` `6f574bd`
applied the highest-value, lowest-risk subset -- 246 tests pass after each.

**Done:** all confirmed dead code (unused imports, zero-caller functions,
write-only attributes); one `paths.ensure_user_copy()` replacing four
near-identical "copy LouisXIV's ini on first use" bodies; one
`paths.LOUISXIV_ROOT`/`LOUISXIV_SUPPORT_DIR` replacing three hardcoded
copies of the install path; `waveform.dither_block`/`assemble_scan` shared
between the two waveform builders; `main_window._render`/`_bordered_panel`
replacing two duplicated call sites each; the module docstrings/comments
that had gone stale (main_window.py's header, CLAUDE.md's cleanup record,
the um/V calibration docs, fpga_trigger.py's tail).

**Deferred (in the audit's report, not applied -- lower value or higher
risk for the size of the change):**
- ~~A shared `gui/widgets.py`~~ -- **done 2026-09-05, but deliberately
  narrower than the audit proposed.** Reading the six modules' helpers side
  by side, the audit's premise does not hold: the `_label`s are not
  near-identical. calibration_tab paints a transparent background,
  camera_debug_panel sets a colour but no background, sample_stage_dialog
  sets both, hw_config_dialog word-wraps and applies its own rect transform
  plus a vertical nudge, waveform_config_panel right-aligns. One helper for
  all six would take five keyword flags and read worse than the six
  six-line functions. So `gui/widgets.py` holds only what is genuinely
  identical: `set_bold` (the three-line Qt font dance, written out 8x),
  `rect_mapper` (the measured-rect-to-page-rect one-liner, 4 copies
  differing only in origin constants -- waveform_config_panel's `_r` ADDS a
  frame offset and is a different transform, left alone), and
  `bring_to_front` (show/raise/activateWindow, 4 copies). 165 call sites
  were NOT touched: each module keeps its own `_label`/`_button` spelling,
  and only the bodies collapse. Verified layout-neutral by fingerprinting
  every child widget's class and geometry in four panels before and after
  -- identical. Named `set_bold`, not `bold`, because every `_label` has a
  `bold=` keyword that would shadow it.
- Merging `spim_ini.RotationStageSettings` into `hw_config.py`'s copy of
  the same `[Rotation Stage (PI U651) Settings]` section (two writers of
  one section today) -- touches a test's code, not just its bytes.
  Merging `spim_ini`'s bool/float ini codec into `hw_config.parse_value`/
  `format_value`, and `calibration_tab.format_value`'s NaN/Inf spelling
  into `um_per_volt.format_ini_double`'s -- both small, low priority.
  Palette hex constants named differently per module (`#dddddd` etc.) --
  cosmetic, skip unless a module using them is touched anyway.
- ~30 remaining stale-text findings (mostly docs/*.md session-log-style
  files and ROADMAP.md's own "Phase 1" framing) -- lower risk left as is;
  fix opportunistically when touching those files.
