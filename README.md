# UNMScope Python

Python replacement for "LouisXIV", the LabVIEW-based control software for
the UNM light-sheet microscope. See [ROADMAP.md](ROADMAP.md) for strategy,
scope, and phasing.

This repo is under active, incremental development alongside the existing
LabVIEW system (`H:\UNM_Lightsheet\UNMScope_Source`, untouched/authoritative
until a subsystem is formally cut over). Reference exports of the LabVIEW
VI block diagrams/front panels are in `H:\UNM_Lightsheet\VI_Diagrams\`.

## Status

The core acquisition path is working on the real instrument. FPGA-timed free
run, camera acquisition, Z-stacks, TIFF saving in LouisXIV's format, stack
projections with deskew, and the AOTF laser controls are all verified on the
hardware — the PCIe-7852R timing card and the Orca Flash 4.0. The GUI covers
Scan Setup, Camera, Images, the Waveforms oscilloscope and Utilities, laid
out to match the LouisXIV front panel.

Not everything is ported. Controls that exist on the panel but are not wired
to anything are deliberately left greyed rather than made to look functional,
and `ROADMAP.md` tracks what is outstanding, what is waiting on a decision,
and what still needs a measurement at the instrument.

It runs without any hardware attached: simulated camera, stage and FPGA
backends stand in, and the whole test suite runs in about 40 seconds on a
machine with no NI software installed.

## Layout

```
src/unmscope/     the program: config, hardware, fileio, analysis, gui
tests/            250 checks, no hardware needed
spikes/           numbered one-question experiments against the real rig
tools/            utilities for development, not for the microscope operator
docs/             what was measured, and how
```

`docs/teaching/` holds a slide deck and a field guide explaining the code
structure, written for lab members rather than programmers.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
pytest
```

Driving real hardware additionally needs NI-RIO installed (for the FPGA card)
and a Micro-Manager device adapter set (`mmcore install`, for the camera).
LabVIEW itself is not required — the FPGA bitfile is loaded through
`nifpga`, which talks to the NI-RIO driver directly.

## Running

```bash
python -m unmscope.gui
```

See [COMMANDS.md](COMMANDS.md) for the rest, and
[CLAUDE.md](CLAUDE.md) for the working agreements this repo is built under.
