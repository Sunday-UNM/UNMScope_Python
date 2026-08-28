# UNMScope Python

Python replacement for "LouisXIV", the LabVIEW-based control software for
the UNM light-sheet microscope. See [ROADMAP.md](ROADMAP.md) for strategy,
scope, and phasing.

This repo is under active, incremental development alongside the existing
LabVIEW system (`H:\UNM_Lightsheet\UNMScope_Source`, untouched/authoritative
until a subsystem is formally cut over). Reference exports of the LabVIEW
VI block diagrams/front panels are in `H:\UNM_Lightsheet\VI_Diagrams\`.

## Status

Phase 1 (core skeleton — no hardware yet) is starting. Nothing here talks
to real hardware yet.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
pytest
```
