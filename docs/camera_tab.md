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
- **DCAM "SENSOR MODE" values** for the three mode names
  (`OrcaFlash4Camera.SENSOR_MODE_VALUES`): check with
  `getAllowedPropertyValues("SENSOR MODE")` on the real camera.
- **Subarray units** assumed 4 px for position and size (Orca Flash 4.0); the
  driver's `hposunit/vposunit/hunit/vunit` should be read and used instead.
- The **sensor-mode enum's full item list**: only the three names above were
  recoverable.

## Not wired (greyed)

SubROIs / Full ROI, Dual View mode, Split pix #. Whether they are keepers or
LouisXIV junk is the user's call (cleanup directive).

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
| SubROIs pane | (268,554)-(383,675) | (268,554)-(383,675) | 0 |

Colours (measured): page (250,250,250), box borders black, read-back fields
(240,240,240), editable fields white, button faces (253,253,253).

Deliberate differences from the live panel: the page scrolls (our Hardware
Connection bar above the tabs costs 119 px of page height), and the "Sync
readout" checkbox sits in the Camera Settings box's empty band (LouisXIV keeps
it in the ini).
