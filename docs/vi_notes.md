# What we already know about each LouisXIV VI

**Read this before opening any render under `VI_Diagrams`.** Diagram PNGs are
the most expensive thing in this project to look at — LabVIEW's 9 px text
means reading them at 2-8x zoom, and a single investigation can run to
hundreds of image reads. Every VI below has already been read. Do not read it
again to answer a question this file answers.

**After reading a VI that is not here, add a row.** One line on what it
computes, the constants or formulas it settles, and where the full write-up
lives. That is what stops the next session paying for the same frames.

Hidden case frames (`hidden/<name>d1.png`, `d2.png`, ...) only exist where
someone exported them; the "frames" column says so. Exporting is cheap
(seconds per VI) — *reading* is what costs.

| VI | What it settles | Frames | Full write-up |
|---|---|---|---|
| `Image Acq/DCAM/Setup/DCAM - Set Sensor Mode` | The sensor-mode enum has **6** items, and they drive two different DCAM properties. SENSOR MODE: Normal Scan 1, Light Sheet 12, Split View 14, Dual LS 16. READOUT DIRECTION: Rolling Top 1, Rolling Bottom 2. LouisXIV's name for 14 is not the SDK's. | 6 hidden | `camera_tab.md` |
| `Image Acq/DCAM/Setup/DCAM - Get Sensor Mode` | The reverse map, agreeing exactly: 1→Normal Scan, 12→Light Sheet, 14→Split View, 16→Dual LS. | 4 hidden | `camera_tab.md` |
| `Image Acq/DCAM/Constants/DCAM CCD Pixel sizes` | Pixel pitch per camera: **Orca4.0 = 6.5 µm** both axes, Orca2.8 = 3.63, Andor = NaN. | 2 hidden | `camera_tab.md` |
| `Image Acq/DCAM/Setup/DCAM - Coerce ROI`, `DCAM - Coerce ROI size` | ROI coercion: 1-based inclusive in, position snapped **down** to the position unit, size rounded **Down** to the size unit. Both units are 4 on this Orca (measured, spike 34). | — | ported in `hardware/roi.py` |
| `Image Acq/DCAM/Setup/DCAM - Read cycle times` | The camera's own frame period, straight from the Hamamatsu table pasted into the VI. 1H = 9.74436e-6 s. SYNCREADOUT: (Vn/2 + 18)·1H. EDGE: exposure + (Vn/2 + 10)·1H. So the cycle depends on the **ROI height**, not just the exposure. | 5 hidden | `louisxiv_cycle_time_semantics.md` |
| `Common/Constants/HHMI - Z Piezo AOTF voltage limits` | Literal constants: **Sample piezo 0..10 V** (not ±10), Pockels 0..2 V. Z piezo / AOTF / AOM limits come from globals, i.e. the ini. | — | `calibration.py` |
| `File IO/Build Image Path` | `<base>_CH%02d_%06d.<ext>`. The `position %d` folder is **1-based** — Position Index runs through a `+1` — so index 0 is `position 1`. A `Raw?` input inserts a `RAW` element (autosave-raw, not ported). | 2 hidden | `stack_save_and_projections.md` |
| `File IO/OME Save Image Dialog` | The **Save Image** prompt. The save directory is BUILT from typed fields, not picked -- the diagram carries the rule as a comment: `\username\celltype\labeling\YYMMDD\cell[n]\location[n]\n.tif`. Root Save Directory + User Name + Cell Type + Cell Labeling are typed; Date (YYMMDD), Experiment (`Find Next Experiment Folder Number`) and Output Path are computed and read-only. Tabs: Experiment Description (free text) and Channels (Camera / Fluor / ND Filter / emission + excitation wavelength, filled from the waveform globals and `Channel.lvclass`, not typed). | panel + diagram | ported in `gui/save_image_dialog.py`; Channels tab not ported |
| `File IO/Companion Metadata File/Companion Metadata Cluster to String` | AcqInfo.txt: 25 fields in the **enum's** order (not the cluster's). Formats differ per field — `%d`; `%.3f` sizes and positions; `%.4f` StageAngle_deg alone; `%f` the two time fields; quoted `"%s"` strings; comma-joined arrays. `skip?` = NOT Multi-positionAcq, which drops the four position fields. | 26 hidden | `stack_save_and_projections.md` |
| `DAQ/Waveform/Common/HHMI - SPIM Shift waveforms by galvo delay` | Channel delays are **held-value padding**, not a resample or a rotate: each channel gets copies of its own first sample at the front and last at the end. | — | `utilities_and_waveform_config.md` |
| `DAQ/Waveform/Common/HHMI - SPIM Number of extrac counts needed for galvo inertia shift` (sic) | `total = round(max|delay| × AO_rate)`; `end_i = round((1 − d_i/max) × total)`; `begin_i = total − end_i`. AOTF/Pockels gets `begin = total, end = 0`. Negative delays produce inconsistent block lengths (we refuse them). | 2 hidden | `utilities_and_waveform_config.md` |
| `Waveform/HHMI - Add counts to waveforms at beginning and end` | Pads with the block's own first and last sample. Negative count = empty (LabVIEW Initialize Array). | — | `utilities_and_waveform_config.md` |
| `FPGA code/FPGA VIs/AOTF Clock/HHMI - Execute AOTF Clock Waveform state` | The AOTF square-wave generator, with its own timing diagram on the block diagram: per fast-axis step the AOTF turns **on** `AOTF Delay` after Start time for `Pulse width`, then off `AOTF Delay` after the next point. "When AOTF Delay is < Pulse width, only 1st pulse is performed." Runs in a Single-Cycle Timed Loop. States: Wait for trigger → rising edge on `AOTF start trigger` → Output Wave; emits `AOTF ch step trigger`. **Not used by the deployed bitfile** — see `aotf.md`. | visible only | `aotf.md` |
| `FPGA code/FPGA VIs/AOTF Clock/HHMI - AOTF Clock Control Loop` | Case `AOTF Mode` = "Start/Run Wvfrm" reads `AOTF ch FIFO` at `Address`; `Data` → the **global** `AOTF ch`, and `AOTF ch > 0` gates the global `Start sweep?`. Address increments on "1st pulse complete?". So the FIFO holds per-step **channel numbers, not levels** — an unloaded FIFO alone produces silence. | visible only | `aotf.md` |
| `FPGA code/FPGA VIs/AOTF channel/HHMI - AOTF Channel Output Loop` | `AOTF ch Mode` selects: "Start/Run Wvfrm" = "Keep AOTF clock line low and Wait for trigger. Then clock out AOTF waveform"; the other case sets the AOTF line to a static state. | visible only | `aotf.md` |
| `FPGA code/FPGA VIs/AOTF channel/HHMI - Execute AOTF ch Waveform state` | The sweep state machine: waits for a **rising edge on `Start sweep?`**, then Output Wave; `AOTF sweep ticks/phase`, `AOTF sweep phases`, `Start time (ticks)`, `Reset sweep?`, `# generated`. | visible only | `aotf.md` |
| `FPGA code/FPGA VIs/AOTF channel/Low Level/HHMI - AOTF ch FIFO size` | Literal: the AOTF channel FIFO is **4** entries — one per step of a laser cycle ("Cycle lasers"). | — | `aotf.md` |
| `FPGA code/Host to FPGA/Front panel/Set AOTF Sync Mode Values` | Writes only `AOTF ch (V)` + `Set F.P. (T)`. No engine configuration — this is the DC level path the port already uses. | — | `aotf.md` |
| `FPGA code/Host to FPGA/Front panel/HHMI - Set AOTF Sweep settings` | Maps AOTF Sweep parameters → FPGA cluster: # of sweep phases, ticks/phase, ticks per ramp, sweep "on"/"off" (Ticks); freq (1/tick) and periods/phase are computed and greyed. | — | `aotf.md` |
| `SPIM LV8.6 VIs/Calibration/AOTF delay/HHMI - SPIM Calc AOTF delay` | `AOTF delay (us)` is a Waveform-Config field where **-1 = auto**; auto calls the Convert VI below. Cycles through used AOTF channels. | — | `aotf.md` |
| `SPIM LV8.6 VIs/Calibration/AOTF delay/HHMI - SPIM Convert X pix size to AOTF delay` | The delay is a **linear interpolation** of X pixel size (um) against a `um`/`delay` calibration table supplied by the caller — not a formula. That table is NOT in `SPIMProject.ini`; where it lives is still open. | — | `aotf.md` |
| `SPIM LV8.6 VIs/DAQ/Control Modules/HHMI - SPIM AO AOTF Channel control module` | Inputs `AOTF Voltage Out (0=off)`, `Mode` (Initialize…), **`Sync to` = "CO Freq Clock"**, `AO sampling rate (Hz)`, `Simulate?`. A DAQmx counter-clock path — **dead on this rig**: the only NI hardware present is the PCIe-7852R (no DAQ card). | visible only (cases hidden) | `aotf.md` |
| `DAQ/Waveform/Linear/HHMI - SPIM Time per exp` | `Time per exposure = points per exposure ÷ AO rate`, where points per exposure is the fast-axis array halved if X Bidirectional or Virtual Confocal. A **waveform** quantity — no camera term in it. | — | `louisxiv_cycle_time_semantics.md` |
| `DAQ/Waveform/Utilities/HHMI - Convert User to Waveform Start-End Axis` and `... Waveform to User ...` | **Slices = Pixels**, and the span is `(Slices − 1) × Interval`. So `Slices = range/interval + 1` — which is what we compute, confirmed live in LouisXIV (−5..5 at 0.05 → 201). | 2 hidden each | `known_issues.md` |
| `Motion/Sample Stage Control (All Axes)/Sample Stage Control GUI` | 73 event cases, all mapped. Hidden-control **defaults** read over COM: Auto Referesh MP = True, Wait for Moves = False, Stage Velocity 2500, Settling Time 0.0 (the ini's 300 ms wins). | 73 hidden | `sample_stage.md` |
| `GUI/HW Configuration GUI` | Window title is **"Hardware Configuration"** (`vi.FPWinTitle`), not the VI's file name. | — | `hw_config.md` |
| `DAQ/Waveform/*`, `DAQ/Control Modules/FPGA/*`, `Image Acq/DCAM/*` — the cycle-time set | How Cam exp / Cycle time / Custom Cycle Time behave: Cam exp is written FROM the camera and never sets it; Cycle time = `max(exposure, camera_cycle - 500 ns)` with Custom off, or floored (never lowered) by that when a typed value is on; FPGA period = `max(Cycle time, camera cycle)`. **Applied to the port 2026-09-06** (the adversarial pass never ran, but the open tension was resolved by asking the user: Custom Cycle Time IS ticked on this rig). One adjacent finding — AO-rate rule 1 should read Cycle time, not Cam exp — was deliberately left unapplied (real hardware-timing consequence; needs a bench check). | many hidden | `louisxiv_cycle_time_semantics.md` |
| `Motion/3D Stage/Generate Multipoint Grid Sequence GUI` | Modal dialog launched from Sample Stage Control's Gen. Grid Sequence button. Inputs: Start XYZ, Range XYZ, Overlap %, tile Size XYZ (from acq settings). Algorithm: `step = size − overlap`; `Pos[n] = step×n` while `(range − size) ≥ step×n`; always ≥ 1 point. Grid order: Z outermost, Y middle, X innermost. "Set Sequence" replaces the LocationSequence. "Relative" toggle offsets Start by current position at generate time. Ported to `gui/grid_sequence_dialog.py`; Tile Size is user-editable (not piped from acquisition). | panel + 1 diagram visible | ported 2026-09-15 |
| `Motion/3D Stage/Multipoint Sequence Grid/Multipoint Seq Grid - Generate XYZ Pt Array` | The subVI that computes positions per axis. Block diagram carries the algorithm verbatim: `Pos[n] = (Size−Overlap)×n` while `(Range−Size) > Pos(n)`. Comment annotation: `Size_X|Y = Image Size X|Y × um/px`; `Size_Z = Z um/px × # slices`. Grid is built as nested XYZ loops. | panel + diagram | ported inside `generate_grid()` in `gui/grid_sequence_dialog.py` |
| `SPIM MAIN` | 392 hidden event/state frames, all triaged. Cam exp / Cycle time / Custom Cycle Time have **no event case of their own** — they are elements of the Waveform cluster, whose event [104] just pushes "Calculate Waveforms". Per-element pattern is [105] (d237). "Update Cam" (d346-d348) writes the Camera tab's Actual readback every 0.25 s. Greying is the engine's "Enable-disable" state. Per-tab panel renders live in `panel_*/`. | 392 hidden | `louisxiv_cycle_time_semantics.md`, `camera_tab.md` |

## Useful COM tricks, so they are not rediscovered

- `vi.GetControlValue(name)` on a **loaded but not running** VI returns that
  control's **default** — the only way to recover a hidden control's default
  when no render can show it (that is how the stage defaults above came out).
- `vi.FPWinTitle` gives the window title, which is often not the file name.
- `app.PrintSetupCustomDiagramHidden = True` before `PrintVIToHTML` prints
  **every** case frame. Without it you get only the case that happened to be
  showing when the VI was saved, which is how several wrong readings started.
- Reading N case labels cheaply: crop the top ~30 px band of each frame and
  stack them into one image with PIL, then read that single image instead of N.
