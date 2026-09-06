# Camera tab (port of SPIM MAIN.vi's Camera tab)

Code: `src/unmscope/gui/camera_tab.py` (layout + wiring), `src/unmscope/hardware/roi.py`
(the ROI arithmetic), ROI / binning / sensor-mode methods on `hardware/camera.py`.
Tests: `tests/test_roi.py`, `tests/test_camera_tab.py`.

## What each control does, and which VI it comes from

| Control | Behaviour | LabVIEW source (VI_Diagrams) |
|---|---|---|
| ROI Left/Right/Top/Bottom | 1-based inclusive, like the panel. Every edit is coerced: Left/Top clamped into the sensor and snapped **down** to the position unit, size rounded **down** to the size unit; the accepted values are written back. | `DCAM - Coerce ROI.vi`, `DCAM - Coerce ROI size.vi` (Round = Down), `DCAM - Set ROI.vi` |
| # of pixels X/Y | Changing the count grows/shrinks the ROI about its centre by `(new - old) IQ 2` per side. | `HHMI - Adjust ROI based upon change in number of pixels.vi` |
| FOV X/Y | `pixels * pixel size`, pixel size = 6.5 um * binning / mag 30 = 0.2167 um. | `Camera Image Pixel sizes.vi`, `SPIMProject.ini [Detection optics]` |
| Sensor Mode | Normal Scan / Light Sheet / Split View / Rolling Bottom. Dual View mode and Split pix # are only enabled in Split View (the live panel greys them in Normal Scan). LouisXIV's [18] "Cam settings" handler also sets X wave = Sawtooth with duty 0.05 for Light Sheet (waveform config, not ported) and sends Set Camera + Update Cam to the engine. | SPIM MAIN [18], `DCAM - Set Sensor Mode.vi` (cases seen: "Split View", "Rolling Bott..."), `DCAM - Sensor Mode enum.ctl` |
| Actual | exposure read back from the camera, frame time = the trigger period for that exposure, rate = 1000 / frame time, "1 exposure(s)". | panel indicators |
| Binning | API only (`set_binning` 1/2/4, symmetric); not on LouisXIV's Camera tab. | `DCAM - Set Binning.vi` |

When settings reach the camera: immediately if it is connected and idle, and
again at Acquire (`CameraTab.apply_to_camera`, LouisXIV's "DCAM - Set
parameters" at scan start). DCAM refuses subarray changes while a sequence is
armed, and our sequence stays armed between runs, so a change made during that
window is deferred and the armed sequence is stopped at the next Acquire,
before the exposure push (stopping is what loses the exposure, see
`known_issues.md`).

## Assumed, to confirm against LouisXIV / the hardware

- ~~The five buttons~~ -- **resolved 2026-09-05** from a hidden-frames export of
  SPIM MAIN.vi's diagram (`VI_Diagrams/.../SPIM MAIN/hidden_frames`): [9] "All
  pixels?" = (1, CCD X, 1, CCD Y); [5] "512x512"/"1024x1024" and [22] "Center
  ROI" shift the ROI by round(round(CCD/2) - mean(Left, Right)) per axis; [21]
  "Center ROI at XY" = X -/+ (width IQ 2), Y -/+ (height IQ 2) (one pixel wide
  of even sizes, then coerced); [1] "# of image pixels" = the Adjust-ROI VI.
  All then fire [73] "ROI": Value Change (coerce, update # of image pixels,
  "Set Camera" to the engine). `roi.py` now implements exactly these.
- ~~DCAM "SENSOR MODE" values~~, ~~the sensor-mode enum's full item list~~ and
  ~~the subarray units~~ -- **all resolved 2026-09-05**, see below.

## Sensor mode and subarray units -- resolved 2026-09-05

Two sources, and they disagree about what is possible.

**What LouisXIV does.** From the hidden case frames of `DCAM - Set Sensor
Mode.vi` and `DCAM - Get Sensor Mode.vi` (exported 2026-09-05; the visible
frame only ever showed "Split View"). The two VIs agree in both directions,
so the mapping is certain. Note four names set the DCAM `SENSOR MODE`
property and two set `READOUT DIRECTION`, leaving the sensor mode alone:

| LouisXIV name | DCAM property | value | DCAM SDK name for that number |
|---|---|---|---|
| Normal Scan | SENSOR MODE | 1 | AREA |
| Light Sheet | SENSOR MODE | 12 | PROGRESSIVE |
| Split View | SENSOR MODE | 14 | DUALLIGHTSHEET (**not** SPLITVIEW, which is 9) |
| Dual LS | SENSOR MODE | 16 | -- |
| Rolling Top | READOUT DIRECTION | 1 | FORWARD |
| Rolling Bottom | READOUT DIRECTION | 2 | BACKWARD |

So the enum has six items, not the three we had recovered, and LouisXIV's
name for 14 is not the SDK's.

**What this camera will accept.** Measured by `spikes/34_*.py` and
`spikes/35_*.py` on S/N 102668 (C11440-42U32, Hamamatsu adapter 2.2.1.71):

- `SENSOR MODE` allows exactly `AREA` and `SPLIT VIEW`. Writing
  `PROGRESSIVE` is refused. **LouisXIV's Light Sheet (12) and Dual LS (16)
  have no route through this adapter at all**, so `OrcaFlash4Camera` lists
  them in `UNSUPPORTED_SENSOR_MODES` and raises a `CameraError` naming the
  reason rather than writing something wrong.
- `READOUT DIRECTION` is conditional: `DIVERGE` only while the sensor mode
  is `AREA`, but `FORWARD` / `BACKWARD` once it is `SPLIT VIEW`. So Rolling
  Top and Rolling Bottom are reachable only from Split View, which the error
  message says.
- `ScanMode` 1 vs 2 changes neither `ReadoutTime` (0.0333 s either way) nor
  the allowed values.
- **Open:** which DCAM number the adapter's `"SPLIT VIEW"` string writes. It
  is probably 9, where LouisXIV writes 14. Not readable from the adapter.

**Subarray units: 4 for position and 4 for size, confirmed.** ROIs requested
at awkward offsets, read back:

| requested (hpos, vpos, hsize, vsize) | driver returned |
|---|---|
| 1, 1, 2046, 2046 | 0, 0, 2044, 2044 |
| 2, 2, 1022, 1022 | 0, 0, 1020, 1020 |
| 3, 3, 510, 510 | 0, 0, 508, 508 |
| 5, 5, 254, 254 | 4, 4, 252, 252 |
| 7, 7, 130, 130 | 4, 4, 128, 128 |
| 9, 9, 66, 66 | 8, 8, 64, 64 |
| 100, 200, 301, 401 | 100, 200, 300, 400 |
| 1023, 1023, 1025, 1025 | 1020, 1020, 1024, 1024 |

Positions snap **down** to a multiple of 4, sizes round **down** to a
multiple of 4 -- exactly what `coerce_roi` already did.

One difference worth knowing: the driver coerces position and size
*independently*, while `coerce_roi` coerces a *rectangle* the way LouisXIV's
`DCAM - Coerce ROI.vi` does, holding Right/Bottom while Left/Top snap down.
Requesting (2, 2, 1022, 1022) therefore gives (0, 0, **1024**, 1024) from us
and (0, 0, **1020**, 1020) from the driver. Both are valid; ours keeps the
edge the user dragged where they put it. It does not cause a mismatch,
because our output is always 4-aligned, in bounds and idempotent, so the
driver takes it unchanged -- checked over 20,000 random ROIs and locked in
by `tests/test_roi.py`.

## Removed (user's cleanup call, 2026-09-05)

SubROIs / Full ROI, Dual View mode, Split pix # -- LouisXIV greys them in
Normal Scan anyway; not carried over.

## Layout, measured

Live panel captured 2026-09-05 (`SPIM MAIN V4.107.100`), box borders found by
PIL scans for 1-px black runs, fields by exact-colour flood fill. Offsets are
from the tab widget frame; ours rendered offscreen and scanned the same way.

| Box | Reference | Ours | Delta |
|---|---|---|---|
| Camera Settings | (15,83)-(230,448) | (15,83)-(230,448) | 0 |
| ROI | (20,470)-(134,576) | (20,470)-(134,576) | 0 |
| # of pixels | (149,469)-(227,552) | (149,469)-(227,552) | 0 |
| FOV | (150,586)-(233,669) | (150,586)-(233,669) | 0 |
| ROI center | (151,732)-(232,784) | (151,732)-(232,784) | 0 |

Colours (measured): page (250,250,250), box borders black, read-back fields
(240,240,240), editable fields white, button faces (253,253,253).

Deliberate differences from the live panel: the page scrolls (our Hardware
Connection bar above the tabs costs 119 px of page height), and the "Sync
readout" checkbox sits in the Camera Settings box's empty band (LouisXIV keeps
it in the ini).
