# Utilities tab and the Low-Level Waveform Config

Code: `src/unmscope/gui/utilities_tab.py` (tool grid + host), 
`src/unmscope/gui/waveform_config_panel.py` (the "Waveform" cluster panel),
`src/unmscope/config/waveform_config.py` (the cluster as a dataclass, persisted
in `~/.unmscope/waveform_config.json`). Tests: `tests/test_waveform_config.py`,
`tests/test_utilities_tab.py`.

## Where this comes from in LouisXIV

The reference panels were rendered from `SPIM MAIN.vi` through LabVIEW's COM
interface (LouisXIV.exe closed) into
`H:\UNM_Lightsheet\VI_Diagrams\SPIM\SPIM LV8.6 VIs\SPIM MAIN\`:
`panel_utilities/` (the Utilities tab), `adv_low_level_waveform_config/` (Adv
Setup > Low-Level Waveform Config -- the user asked for it under Utilities),
and `hidden_frames/` (the whole diagram with every case / event frame; an OCR
index of those ~390 frames identified the event cases quoted below).

The Low-Level Waveform Config page is the **"Waveform" cluster** of
`HHMI - SPIM Waveform Config functional global.vi`, the input of
`HHMI - Generate SPIM Waveform.vi` ("Calculate Waveforms" state). Its fields
and defaults are reproduced 1:1 in `WaveformConfig`.

## What is live, what is only shown

| Field(s) | State here | LouisXIV use (VI) |
|---|---|---|
| Fractional Flyback | live -> `build_scan_waveform(flyback_fraction)` | Linear/`SPIM compute Min Rate needed for Flyback`, the ramp flyback |
| Dither Triangle Pulses, Dither Fract. Flyback | live (the Scan Setup Dither box's sweeps / flyback are the same two values; both views stay in step) | dither galvo triangle (`Generate Triangular Waveform`) |
| Pixel / ms | indicator = AO rate (kHz) / Updates per Pixel | `Compute AO rate from Cycle Time.vi` |
| Cam exp (s), Cycle time (s) | indicators = exposure, trigger period | waveform timing cluster |
| X / Xwvfrm / Z / Spiezo / Zpiezo / Dither (value, Size, Pixels) | indicators mirrored from Scan Setup at scan start | `SPIM Waveform to GUI clusters.vi` |
| Z motion | shown; only the default item "Z galvo & piezo" is known, so the combo is greyed; `z_axes_moving()` is ready for the other items | [112] "Z motion" -> Enable-disable |
| Updates/Pix, Fract. Smoothing, delays, Sweep period, Duty, # of Integrations, # of DOE beams, DOE period, X Triangle Pulses, AOTF pulse width / duty, Waveform type, X wave, Z wave, AOTF cycle, 1 exp per, Wait for Zsettle?, Dual View, AOTF sweep mode, Linked / XZcrrct, X Single Direction, Z Bidirectional, Custom Cycle Time, Z Piezo Selector | shown with LouisXIV defaults, greyed | the "Calculate Waveforms" pipeline (below) |

## The rate model: the important difference to port next

Our builder fixes the AO point period (4000 ticks = 10 kHz) and derives the
number of points from the exposure. LouisXIV does the reverse
(`HHMI - Compute AO rate from Cycle Time.vi`, `HHMI - Min AO rate needed.vi`,
`HHMI - SPIM Time per exp.vi`): the fast-axis point count is fixed by
pixels x Updates/Pix plus the flyback points, the **AO rate is computed** as
the minimum that (1) finishes the laser-ON ramp within the exposure and (2)
finishes the whole waveform including the Z flyback within the cycle time,
checked against the card's maximum rate; Pixel/ms = AO rate (kHz) /
Updates/Pix. Porting that (with `Generate SPIM Waveform`'s subVIs under
`DAQ/Waveform/Linear/`: `Calc Single Fast Line Ramp`, `Generate 1 Frame Ramp`,
`Generate S curve flyback for slow axes`, `Generate Slow axes arrays`, `Shift
waveforms by galvo delay`, `Correct X Galvo for XZ coupling`, ...) is what
makes the rest of the cluster live. Until then the greyed fields are display
only.

## Tools grid

18 buttons in LouisXIV's order. Wired: **View TIF stack** (loads a stack as
if just acquired: Images view, Calc, Save all work on it -- LouisXIV's [100]
"View tif" opens its reviewer instead) and **FPGA Scope** (the Waveforms tab
is our FPGA scope). Greyed: Align Laser, Image Reviewer, Calculate PSF, Sample
Stage Control, Resave OME-XML TIFs, Camera Debug Panel, Auto Background
(greyed in LouisXIV too), Reset HW, HW Config, um per V calibration, View Z
Lookup Table, Shift Vslit calibration, X&Z Galvo offsets per AOTF ch, FPGA
Monitor, X Galvo Z Corrections, Imagine Optics. The event cases for each
exist in `hidden_frames/` ([17] Calculate PSF, [42] FPGA Monitor, [45] HW
Config, [68] Resave OME-XML Util, [69] Reset HW, [85] Shift Vslit cal, [101]
View Z Lookup, [107] X Galvo Z Corrections, ...) for when the user picks
which to port.

## Layout, measured

Rendered reference vs ours rendered offscreen, scanned the same way.

Tools grid: 2 columns x 9 rows, 152 x 56 px buttons at page x = 13 / 193,
y = 37 + 72 k -- ours 0 px off on all 18.

Waveform cluster (360 x 588 frame, sub-cluster frames (170,170,170), fields
white, relative to the frame's top-left):

| Element | Reference | Ours | Delta |
|---|---|---|---|
| Xwvfrm | (253,85)-(346,144) | (253,85)-(346,144) | 0 |
| Z | (149,152)-(246,211) | (149,152)-(246,211) | 0 |
| Spiezo | (251,152)-(348,211) | (251,152)-(348,211) | 0 |
| Zpiezo | (149,216)-(246,275) | (149,216)-(246,275) | 0 |
| Dither | (251,216)-(348,275) | (251,216)-(348,275) | 0 |

Deliberate difference: the page lives under Utilities (user's request), so
its origin differs from LouisXIV's Adv Setup nested tab; the classic-grey
cluster look (204,204,204) is the render's.
