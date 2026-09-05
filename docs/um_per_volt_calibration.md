# um/V calibration tab (port of `Microns per Volt Settings GUI.vi`)

Code: `src/unmscope/config/um_per_volt.py` (the cluster, the ini copy and its
section writer), `src/unmscope/gui/calibration_tab.py` (the tab),
`src/unmscope/config/calibration.py` (now takes its um/V numbers from the
cluster). Tests: `tests/test_um_per_volt.py`, `tests/test_calibration_tab.py`.

LouisXIV opens this as an independent window from SPIM MAIN.vi's Utilities
button "Edit um/V Cal" (event case [31], `SPIM MAINd139.png`, an async launch
with nothing passed in). A left tab next to Scan Setup / Camera / Utilities
was tried first; the user's 2026-09-05 call was to open it the way LouisXIV
does instead -- its own non-modal window (`MainWindow.calibration_window`, a
`QWidget(self, Qt.Window)` sized to `CalibrationTab.minimumSize()`), raised
by the Utilities grid's "um per V calibration" button
(`_show_calibration_tab`). `saved` (see below) is connected the same way
either hosting choice would need it.

## What each element does, and which VI it comes from

| Element | Behaviour | LabVIEW source (VI_Diagrams + hidden-frame renders) |
|---|---|---|
| The nine numeric fields (cluster "Microns to Volt calibrations") | Editable, shown in LabVIEW's default numeric format (`2000`, `0.5`, `NaN`). Field labels are the ini keys verbatim, including the double space in `Galvo position  cmd V/pos V`. Committing an edit (Enter / focus out) recomputes the indicator; nothing is written until Save. | `Constants/Microns to Volt cluster.ctl`; GUI event [1] "Microns to Volt calibrations: Value Change" -> state "Update Values" |
| `Galvo Pos um/Galvo Pos Volt` (indicator) | `Galvo cmd X um/X Volt` x `Galvo position  cmd V/pos V`, read-only. (The saved panel shows a stale 52.3861; a saved indicator value is whatever was there last.) | state "Update Values" (`GUId3.png`); `Common/Constants/HHMI - Microns per Volt.vi` computes the same product for its consumers |
| Save | Commits pending edits, writes all nine keys into `[Microns to Volt calibrations]` of the UNMScope ini copy, reloads a `Calibration` from that file and emits `saved(Calibration)`. LouisXIV then sends `"Force Waveform Recalc"` to SPIM MAIN, whose handler sets `Force Recalc? (F)` = TRUE and pushes "Calculate Waveforms" (`SPIM MAINd3.png`); the window's counterpart is to store the emitted Calibration as `self.calibration`, which the next Acquire's waveform build reads. An ini write failure is logged (LouisXIV's Save frame does not wire the error line at all). | event [0] "Save" (`GUId5.png`) -> `HHMI - SPIM microns per volt calibrations.vi` (Write) + `Send Message to GUI.vi` |
| Revert | **UNMScope addition**: reload the nine values from the ini copy. A tab has no dialog lifecycle, so this is the way to discard edits. It sits in the measured rect of LouisXIV's Close button. | -- |
| Close | **Dropped**: pushes "Exit" -> `FP.Close`, meaningless for a tab. | event [2] "Close" (`GUId4.png`), states "Exit" (`GUId8.png`) |
| "Initialize" / "Initialize Variables" / "Property Nodes" | Initialize pushes [Initialize Variables, Update Values, Property Nodes]; Initialize Variables reads the cluster from the constants VI (first call: the ini) -> `CalibrationTab.reload()`; Property Nodes is an empty frame (no-op). | `GUId.png`, `GUId1.png`, `GUId2.png` |
| "Error" / "Invalid State" | Simple Error Handler dialog / debugging prompt then Exit -- state-machine plumbing, not ported. | `GUId7.png`, `GUId9.png`, `GUId10.png` |
| error in / error out clusters | Connector-pane terminals sitting off-window in the print (x ~967 and ~1747); not user-facing, not ported. | -- |

### The cluster and who consumes each value

`MicronsToVolt` mirrors the typedef cluster 1:1 in panel order; each field's
`metadata["key"]` is the ini key and `metadata["consumer"]` what uses it in
UNMScope today. Defaults are the cluster's saved control values (53, 0.5,
NaN, 53, -10, 1, 10, 10, 0 -- the same on the GUI panel, the constants VI's
panel and `SPIM - RemoteAcqProject.ini`; the `DefVal` property the missing-key
path really writes cannot be read from a render, so "plausible, not
measured").

| Ini key | Live ini (2026-09-05) | LouisXIV consumer | UNMScope consumer |
|---|---|---|---|
| `Galvo cmd X um/X Volt` | 2000.000000 | `Convert um array to voltage array` "X": V = um / (um/V) + (Xmin+Xmax)/2, then the X Galvo LUT when enabled | X galvo sweep, `Calibration.x_galvo` (AO1) |
| `Galvo position  cmd V/pos V` | 1.000000 | only the derived Galvo Pos um/V (`Smallest DAQ V and um steps`: "Smallest Galvo step read") | the indicator only |
| `Galvo cmd Y um/Y Volt` | NaN | "Y" case; no Y galvo on this rig | none (stored) |
| `Galvo cmd Z um/Z Volt` | 7.000000 | "Z" case, ZGalvo selector | Z galvo steps, `Calibration.z_galvo` (AO0) |
| `Zpiezo um/Zpiezo Volt` | 8.000000 | "Z" case, ZPiezo selector | Z piezo steps, `Calibration.z_piezo` (AO2) |
| `XTile um/V` | 1.000000 | `Generate Tile array for ramp`, Tile Galvo cal VIs | none yet (`Calibration.x_tile` exists; the Tiling AO has no pin in the io map) |
| `SamplePiezo um/SamplePiezo Volt` | 100.000000 | "Z" case, SampleP selector | none (`Calibration.sample_piezo` exists; the sample piezo is not an FPGA AO on the deployed bitfile, SIM900 slot -1) |
| `Dither Galvo um/V` | 10.000000 | "D" case | Dither triangle, `Calibration.dither_galvo` (AO4) |
| `Z Piezo2 um/V` | 0.000000 | `Link Z Galvo to Z Piezo using Z lookup table` when `[Misc Settings] Enable Z Piezo 2 (Dither Chnl.)` = TRUE (FALSE here) | none (stored) |

All nine stay editable, as in LouisXIV (the dialog only ever writes the ini;
it drives nothing itself). Each field's tooltip states its UNMScope consumer
or that it is stored for parity only.

## Persistence (the user's decision)

Python never writes LouisXIV's `H:\UNM_Lightsheet\UNMScope_Source\SPIM\SPIM
Support files\SPIMProject.ini`. On first use it is copied byte for byte to
`~/.unmscope/SPIMProject.ini` (`ensure_unmscope_ini`); the tab reads and
writes only that copy. Reading goes through `configparser` (`optionxform=str`,
`interpolation=None`, `strict=False`). Writing (`MicronsToVolt.save`) rewrites
**only** the nine key lines of `[Microns to Volt calibrations]`, in place,
keeping the file's `Key = Value` spacing and CRLF endings, appending keys the
section lacks (or the whole section if absent) and copying every other byte
verbatim. A `configparser.write` round trip was rejected on purpose: it would
drop `# of beams = 10` and `# Pts (Default) = 5/11` as comments, strip the
trailing spaces on the `[Misc Settings]` lines and re-space every line.
Numbers are written as LabVIEW's `Write Key (Double)` writes them in the live
file: `%f` with six decimals, `NaN` literally (`Inf`/`-Inf` likewise).

Two deliberate differences from LouisXIV: a missing key yields the default in
memory but is not written back on read (`INI Read write dbl` writes the
default into the file when `Found? = FALSE`; here only Save writes, and Save
writes all nine keys), and when the ini is missing altogether
`load_calibration` keeps its long-standing 2026-09-03 fallbacks (2000 / 7 / 8
/ 10 / 100 / 1) for the six keys it always read rather than LouisXIV's
cluster defaults -- a pre-existing choice of `calibration.py`, kept so the GUI
behaviour does not change; the user may want to pick one.

`Calibration` now carries the whole cluster as `Calibration.um_per_volt`,
and its `Axis.um_per_volt` values are taken from that same object, so the
tab, the waveform builder and the ini agree by construction.
`MicronsToVolt.apply_to(cal)` produces an updated Calibration without a file
round trip; the tab's `saved` signal carries the reloaded one.

## Layout, measured

Reference: the COM render `VI_Diagrams/SPIM/SPIM LV8.6 VIs/GUI/Microns per
Volt Settings GUI/Microns per Volt Settings GUIp.png` (1901 x 593 -- the
print is the whole panel; the visible window bounds are unknown, LouisXIV was
not running). Bevels and fills found by PIL row/column colour-run scans and
exact-colour bounding boxes; ours rendered offscreen (`widget.grab()`) and
scanned the same way. Page origin **ASSUMED** `(OX, OY) = (0, 320)`: x = the
render's pane edge, y chosen so the cluster label sits 4 px below the page
top. Every number below is render px minus that origin.

| Element | Reference (page px, x0,y0,x1,y1) | Ours | Delta |
|---|---|---|---|
| field k bevel (170), k = 0..8 | (4, 26+20k, 78, 45+20k) | same, all nine | 0 |
| field k white interior | (5, 27+20k, 77, 44+20k) | same, all nine | 0 |
| indicator fill (221) | (315, 34, 384, 53) | (315, 34, 384, 53) | 0 |
| indicator bevel (170) | (314, 33, 385, 54) | (314, 33, 385, 54) | 0 |
| multiply triangle fill (255,255,204) | (278, 35, 295, 53) | (278, 35, 295, 53) | 0 |
| bracket lines (black) | (250, 35)-(276, 35), (250, 54)-(276, 54) | same | 0 |
| cluster bevel (204) top / left | (2, 24, 248, 25) / (2, 24, 3, 206) | same | 0 |
| Save button (outer bevel) | (106, 238, 190, 271) | (106, 238, 190, 271) | 0 |
| Revert = LouisXIV's Close slot | (238, 238, 322, 271) | (238, 238, 322, 271) | 0 |

Text positions (not compared pixel-wise; offscreen renders show glyph boxes):
cluster label text bbox x0..126 y324..337 -> label rect (0, 2, 170, 16); row
labels start x82 (row-0 text y351..359) -> (82, 26+20k, 168, 20); indicator
label text x316..455 y340..348 -> (315, 16, 150, 16); button captions
centred. Colours measured: page white, field bevel (170,170,170), cluster
bevel (221,221,221) outside / (204,204,204) inside (the right and bottom
sides are the light side of the bevel and vanish into the white page), the
indicator (221,221,221), the multiply triangle (255,255,204).

Deliberate differences: Close dropped, Revert added in its rect; the `x` and
`=` glyphs of the decoration are drawn as lines, not text; the panel's 466 px
width exceeds the 406 px tab column (scroll, or the integrator may wrap the
indicator label -- the user's call).

## Open questions / not determined

- The dialog's real window size and origin (only the full panel print
  exists); the assumed origin only matters if the tab is to be compared
  against a live capture.
- Missing-key fallback: LouisXIV's cluster defaults vs `calibration.py`'s
  2026-09-03 numbers (see above).
- X galvo: LouisXIV's `Convert um array to voltage array` adds
  (Xmin+Xmax)/2 (0 V with today's symmetric limits) and, with `[Misc
  Settings] Enable X Galvo Correction LUT = TRUE`, runs the 19-point
  `SPIMProject XGalvo_LUT.txt` through `Interpolate 1D` -- neither is in the
  Python X path (out of this tab's scope; the out-of-range rule of the LUT
  for today's millivolt sweeps is unknown).
- Whether the tab should refuse Save while a run is armed (LouisXIV's dialog
  can only be used between scans in practice); not enforced here -- the
  window can call `setEnabled(False)` on the tab during a run.
