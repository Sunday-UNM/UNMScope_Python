# HW Config (port of `GUI/HW Configuration GUI.vi`, SPIM MAIN event case [45])

Code: `src/unmscope/config/hw_config.py` (the four ini settings groups as
dataclasses + the line-preserving ini writer), `src/unmscope/gui/hw_config_dialog.py`
(the non-modal panel). Tests: `tests/test_hw_config.py` (pure),
`tests/test_hw_config_dialog.py` (offscreen Qt).

Integration (for the window): `show_hw_config_dialog(parent, existing=...)`
opens / re-raises the panel; `HwConfigDialog.applied(HwConfig)` fires after
Apply or Save has written the ini copy. What the window does with it is its
call (see "Apply" below). Utilities' `("HW Config", None)` entry is what the
integrator wires to it.

## Where this comes from in LouisXIV

Sources read (all under `H:\UNM_Lightsheet\VI_Diagrams` unless noted): the
panel render `SPIM/SPIM LV8.6 VIs/GUI/HW Configuration GUI/HW Configuration
GUIp.png` (Cameras page, 960 x 679), the other three tab pages and all 37
hidden case frames rendered 2026-09-05 through LabVIEW COM into the session
scratchpad (`hwconfig_frames/`; not copied into VI_Diagrams -- see open
questions), SPIM MAIN's hidden frames d155 ([45] HW Config), d301/d302
(Acquisition Start-Stop), d310 (Reset HW), the four INI functional globals,
`Read/Write Configuration Section to INI File.vi`, `Get Cam Save Index.vi`,
`Populate Camera[n] Settings from INI.vi`, `Enabled Camera Settings.vi`, the
typedef `.ctl` files (enum item lists, zlib-inflated) and the live
`SPIMProject.ini`.

SPIM MAIN [45] "HW Config" launches the VI asynchronously (`Launch Asynch
VI.vi`, FP.Open) with no data in or out; the panel runs its own queued state
machine. The `RemoteNode` conditional-disable symbol is `False` in the SPIM
project (`Tonmoy Project 2020.lvproj`), so every "remote node" branch is
compiled out: the port implements only the SPIM MAIN behaviour.

## State / event -> what the port does

| LouisXIV | Frames | Port |
|---|---|---|
| Initialize -> Initialize Variables: `.ini path` from `HHMI - Filepaths global`, Read INI for Cam1..5, Imagine Optics, Rotation Stage, Misc | d1-d3 | `load_hw_config()` on the owned copy (below) |
| Display Configs: show the cluster of the current tab page / camera | d10-d13 | `HwConfigDialog.refresh()` |
| Property Nodes, frame 0 (Cameras page): Enabled off -> every other element "Disabled and Grayed Out"; Enabled on -> RemoteIP enabled iff Remote, DCAM Port / Cmd Port enabled iff Remote, Model / Serial Number / Image Transform / Sync Readout / Simulate / Binning enabled iff not Remote; Save Index and Remote enabled | d4-d9 | `_property_nodes()` (same rules, all 11 widgets) |
| Property Nodes, frame 1: page visibility / Camera ring disabled only on a remote node | d9 | nothing (never a remote node) |
| [1] Camera: Value Change -> Display Configs + Property Nodes | d15 | `camera_combo.currentIndexChanged -> refresh()` |
| [2] Tab Control: Value Change -> same | d16 | `tabs.currentChanged -> refresh()` |
| [8] Camera Settings: Value Change -> Replace Array Subset camera[Camera], Property Nodes | d23 | every camera widget writes into `config.cameras[current]` at once; switching cameras only re-displays, so edits are never lost |
| [9] [10] [12] Imagine Optics / Misc / Rotation Stage cluster edits -> stored in the shift register | d24, d14, d26 | widgets write into the dataclasses at once |
| [4] Apply -> Apply Configs: Write INI (input) for Cam1..5, Imagine Optics (+ Controller), Rotation Stage, Misc; then `Send Message to GUI 'Acquisition Start-Stop'`, 200 ms, `Enabled Camera Settings.vi (Refresh?=T)`, `Send Reset HW to ENGINE`, 200 ms, `Send Message to GUI 'Property Nodes'` | d28, d29 | `apply()`: `save_hw_config()` then `applied.emit(config)`. The rest is the main window's reaction -- see Apply below |
| [7] Save -> Apply Configs + Exit | d22 | `save()` = `apply()` + `accept()` |
| [5] Revert -> Read INI for everything, Display Configs | d30-d33 | `revert()` re-loads the owned copy and refreshes |
| [3] Close / [6] Panel Close -> Exit (FP.Close), edits discarded, no prompt | d18, d21, d34 | `reject()` / the window close box; nothing is written |
| [11] "Enter Values" (Enter X Galvo Linearize LUT Values) -> Launch Asynch `SPIM X Galvo LUT Correction GUI.vi` | d25 | button present, **greyed** (editor not ported) |
| Error / Invalid State / Cancel | d27, d35 | n/a |

### Apply, and what LouisXIV does afterwards (documented, not performed)

After writing the ini LouisXIV re-runs SPIM MAIN's *Acquisition Start-Stop*
state (the same state the Acquire button pushes -- with Acquire off its
unrendered branch presumably stops a run; pressing Apply during a run would
re-enter the start branch), refreshes the enabled-camera table that 51 VIs
read through `Read Register`, and sends **Reset HW** to the Engine: "Do a
hardware reset on DAQ boards. Reinitialize everything" = Start hardware 1 /
Load Locals / Set Camera / Enable-disable / Start UI Loop. Even so, the panel
says "Enabling and Disabling cameras requires SW restart" because camera
handles are only opened at startup (`DCAM - Open Camera Handles.vi`).

The dialog therefore only emits `applied`; the window decides whether to
stop a run and reconnect. Of the camera keys, the Python side already has
homes for `sync_readout` (`TRIGGER_SYNCREADOUT` / `TRIGGER_EDGE`, currently
hard-coded TRUE in the Camera tab) and `binning` (`set_binning`); `serial_number`
can only be *checked* against DCAM's `CameraID` after connect (the
HamamatsuHam adapter cannot select a camera, `known_issues.md`); Image
Transform has no consumer yet. Note also (skeptic): LouisXIV's Set Camera
state applies the Camera tab's binning, not the ini key -- the ini `Binning`
is only the start-up default.

## The ini: what is written, and how

Python never writes LouisXIV's file
(`H:\UNM_Lightsheet\UNMScope_Source\SPIM\SPIM Support files\SPIMProject.ini`).
`user_ini_path()` creates `~/.unmscope/SPIMProject.ini` from it byte-for-byte
on first use and everything reads / writes that copy.

Sections owned by this panel (spellings are LouisXIV's, including
`Analyis` and `Enable?`):

| Section | Cluster / dataclass | Keys |
|---|---|---|
| `[Cam1.Camera Settings]` .. `[Cam5.Camera Settings]` | `Camera INI Settings Cluster.ctl` / `CameraSettings` | Enabled, Simulate, Model (enum idx: 0 Andor, 1 Andor sCMOS, 2 Orca2.8, 3 Orca4.0), Serial Number, Save Index, Sync Readout, Image Transform (enum idx: None, Transpose, V Flip, H Flip, Diag Flip, Rot 90, Rot 180, Rot 270), Remote, RemoteIP, DCAM Port, Cmd Port, Binning (ring value 1/2/4) |
| `[Imagine Optics Settings]` | `Imagine Optics All Settings Cluster.ctl` / `ImagineOpticsSettings` | Enable, Simulate, # Pts (Default), Default Camera (enum idx Cam1..Cam5), Default Analyis Method (enum idx: RMS Contrast, Peak Intensity, ModulationDepth (Matlab)), Matlab Script Directory |
| `[Imagine Optics Settings.Controller]` | the `Controller` sub-cluster / `ImagineOpticsController` | Wavefront Corrector Setup File, HasoConfigFile, Correction Interaction Matrix File, Default Wavefront Positions File, Flat Wavefront Positions File, Sleep after command apply (ms) |
| `[Rotation Stage (PI U651) Settings]` | `Rotation Stage Settings Cluster.ctl` / `RotationStageSettings` | Enable?, Simulate, Serial Number, Speed (deg/s), Settling Time (ms) |
| `[Misc Settings]` | `Misc Settings Cluster.ctl` / `MiscSettings` | Z um/px source (enum idx: Z Galvo, Z Piezo), Disable Xg-Zg-Zp Calibrations, Enable X Galvo Correction LUT, Enable Z Piezo 2 (Dither Chnl.), Use Z Galvo and Dither as Alternating Galvo Channels, Galvo 1/2 Alternating First/Second (V) |

Encoding (`Write Configuration Section to INI File.vi`, and the live file):
booleans `TRUE`/`FALSE`, strings and paths double-quoted, enums as integer
index, the Binning ring as its U32 value, paths in LabVIEW form
(`/C/Users/...`; the dialog shows and browses them as `C:\Users\...`).
Facts about the live file the writer must respect: CRLF, no BOM, **no final
newline**, `Key = Value` with single spaces, and every `[Misc Settings]` value
carries four trailing spaces. `configparser` cannot reproduce that, so
`IniText` rewrites **only the value of a key whose value changed** (keeping
its prefix, trailing spaces and line ending), appends keys / sections the file
lacks (LouisXIV's Read-then-Write adds missing params too) and leaves every
other byte alone. `save_hw_config` after `load_hw_config` is byte-identical
(tested on the real file). Edited floats are written in shortest form
(`5`, `2.5`) as the live `[Misc Settings]` shows; the exact LabVIEW DBL
format of the Misc writer was not verified (other sections use `%.6f`, which
this module never touches).

The `Enum` case of `Write Configuration Section` was not rendered (only the
`Ring` case); "enums as index" rests on the five observed values in the ini
(Model = 3, Image Transform = 0, Default Camera = 0, Default Analyis Method
= 0, Z um/px source = 1) and matches the typedef orders.

## Live vs. stored only

| Page / control | State |
|---|---|
| Cameras: all 12 keys x 5 cameras, greying rules, Camera ring | editable, stored; the window may act on `applied` (nothing is pushed to hardware by the dialog) |
| Imagine Optics, Rotation Stage, Misc. pages | **ini-only editors**: no Python consumer exists for these sections (no HASO / deformable mirror module, no PI U-651 driver, no consumer of the Misc waveform flags or Z um/px source). Kept so the settings file stays complete for LouisXIV and for those ports |
| Misc. "Enter Values" | greyed: `SPIM X Galvo LUT Correction GUI.vi` (edits `SPIMProject XGalvo_LUT.txt`) is not ported |
| Misc. square bool buttons | the "on" colour is ASSUMED (green); the render shows them off (white, black frame) |
| Error in / error out clusters | dropped -- connector-pane terminals, not controls |

## Dropped / trimmed (cleanup call)

- The window is 486 x 490 instead of 960 x 679: LouisXIV's panel is the tab
  control (0..485 x 0..440) plus the button row; the remaining area holds
  only the error clusters and empty space.
- No remote-node behaviour (compiled out in the SPIM build).
- The "Show Length Scale" string that exists in `Misc Settings Cluster.ctl`
  but neither on the panel nor in the ini (probably a removed element).

## Layout, measured

Reference: the COM renders (Cameras page from VI_Diagrams, the other pages
from the session render), PIL row/column scans for the structural colours
(pane / tab outline (221,221,221), field and ring borders (170,170,170),
button top-left (204,204,204) and bottom-right (170,170,170), square LED
frames black, LabVIEW's classic string/numeric fields = a 4 px top-left
shadow (2 rows 221 + 2 rows 204) and no right/bottom edge). Ours: the dialog
grabbed offscreen and scanned the same way (`hwc_measure/ours.py` in the
session scratchpad). Render coordinates; page widgets are placed with
`_r()` = render minus the pane origin (1, 26).

| Item | Reference (x0,y0,x1,y1) | Ours | Delta |
|---|---|---|---|
| tab pane top / right / bottom edge | y 25 / x 485 / y 440 | same | 0 |
| tab dividers (Cameras/Imagine Optics/Rotation Stage/Misc.) | x 69, 167, 264, 312; tops y 2 | same | 0 |
| Apply / Save / Revert / Close | (82,446,166,479) / (178,..,262,..) / (274,..,358,..) / (370,..,454,..) | same | 0 |
| Camera ring | (21,94,155,114) | same | 0 |
| Enabled / Sync Readout / Simulate boxes | (27,132,39,144) / (27,266,39,278) / (27,327,39,339) | same | 0 |
| Model / Image Transform / Binning rings | (27,164,95,184) / (27,296,106,316) / (149,306,196,328) | same | 0 |
| Serial Number / Save Index (bevel) | (27,201,98,224) / (27,242,80,263) | same | 0 |
| Remote box | (149,155,161,167) | same | 0 |
| RemoteIP / DCAM Port / Cmd Port (bevel) | (149,185,232,208) / (149,226,202,247) / (149,265,202,286) | same | 0 |
| IO: Enable / Simulate boxes | (11,36,23,48) / (11,51,23,63) | same | 0 |
| IO: # Pts spin / Default Camera ring | (11,64,68,84) / (11,84,145,104) | same | 0 |
| IO: 5 path fields + browse buttons | (15,126+40k,434,148+40k) / (438,127+40k,468,143+40k), k=0..4 | same | 0 |
| IO: Sleep spin / Analysis ring | (15,328,72,347) / (11,371,202,391) | same | 0 |
| IO: Matlab dir field / browse | (11,409,430,431) / (434,410,464,426) | same | 0 |
| Rot: Enable? / Simulate boxes | (28,70,40,82) / (28,85,40,97) | same | 0 |
| Rot: Serial / Speed / Settling fields | (28,98,174,118) / (28,119,66,138) / (28,139,66,158) | same | 0 |
| Misc: Z um/px ring | (34,52,149,72) | same | 0 |
| Misc: 4 square buttons | (34,73+24k,58,96+24k), k=0..3 | same | 0 |
| Misc: Enter Values | (214,111,298,144) | same | 0 |
| Misc: 4 numeric fields | (34,169+20k,72,188+20k), k=0..3 | same | 0 |

59 scanned structures, worst delta 0 px. Notes:

- Bevel fields: the render shows only the top-left shadow, so their width is
  the top-shadow run and their height the left-shadow run (string fields 23
  px, numerics 21 px); Qt frames are 1 px taller because LabVIEW draws the
  bevel 1 px past the fill.
- Labels are placed at (measured glyph left - 1, glyph top - 4) and are not
  in the table: offscreen Qt renders text as boxes, and the on-screen font
  differs from LabVIEW's anyway. Text baselines should be checked against
  the real panel once it is run side by side.
- The pane's left edge sits under the render's 1 px print border (x = 0)
  and could not be measured; ours is at x = 0.
- ~~Window title~~ **resolved 2026-09-05**: `vi.FPWinTitle` over COM says
  the window is titled **"Hardware Configuration"**, not the VI's file name.
  Our assumed "HW Configuration GUI" was wrong and is fixed. (The same read
  confirms "Microns per Volt Settings" and "Debug Panel" for the other two
  tool windows, which we already had right.)

## Open questions

1. What should the window do on `applied`: nothing until the next Connect,
   or stop a run / disconnect / reconnect the camera and reset the FPGA
   (LouisXIV's Reset HW)? Which camera keys should be honoured on reconnect
   (Sync Readout, Binning, Serial-number check, Simulate)?
2. Keep the three ini-only pages editable (LouisXIV parity, as built) or
   grey them until their subsystems exist?
3. Should the three extra page renders and the 37 hidden frames be copied
   from the session scratchpad into `VI_Diagrams/.../HW Configuration GUI/`?
4. Port `SPIM X Galvo LUT Correction GUI.vi` so "Enter Values" can go live?

## Not determined

- The Misc writer's exact DBL text format (only integral values are in the
  live file); the 4 trailing spaces' origin.
- The Binning ring's stored values (1/2/4 inferred from the ini and
  `Populate Camera[n] Settings from INI`'s "1, Default" case).
- The unrendered False branch of `HHMI - SPIM Acquire button pressed.vi`
  (what 'Acquisition Start-Stop' does while idle).
- The "on" colour of the Misc square buttons and the VI's window title.
