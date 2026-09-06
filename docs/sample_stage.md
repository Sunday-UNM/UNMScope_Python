# Sample Stage Control (port of LouisXIV's Sample Stage Control GUI.vi)

Code: `src/unmscope/gui/sample_stage_dialog.py` (the non-modal panel + the
Save Location dialog), `src/unmscope/hardware/stage.py` (XYZStage /
RotationStage ABCs, `SimulatedMP285`, `SimulatedRotationStage`, the MP-285
serial protocol `MP285Protocol` + `MP285Serial` over an injected transport),
`src/unmscope/fileio/stage_locations.py` (LouisXIV's two location files,
`SavedLocations`, the shared `LocationSequence`), `src/unmscope/config/spim_ini.py`
(the UNMScope copy of `SPIMProject.ini`, `Simp285Settings`, `RotationStageSettings`).
Tests: `tests/test_stage.py`, `tests/test_stage_locations.py`, `tests/test_spim_ini.py`,
`tests/test_sample_stage_dialog.py` (offscreen Qt).

LouisXIV opens this panel from the Utilities button `3D Stage Control`
(caption "Sample Stage Control") and from Scan Setup's `Configure` next to
`Multi-location` -- both in SPIM MAIN event case [4] via Launch Asynch VI --
and hides it on Exit. The Python panel is `SampleStageDialog`: construct it
once (`show()` from both buttons, `closeEvent` hides). Nothing here is wired
into `main_window.py` / `utilities_tab.py` yet (integrator's job); the
constructor takes optional `stage`, `sequence` (the shared
`LocationSequence` the acquisition will read for multi-position stacks),
`real_stage_factory`, `rel_offset_provider`, `log`, and emits
`rel_offset_recalled(float)` (LouisXIV's 'Write Relative Offset' to SPIM
MAIN), `sequence_changed()` ('Configure Stack'), `position_changed(x, y, z)`
and `settings_saved(Simp285Settings)`.

## Sources rendered for this port (2026-09-05, LabVIEW COM)

The panel's two other tab pages and every hidden case frame were not in
`VI_Diagrams` before; they were rendered with the recipe in the memory notes
into `VI_Diagrams/SPIM/SPIM LV8.6 VIs/Motion/`:

- `Sample Stage Control (All Axes)/Sample Stage Control GUI/{panel_xyz_settings,
  panel_rotation}/` (tab pages 1 and 2) and `hidden_frames/` (73 frames: 24
  event cases + the consumer loop's ~25 message cases).
- `3D Stage/SIMP-285/SIMP-285 Module/` (+ `hidden_frames/`, 19 frames),
  `SIMP-285 - Calc XYZ Move Time/`, `SIMP-285 3D Stage Settings/`,
  `Settings/SIMP-285 INI FG/`.
- `3D Stage/SIMP-285/SIMP-285_Driver/siMP-285.llb/*` (16 driver VIs) and
  `siGen.llb/*` (4 VIs) -- the Sutter VISA driver, previously excluded from
  the export.
- `3D Stage/{Location Sequence FG, Load Location File, Translate XYZ
  Assignment, Save Location Dialog}/hidden_frames/`.

## What each control does, VI by VI

| Control | Python | LabVIEW source (GUI event case -> consumer message -> VI) |
|---|---|---|
| Current Location (um) readout | live | 'Refresh Position' = `SIMP-285 Module` 'Query Position' |
| Refresh | live | [19] -> 'Refresh Position' |
| Auto Refresh (checkbox, hidden control in LouisXIV) | live, 500 ms QTimer | [23] Timeout: 'Auto Referesh MP' AND queue idle -> 'Refresh Position' |
| Control Location X/Y/Z | live | `Control Location Cluster.ctl` |
| Set Control Loc. | live | [11] 'Current to Control Location' = Current -> Control |
| Go | live, moves the stage | [1] -> 'Validate Position' ("#TODO: add range checking") -> 'Set Position' = `SIMP-285 Module` 'Set Position', Wait for Move = 'Wait for Moves' |
| Wait for Moves (checkbox, hidden control in LouisXIV) | live | tunnel into the consumer loop, read by 'Set Position' |
| Save Location | live | [8] 'Save Control Pos' -> `Save Location Dialog.vi` -> 'Insert Location' / 'Update Location' -> 'Save Locations File' |
| Set Origin | live | [2] "Set current position as origin?" -> `SIMP-285 Module` 'Set Origin' ('o') |
| Saved Locations table | live | MCL; lock/unlock item symbols 42/41; [6] selection -> Recall/Sequence/Remove enabled iff row in 0..N-1 |
| Recall / double-click | live, moves the stage | [9]/[10] -> 'Recall Saved': Control Location := XYZ, Z Rel. Offset -> `rel_offset_recalled` unless NaN, theta -> 'Set Rotation Position' unless NaN, then 'Validate Position' |
| double-click on the symbol column | live | [10] InSymbol -> 'Toggle Lock' + 'Save Locations File' |
| Sequence | live | [15] 'Add to Sequence' -> `Location Sequence FG` Write, 'Configure Stack' (`sequence_changed`) |
| Remove / Remove All | live | [5] "Remove selected location?"; [18] "Remove ALL unlocked saved locations?" |
| Location Sequence table | live | `Location Sequence FG` Load/Read |
| Seq. Recall / double-click, Remove, Remove All | live | [13] 'Recall Sequence'; [12] "Remove selected location from sequence?"; [17] "Remove ALL locations from sequence?" |
| Gen. Grid Sequence | greyed | [22] launches `Generate Multipoint Grid Sequence GUI.vi` (needs the acquisition's image size and Z piezo pixels; multi-position acquisition is out of scope) |
| Locations File / Sequence Locations File paths | live (our copies' paths) | path indicators |
| Simulate? | greyed, checked | connector-pane input; `SIMP-285 Module` 'Init': simulate = ini Simulate OR Simulate? OR NOT Enable?. Becomes live when a `real_stage_factory` is passed |
| XYZ Stage Settings: COM Port, Enable Stage, Simulate, XYZ Assignment, Stage Velocity, Settling Time | live | 'Init Controls' = `SIMP-285 INI FG` 'Read Register' |
| Save Settings | live | [14] 'Update Settings' -> `SIMP-285 INI FG` 'Write INI (input)' -> 'Init HW' (`SIMP-285 Module` 'Init' = reconnect) |
| Rotation Control page (Position, Go, Speed, Write Config, Settling Time, status cluster, Auto Referesh, Refresh, Move Time) | greyed | [3] 'Set Rotation Position', [4] 'Set Rotation Speed' + 'Set Rotation Settling Time' (+ Write INI), [20]/[21] 'Refresh Rotation Status' = `PI 651-03 Module` 'Read Status'. The PI U-651 is `Enable? = FALSE` with no serial number in the ini and not on this rig: `SimulatedRotationStage` only |

Confirmation prompts use LouisXIV's exact strings. `SampleStageDialog._confirm`
is a plain callable so tests answer them.

## The MP-285 (SIMP-285 Module.vi + siMP-285.llb), what was ported

`stage.py`'s module docstring lists the actions frame by frame. In short:
Init = open port (9600 8-N-1, no flow control, CR terminated), 'n', velocity
1000 ("force speed to 1000 temporarily"), low resolution ('L'), the ini
velocity, 'a', 'c'; Query Position = 'c' when idle, else "done?" = time >
expected OR a CR arrived; Set Position = `Translate XYZ Assignment`, 'c', 'a',
'm', optional wait for the CR (timeout = Calc XYZ Move Time), 'c'; Set
Origin = 'o'; Reset = 'r'. The driver's framing: 'c' -> 12 bytes (3 x int32
little-endian microsteps) + CR, x 0.04 um; 'm' + 3 x int32 LE (um x 25) + CR;
any non-CR reply byte = error -285; 's' -> 32 status bytes + CR with the
velocity word at offset 28 (bit 15 = high resolution, 3000 / 1310 um/s
limits); 'V' + uint16 LE + CR; Ctrl-C interrupt ('=' reply, the module has
it disabled: "move interrupt not working").

Pure functions: `translate_xyz_assignment` (all six cases read from the
frames: the case name says which input axis feeds output X, Y, Z),
`calc_xyz_move_time_s` (`SIMP-285 - Calc XYZ Move Time`: max delta / velocity
+ settling), `calc_max_move_time_s` (`Calculate Max Move Time`: rotate the
list by one, max per-move delta; 0 when not enabled).

**Not bench-verified** (the stage is not connected): every byte of the
serial protocol is exercised only against `tests/test_stage.py`'s
`FakeMP285`. Deliberate deviation, to confirm on the bench: after a
non-waited 'm' the module sends 'c' at once; `MP285Serial` re-reads the
position when the move is seen to complete instead, because a real MP-285
answers 'c' only after the move. `SimulatedMP285` is the module's own
simulate branch: position jumps to the target, 'Moving?' for Sim Exp. Move
Time = Calculate Max Move Time([current, new]); the +-0.01 um dice jitter
and the 1 s / 100 ms Reset / Set Origin waits are not reproduced by default.

## Files: interchangeable with LouisXIV

`stage_locations.py` reads/writes `SPIMProject 3D Stage Locations.txt` and
`SPIMProject 3D Stage Sequence Locations.txt` exactly as `Load Location
File.vi` / `Location Sequence FG.vi` Write / the GUI's 'Save Locations File'
do: tab separated, CRLF, trailing CRLF, no header, values as the table's
strings (X/Y/Z %.2f, Theta %.4f, Rel. Offset %.6f or `NaN` from the Save
Location Dialog), column 0 = lock symbol 41/42 (locations) or the running #
renumbered on every write (sequence); 5/6-column rows get a `--` column
appended, other counts are thrown out, a missing file is not an error. Both
backup files round-trip byte for byte (`test_real_backup_files_round_trip`).

Persistence (the user's rule): LouisXIV's files under `SPIM Support files`
are never written. `~/.unmscope/SPIMProject.ini`, `~/.unmscope/SPIMProject
3D Stage Locations.txt` and `~/.unmscope/SPIMProject 3D Stage Sequence
Locations.txt` are byte-for-byte copies made on first use and are the only
files the panel modifies; `spim_ini.write_keys` rewrites only the keys it
owns and keeps CRLF, the `Key = Value` spacing and every unknown section.
(Since 2026-09-05 that is a thin wrapper over the one shared writer,
`config/ini_text.py`, which hw_config and um_per_volt use too.)
The ini's boolean spellings differ (`Enable? = False`, `Simulate = FALSE`);
both are read, `True`/`False` is written.

## Assumed / unknown

- 'Auto Referesh MP' and 'Wait for Moves' exist in the VI (the Timeout
  frame reads them; 'Set Position' reads Wait for Moves) but are not visible
  on any of the three rendered pages (hidden controls). They are shown in the
  empty band right of Go -- **position** still assumed.

  Their **default state is no longer assumed**. Read out of the VI over COM
  on 2026-09-05 (`vi.GetControlValue`, which reports a loaded VI's control
  defaults -- the way to recover a hidden control's default when no render
  can show it):

  | control | LouisXIV default | ours |
  |---|---|---|
  | `Auto Referesh MP` | `True` | on |
  | `Wait for Moves` | `False` | off |
  | `Stage Velocity` | `2500` | 2500 um/s |
  | `Settling Time` | `0.0` | 300 ms, see below |
  | `Simulate` | `False` | False |
  | `Enable Stage` | `False` | False |
  | `COM Port` | `("", 0)` | COM8 from the ini |

  Both guesses were right. The one to watch is Settling Time: the VI's
  control default is 0, but `[SIMP-285 3D Stage] Settling Time (ms) = 300`
  in SPIMProject.ini overrides it at load, so 300 ms is the effective
  default and the control's 0 never takes effect. Velocity, COM port,
  Simulate, Enable and XYZ Assignment are ini-backed the same way; the two
  hidden checkboxes are not, so their VI defaults are the real ones.
- 'MP COM Error - monitor' is likewise hidden until an error; ours is a red
  "COM ERROR" label under the two checkboxes.
- The lock / unlock item symbols are LabVIEW images; we prefix the name with
  a filled / empty square.
- The MP-285 'n' (refresh VFD) reply and the exact Init IO retry loop (3
  attempts, 250 ms) were not reproduced beyond "flush, then 'n'".
- Save Location's Z Rel. Offset default is SPIM MAIN's ZG-P Rel Offset
  global; here `rel_offset_provider` (0.0 until the integrator wires it).

## Dropped (cleanup)

- The `error in` / `error out` clusters at the panel's bottom (VI plumbing):
  the window is 701 x 735 instead of 701 x 805; the Simulate? switch keeps
  its measured place.
- The path indicators' folder glyphs, the empty 'Settings File' path
  indicator on the settings page, the button icons (Refresh / Save Location /
  Recall / Sequence / Remove / Remove All / Go glyphs).

## Layout, measured

Reference: the three COM renders (701 x 805, background (221,221,221), tab
page white, readout black with (0,204,51) digits, listbox header and grid
(204,204,204), selection (0,102,204)). Both sides measured the same way: the
bounding box of non-background pixels inside a window around each element
(scratch script `bbox.py`, windows chosen from row/column colour-run scans).
LabVIEW's system buttons carry a 3 px drop shadow that the reference bbox
includes; our buttons are given that whole rect. Text labels are placed at
the measured text tops but are not in the table (offscreen renders draw
text as boxes).

Ours rendered offscreen (`widget.grab()`), 701 x 735. All 43 elements: delta 0.

| Element | Reference | Ours | Delta |
|---|---|---|---|
| tab page (white) | (10,24)-(573,182) | (10,24)-(573,182) | 0 |
| Save Location btn | (582,98)-(699,131) | (582,98)-(699,131) | 0 |
| Set Origin btn | (581,141)-(698,174) | (581,141)-(698,174) | 0 |
| Saved Locations bevel | (13,212)-(550,376) | (13,212)-(550,376) | 0 |
| Recall / Sequence / Remove btns | (577,218)-(693,250) / (577,262)-(693,294) / (577,306)-(693,338) | same | 0 |
| Remove All btn | (577,350)-(694,383) | (577,350)-(694,383) | 0 |
| Location Sequence bevel | (13,416)-(550,596) | (13,416)-(550,596) | 0 |
| Seq Recall / Seq Remove btns | (575,421)-(691,453) / (575,469)-(691,501) | same | 0 |
| Seq Remove All btn | (577,517)-(694,550) | (577,517)-(694,550) | 0 |
| Gen. Grid Sequence btn | (577,566)-(694,599) | (577,566)-(694,599) | 0 |
| Simulate? switch | (214,686)-(268,712) | (214,686)-(268,712) | 0 |
| XYZ Control: Current Location readout | (19,62)-(442,91) | (19,62)-(442,91) | 0 |
| Refresh btn | (450,62)-(567,95) | (450,62)-(567,95) | 0 |
| Set Control Loc. btn | (339,95)-(444,128) | (339,95)-(444,128) | 0 |
| Go btn | (340,137)-(445,170) | (340,137)-(445,170) | 0 |
| X / Y / Z spin frames | (20,138)-(112,172) / (128,..)-(220,..) / (236,..)-(328,..) | same | 0 |
| X / Y / Z spin fields | (44,144)-(105,165) / (152,..)-(213,..) / (260,..)-(321,..) | same | 0 |
| Settings: COM Port frame / field | (25,49)-(140,82) / (43,54)-(114,73) | same | 0 |
| Enable Stage / Simulate boxes | (159,56)-(171,68) / (276,56)-(288,68) | same | 0 |
| XYZ Assignment combo | (400,51)-(515,71) | (400,51)-(515,71) | 0 |
| Stage Velocity frame / field | (25,111)-(92,145) / (48,116)-(86,138) | same | 0 |
| Settling Time frame / field | (155,111)-(222,145) / (178,116)-(216,138) | same | 0 |
| Save Settings btn | (473,134)-(558,167) | (473,134)-(558,167) | 0 |
| Rotation: Position / Speed / Settling fields | (39,54)-(98,75) / (39,103)-(98,124) / (38,151)-(97,172) | same | 0 |
| Go - Rotation / Write Config btns | (133,49)-(238,82) / (155,116)-(260,149) | same | 0 |
| Rotation Stage Status box | (324,77)-(437,148) | (324,77)-(437,148) | 0 |
| Auto Referesh box | (456,86)-(468,98) | (456,86)-(468,98) | 0 |
| Refresh (rot) btn | (445,111)-(562,144) | (445,111)-(562,144) | 0 |
| Move Time (s) field | (324,153)-(412,174) | (324,153)-(412,174) | 0 |

Save Location Dialog (327 x 429 render), same method, all 10 elements delta 0:
X/Y/Z controls (24,46)-(97,70) / (130,46)-(205,70) / (238,46)-(313,70), Rel.
Offset (24,106)-(93,134), Save Rel. Offset box (106,107)-(129,130), Theta
(24,166)-(93,194), Save Theta box (107,165)-(130,188), Name field frame
(2,224)-(321,256), OK (109,319)-(203,352), Cancel (217,319)-(311,352).

Table internals (measured, applied): header 19 px (204,204,204), rows 18 px
pitch with 1 px grid, Saved Locations columns 152/64/63/68/79/69 px,
Location Sequence columns 26/128/66/64/59/81/71 px, vertical scrollbar band
15 px at x 521-535. Tab strip: 24 px; tab widths are Qt's (not matched).
