# UNMScope Python Roadmap

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
