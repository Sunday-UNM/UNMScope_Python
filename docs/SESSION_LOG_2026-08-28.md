# Session Log — 2026-08-28

Full narrative record of the first working session on the UNMScope
LabVIEW→Python conversion. Living reference docs (`../ROADMAP.md`,
`fpga_io_map.md`) hold the distilled current-state facts; this file is
the story of how we got there, for context when picking this back up.

## 1. Architecture reconnaissance (no hardware touched)

Explored `UNMScope_Source` (the LabVIEW "LouisXIV" light-sheet microscope
control app, ~2,353 `.vi` files) to map its architecture before touching
anything:
- Master/GUI app (`Tonmoy Project 2020.lvproj`, top-level VI `SPIM
  MAIN.vi`) + a separate Remote Acquisition Node app for distributed
  multi-camera setups.
- Queued-state-machine + named-message-queue producer/consumer design
  (`Common/State Machines/`, `Common/Main MsgQ/`).
- TCP-IP client/server layer for distributed acquisition.
- Per-subsystem folders: DAQ, Scan, Waveform, Image Acq (camera drivers),
  Motion, Adaptive Optics, GUI, Globals.

## 2. Batch VI diagram export

Since `.vi` files are binary/unreadable, wrote a Python + `pywin32` COM
automation driver (`H:\UNM_Lightsheet\scripts\export_vi_diagrams.py`)
against LabVIEW 2020's `PrintVIToHTML` to batch-render every VI's block
diagram + front panel to PNG. Exported **2,318 of 2,329 candidate VIs**
across the core app tree to `H:\UNM_Lightsheet\VI_Diagrams\` (browsable via
its `index.html`). This became the primary reference for understanding
what the LabVIEW code actually does throughout the rest of the session.

Key gotchas hit and solved along the way (see
[../../scripts](../../scripts) and the `labview-vi-png-export-recipe`
memory): a stale/wrong cached COM typelib via `gencache` (fixed by using
`win32com.client.dynamic.Dispatch` instead), a first-call binding quirk
needing a retry, and PNG filenames being prefixed by the LabVIEW-qualified
name for VIs that are `.lvlib` members.

## 3. Python project strategy agreed

Set up `H:\UNM_Lightsheet\UNMScope_Python\` (git repo, sibling to
`UNMScope_Source`, never nested inside it) with an agreed roadmap
(`ROADMAP.md`):
- Incremental, subsystem-by-subsystem rollout — LabVIEW stays the
  daily-driver until each piece is validated on real hardware.
- Custom hardware abstraction layer (ABC per device + `Simulated*`
  backend), not an existing framework like Pycro-Manager.
- GUI: PyQt6/PySide6, originally planned as a later phase.
- The NI FPGA board + breakout box keep being used as-is — Python talks to
  the existing compiled bitfile via `nifpga`, not a reimplementation.

Initial plan was core-skeleton-first (async task/queue architecture, no
hardware). The user then asked to pull real hardware validation forward
instead — camera triggering + breakout-box voltage verification — which
is what the rest of this session was actually about.

## 4. FPGA I/O reconnaissance

Extracted the real, physical breakout-box pinout directly from
`FPGA code\Reto FPGA Project.lvproj`'s FPGA-target I/O binding table (not
guessed) — see `fpga_io_map.md` for the full AO0-7/DIO0-15 table. Found
DIO4 = "Cam Ext Trigger Out DO", the camera's external trigger line.
Also extracted the FPGA bitfile's full register list directly from the
compiled `.lvbitx` (self-documenting XML).

## 5. Stage A — first real hardware contact

`spikes/01_fpga_connect.py`: opened a real `nifpga` session against the
board (confirmed present via Device Manager, `NI PCIe-7852R` status OK),
read `SW Version` = 3, wrote/confirmed an all-zero safe state. Real
hardware immediately caught two things the source diagrams got wrong (the
actual `Static AO to set` cluster fields differ from what the block
diagrams showed) — first sign that trusting only static analysis wasn't
enough.

## 6. Live FPGA panel + first real camera connection

Built `tools/fpga_live_panel.py` (PySide6) for interactive register
poking, and separately got the real Hamamatsu Orca Flash 4.0 camera
connected via `pymmcore-plus`. Hit and fixed:
- `pymmcore-plus` bundles its own MMCore (Device API v75), incompatible
  with the already-installed Micro-Manager 1.4/2.0 adapters — fixed with
  `mmcore install` + `find_micromanager()`.
- Corrected the documented camera model: the physically connected camera
  is a **C11440-42U32 (USB3 V3), S/N 102668** — not the C11440-22CU
  (Camera Link V2) `UNMScope_Source`'s docs describe. Confirmed by the
  user.
- `qtpy` missing when `pymmcore-plus` runs inside a Qt app (needs it for
  Qt-signal-based event callbacks).

Result: real 2048×2048 frames captured through a working `Camera` ABC
(`src/unmscope/hardware/camera.py`) and a minimal real GUI
(`python -m unmscope.gui`, window titled "UNMScope").

## 7. The trigger debugging story

This was the bulk of the session. Goal: prove the camera is actually
triggered by the FPGA's real external trigger line, not just running
standalone.

1. First attempt: toggle `Trigger Enable?` alone, combined with the
   camera armed via `startSequenceAcquisition`. **Crashed** — a real
   access-violation (`VCRUNTIME140.dll`, `0xc0000005`) from calling
   `snapImage()` on a background thread while FPGA calls happened on the
   main thread. Fixed by rewriting to a fully single-threaded, non-blocking
   sequence-acquisition poll instead of a blocking call in a thread.
2. Re-ran: got an inconsistent result (one run "passed" with a suspicious
   ~19.7s lag between firing and the frame arriving; a repeat got no frame
   at all in 30s). A control test (arm for external trigger, never fire
   anything) confirmed frames never appear spontaneously — so the
   inconsistency was real, not a false-positive artifact.
3. To isolate FPGA-side vs. camera-side, **ran the real LabVIEW LouisXIV
   app** briefly while the user probed with an oscilloscope — confirmed
   real, correct pulses exist when LabVIEW drives the sequence. This
   proved the physical wiring was fine and pinned the problem to our
   Python register sequence.
4. Diagnosed by reading the real `HHMI - Set all FPGA devices with
   waveform config information.vi` and `HHMI - Start FPGA device
   waveform.vi` diagrams closely: our sequence was missing that the AO
   waveform engine must actually be running (`AO Mode = "Start/Run
   Wvfrm"`, wait for `AO wvfrm ready`) before `Trigger Enable?` does
   anything, **and** three trigger-count clusters (`Trigger #s`, `Trigger
   stack #s`, `Trigger blast #s`, each `{'# on': int, '# off': int}`) were
   silently defaulting to 0/0 — zero triggers requested, no error, no
   pulse.
5. Fixed sequence (`spikes/03b_fpga_trigger_with_waveform.py`) — but the
   first try produced **two pulses per fire** (holding `Trigger Enable?`
   asserted for 150ms crossed a `Cycle(Ticks)` boundary and caused a
   second auto-fire). Shortened the host-side hold to ~10ms — clean single
   pulse confirmed on the oscilloscope, matching real LabVIEW's own pulse.
6. Connected the BNC from DIO4 to the camera's external trigger input,
   updated `05_roundtrip_test.py` to the corrected sequence, reran:
   **frame arrived 230ms after the trigger fired** — clean and
   timing-consistent (matches the 100ms exposure + readout), unlike the
   earlier broken attempts. **PASS, confirmed for real.**

Full corrected register sequence is documented in `fpga_io_map.md` — treat
that as the reference, this section is the story of how we found it.

## 8. GUI: External Trigger toggle

Added an "External Trigger (from FPGA, DIO4)" checkbox to the main GUI —
with it on, Snap/Live wait for a real FPGA pulse before showing a new
frame, so the trigger link is visible in the actual application window,
not just a script log. Verified by running the GUI alongside a repeating
1-pulse/second FPGA script.

## Current state (end of session)

- Camera subsystem: **working** (connect, snap, live, exposure, external
  trigger from FPGA) via `pymmcore-plus`.
- FPGA trigger subsystem: **working and verified on oscilloscope + real
  camera**, corrected sequence documented.
- Static analog voltage output (Stage B — e.g. hold a known voltage on a
  galvo channel and verify with the oscilloscope): **not yet done**, still
  open from the original plan.
- Hardware left in safe state (FPGA `AO Mode = "Set AO"`, all channels
  zero) with no process holding the FPGA or camera session.
- Everything committed to git (`UNMScope_Python`, currently at commit
  `41357d4` + the External Trigger GUI commit) — `git log` there is the
  detailed change-by-change record.

## Next steps (pick up here)

1. Stage B: static voltage verification on a galvo channel (oscilloscope),
   the one piece of the original plan never completed.
2. Start folding the validated Camera + FPGA trigger code into a real
   "Acquire" workflow in the GUI (currently separate Connect/Trigger
   controls, not yet a unified acquisition button).
3. Eventually: motion/stage control, adaptive optics, and the full AO
   waveform engine (we've only ever pushed all-zero waveform data — real
   galvo/AOTF scanning waveforms are a separate, not-yet-started piece).
