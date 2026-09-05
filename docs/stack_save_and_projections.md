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
the dialog), multi-timepoint numbering, multi-position folders, and the exact
line format of `AcqInfo.txt` (the `Companion Metadata Cluster to String.vi`
was not read; ours is `key = value` in ini style).

## Projections (item 2)

| LouisXIV VI | Python |
|---|---|
| `HHMI - Deskew Stack data into XY Max projection.vi`: shift slice *k* by `k * X step (pix)` -- integral part as an array offset, fractional part by interpolating neighbouring pixels -- into a canvas widened by `(# slices - 1) * X step`; max-combine | `xy_max_projection` |
| `HHMI - Get Max Projection of slanted stack.vi`: per slice, the column max (one row per slice -> XZ) and the row max (-> YZ) | `xz_yz_max_projections` |
| `HHMI - Deskew Max column data into XZ Array.vi`: the same shear on the XZ rows (YZ has X collapsed, no shear) | same |
| `HHMI - Calc XZ and YZ Max Projection from slanted stack.vi`: Z rescaled by `Zpixsize / X and Y pixsize`, both flipped horizontally, U16 out | same |

Geometry. `[Sample stage] Angle between stage and bessel beam (deg) = 45`.
A stage step `ds` per slice moves the image laterally by `ds*cos(45)` and in
depth by `ds*sin(45)`; in pixels `X step = ds*cos/pix`, `Z/XY = ds*sin/pix`.
**These two formulas are inferred from the geometry, not read off a diagram**
(the VIs take `X step (pix)` and the ratio as inputs computed upstream from the
per-slice "Sample Piezo" positions). Pixel size is `[Detection optics]`:
6.5 um sensor pitch / Magnification 30 = 0.2167 um (= the Camera tab's 444 um
FOV over 2048 px). `ds` is the Scan Setup Z Piezo Interval.

One deliberate deviation: the sheared canvas width uses `ceil` where LabVIEW
rounds to I32, so a fractional final shift never spills intensity off the
right edge (at most one pixel wider, never narrower). The sub-pixel shift is
an exact two-tap spread that conserves intensity; a test pins both.

**Direction of the shear.** LouisXIV's `X step (pix)` is signed by the
per-slice Sample Piezo positions; which way the image content actually walks
across the sensor for a positive step depends on the optics and is not in the
ini. Python therefore measures it from the data (`estimate_drift_sign`: the
cross-correlation lag between the first and last slices' column-max
profiles) and shears against it, so a stationary feature collapses to a spot
whichever way it drifts. `stack_projections(..., direction=+1/-1)` overrides
the measurement. This is a deliberate departure, and the reason for it was
found the hard way: with a fixed sign the first synthetic bead's streak
doubled (38 -> 80 columns) instead of collapsing.

DeSkew unticked = zero shear = a plain max along each axis.

## Still open
- Item 1 Camera tab, item 4 Low-Level Waveform Config (under Utilities),
  item 5 the Utilities tools -- see ROADMAP.md.
- The projections have only been checked on synthetic and simulated stacks;
  the sign of the shear against a real slanted stack should be confirmed
  with a real acquisition (a point-like feature must collapse, not smear).
