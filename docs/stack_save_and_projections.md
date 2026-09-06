# Saving stacks and the Stack Projections (Calc) -- the LouisXIV way

Ported 2026-09-04 from the LabVIEW block diagrams in
`H:\UNM_Lightsheet\VI_Diagrams` (rendered from the source on 2026-08-28).
Code: `src/unmscope/fileio/tiff_stack.py`, `src/unmscope/analysis/projections.py`,
the Save/Calc section of `gui/main_window.py`. Tests: `tests/test_tiff_stack.py`,
`tests/test_projections.py`, and the end-to-end cases in
`tests/test_gui_flow_fake_fpga.py`.

## Foundation: the stack is retained

The GUI used to drain the camera and keep only the newest frame. A Z-stack
now keeps every accepted slice in memory (`MainWindow.acquired_stack()`,
oldest first, warm-up/stale frames excluded), bounded by the FPGA's exact
trigger count. Continuous scan is unbounded and retains nothing.

## Saving (items 3)

| LouisXIV VI | What it does | Python |
|---|---|---|
| `HHMI - SPIM Save Image.vi` | prompt for a path, convert to U16, call the TIFF stack writer | `_on_save_image`, `_save_stack` |
| `Save multiple images with metadata to new tiff stack.vi` | one multi-page TIFF, **uncompressed**, first page saved then each page appended via its IFD; **OME-XML in the first page's ImageDescription** | `save_tiff_stack(..., ome=True)` (tifffile) |
| `Build Image Path.vi` | `<base>_CH%02d_%06d.<ext>`; `position %d` subfolder only with "Save Multi-position separate folders" | `stack_filename`, `stack_path` |
| `Image File Type to File Extension.vi` | TIFF -> `tif` | `TIFF_EXTENSION` |
| `Find Next Experiment Folder Number.vi` | highest `<Prefix><N>` folder (prefix "Cell", e.g. `Cell24`) -> `Cell25` | `MainWindow.next_experiment_folder` |
| `Prompt for Save Path.vi` | a save dialog when saving is on | `_ensure_data_dir` (once per session) |
| `Write Companion Metadata File.vi` | `AcqInfo.txt` next to the images, built from the settings | `write_acq_info`, `_acq_info` |

Behaviour: with **Save Files** ticked, a *complete* Z-stack is written to
`<data folder>/Cell<N>/img_CH<row>_000000.tif` plus `AcqInfo.txt`. Partial
stacks (stopped early, errors) are not saved -- LouisXIV's "Close Files and
Delete Partials". The CH index is the one enabled Excitation row; the
timepoint is 0 (Timepoints = Single). The per-view floppy buttons save that
projection as `..._<XY|YZ|XZ>MIP.tif`; the Images tab's "Save Image" prompts
for a path and writes the newest frame. The user manual: "Save File Type:
TIFF (default) ... Save OME-XML: only valid when File Type = TIFF."

Not yet matched: the `Base file` name (fixed to `img`; LouisXIV takes it from
the dialog), multi-timepoint numbering, and multi-position folders.

## AcqInfo.txt: read 2026-09-05, and ours does not match

`Companion Metadata Cluster to String.vi` has now been read (26 hidden case
frames). The finding is blunt: **our `AcqInfo.txt` shares not one key name
with LouisXIV's.** Ours was invented; the 2026-09-04 directive says file
formats come from the source.

The file is a for-each over `Companion Metadata File Fields Enum`, one line
per field, in this order — note the enum's order is not the cluster's, and
two key names differ from their cluster fields (`AOTFCycleMode` for "AOTF
cycle mode", `StageAngle_deg` for `Angle_deg`):

| # | key | format | do we have it? |
|---|---|---|---|
| 1-3 | `SizeX_px` `SizeY_px` `SizeZ_px` | `%s = %d` | yes (ROI width/height, slice count) |
| 4-6 | `PhysicalSizeX_um` `PhysicalSizeY_um` `PhysicalSizeZ_um` | `%s = %.3f` | yes (xy pixel size; Z interval) |
| 7 | `Timepoints` | `%s = %d` | yes (always 1 today) |
| 8 | `Cameras` | `%s = %d` | yes (1) |
| 9 | `Channels` | `%s = %d` | yes (1, one laser at a time) |
| 10 | `AOTFCycleMode` | `%s = %s` | enum, "per Z" default |
| 11 | `TimeIncrement_s` | `%s = %f` (6 dp) | yes (trigger period) |
| 12 | `Username` | `%s = "%s"` | **no panel field** |
| 13 | `CellLabeling` | `%s = "%s"` | **no panel field** |
| 14 | `CellType` | `%s = "%s"` | **no panel field** |
| 15 | `ExperimentDescription` | `%s = "%s"` | **no panel field** |
| 16 | `Fluor` | elements `"%s"`, COMMA-joined | **no panel field**, written empty |
| 17-18 | `ExcitationWavelength_nm` `EmissionWavelength_nm` | elements `%d`, COMMA-joined, unquoted | excitation yes, emission empty |
| 19 | `FilterType` | `%s = "%s"` | **no panel field** |
| 20 | `CamExposure_s` | `%s = %f` (6 dp) | yes, converted from ms |
| 21 | `Multi-positionAcq` | `TRUE` / `FALSE` | no, always FALSE |
| 22-24 | `PositionX_mm` `PositionY_mm` `PositionZ_mm` | `%s = %.3f` | skipped (single position) |
| 25 | `StageAngle_deg` | `%s = %.4f` | yes |

Formats by type: integers `%d`, floats `%.4f`, strings **quoted** `"%s"`,
string arrays as quoted elements joined, booleans as text. Each case also
carries a `skip?` flag, so a field can be left out of the file entirely
rather than written empty.

**Implemented 2026-09-05, option (b), the user's choice**: LouisXIV's keys
and order, then our own settings under a `[UNMScope]` heading.

`fileio/tiff_stack.ACQ_INFO_FIELDS` is the field table; `render_acq_info`
formats it. Points worth keeping in mind:

* **The per-field formats really do differ.** `%.3f` for the physical sizes
  and stage positions, `%.4f` for `StageAngle_deg` and nothing else, `%f`
  (six decimals) for the two time fields, `%d` for integers. An early guess
  that everything was `%.4f` was wrong on most fields.
* **`skip?` is wired to `Multi-positionAcq`.** `PositionX/Y/Z_mm` and
  `StageAngle_deg` each run that boolean through a NOT into the VI's skip
  output, so a single-position acquisition omits those four lines rather
  than writing zeros. We do the same.
* **Fields whose panel controls were never ported are written empty**, not
  omitted -- LouisXIV writes them unconditionally, and an empty panel field
  there produces an empty value here. Only the position fields really skip.
* **Arrays are comma-joined**, elements quoted for strings (`"GFP","RFP"`)
  and bare for the wavelengths (`488,561`).
* **`CamExposure_s` is seconds**; the panel's exposure is ms.

The `[UNMScope]` block below carries Mode, Trigger mode, the excitation
percentage, the Z start/end range and the camera model and serial -- what
this acquisition used that LouisXIV's fields have nowhere to put. Keeping
them rather than dropping them was the user's call; fencing them under
their own heading keeps everything above it LouisXIV's.

## Projections (item 2)

| LouisXIV VI | Python |
|---|---|
| `HHMI - Deskew Stack data into XY Max projection.vi`: shift slice *k* by `k * X step (pix)` -- integral part as an array offset, fractional part by interpolating neighbouring pixels -- into a canvas widened by `(# slices - 1) * X step`; max-combine | `xy_max_projection` |
| `HHMI - Get Max Projection of slanted stack.vi`: per slice, the column max (one row per slice -> XZ) and the row max (-> YZ) | `xz_yz_max_projections` |
| `HHMI - Deskew Max column data into XZ Array.vi`: the same shear on the XZ rows (YZ has X collapsed, no shear) | same |
| `HHMI - Calc XZ and YZ Max Projection from slanted stack.vi`: Z rescaled by `Zpixsize / X and Y pixsize`, both flipped horizontally, U16 out | same |

**Where the numbers come from -- read off the diagrams, not derived.**
Callers were found by grepping the `.vi` files: `HHMI - Get Max Projection of
slanted stack.vi` (from SPIM MAIN / Reviewer) calls the XY and XZ/YZ VIs.

- **XY** (`Get XY Max Projection of slanted stack`): the `Sample Piezo` position
  is read from image[1] and image[0], subtracted, and passed through
  **Absolute Value**. So the XY shift per slice is **`|S[1] - S[0]|`, in the
  position's own units** -- that VI has no link to any pixel-size VI. The
  canvas is widened by `(n-1)*step` converted **To I32** (round half to even;
  Python's `round()` matches).
- **XZ / YZ** (`Deskew Max column data into XZ Array`): `S step = S[1]-S[0]`
  (**signed**), `X step (um) = S step * cos(STAGE ANGLE)`, `Z step (um) =
  S step * sin(STAGE ANGLE)`, `X step (pix) = X step (um) / pixel size`,
  `Zpixsize/XYpixsize = Z step (um) / pixel size`.
- **Pixel size** (`Image Acq/High level/Constants/Camera Image Pixel sizes`):
  `CCD pixel pitch * Binning / Mag` = 6.5 um * 1 / 30 = 0.2167 um
  (`Detection.pixel_size_um(binning)`).
- `STAGE ANGLE` = `[Sample stage] Angle between stage and bessel beam (deg)` = 45.
- `S positions` in the GUI: Scan Setup Z Piezo start, stepping by the interval
  towards the end (signed), one per slice -- the values LouisXIV reads back
  from each image's Position cluster.

An earlier version of this port derived the X-step formula from geometry and
then *measured* the shear direction from the data; both were replaced by the
above. The asymmetry (XY unconverted and absolute, XZ signed and in pixels) is
LouisXIV's, reproduced as-is.

**The one thing the diagrams cannot settle:** whether "Find shifted pixel
value" moves content to the right or the left of the array for a positive
step. This port shifts right. LouisXIV works on this rig, so on a real slanted
stack a point-like feature must collapse after Calc; if it smears, that single
convention is the thing to flip.

DeSkew unticked = zero shear = a plain max along each axis.

## Still open
- Item 1 Camera tab, item 4 Low-Level Waveform Config (under Utilities),
  item 5 the Utilities tools -- see ROADMAP.md.
- The projections have only been checked on synthetic and simulated stacks;
  the sign of the shear against a real slanted stack should be confirmed
  with a real acquisition (a point-like feature must collapse, not smear).
