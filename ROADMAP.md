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
