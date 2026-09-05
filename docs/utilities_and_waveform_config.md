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
| Updates/Pix, Fractional Flyback, Fract. Smoothing, X Single Direction | live -> `louisxiv_waveform.build_louisxiv_waveform` (the fast-axis line and the AO rate) | `Generate Fast axis ramp for DOE scan`, `Fast Axis Cubic Ramp Coeffs`, `Compute AO rate from Cycle Time` |
| Dither Triangle Pulses, Dither Fract. Flyback | live (the Scan Setup Dither box's sweeps / flyback are the same two values; both views stay in step) | dither galvo triangle (`Generate Triangular Waveform`) |
| Pixel / ms | indicator = AO rate (kHz) / Updates per Pixel | `Compute AO rate from Cycle Time.vi` |
| Cam exp (s), Cycle time (s) | indicators = exposure, trigger period | waveform timing cluster |
| X / Xwvfrm / Z / Spiezo / Zpiezo / Dither (value, Size, Pixels) | indicators mirrored from Scan Setup at scan start | `SPIM Waveform to GUI clusters.vi` |
| Z motion | shown; only the default item "Z galvo & piezo" is known, so the combo is greyed; `z_axes_moving()` is ready for the other items | [112] "Z motion" -> Enable-disable |
| delays, Sweep period, Duty, # of Integrations, # of DOE beams, DOE period, X Triangle Pulses, AOTF pulse width / duty, Waveform type, X wave, Z wave, AOTF cycle, 1 exp per, Wait for Zsettle?, Dual View, AOTF sweep mode, Linked / XZcrrct, Z Bidirectional, Custom Cycle Time, Z Piezo Selector | shown with LouisXIV defaults, greyed | the parts of "Calculate Waveforms" not ported (below) |

## The waveform itself: LouisXIV's "Calculate Waveforms", ported

`src/unmscope/hardware/louisxiv_waveform.py` (tests `tests/test_louisxiv_waveform.py`)
replaces the earlier fixed-rate builder for the scan. It is a VI-for-VI port
of the Linear ramp, read from the hidden-frames export of `DAQ/Waveform/*`
and `Waveform/*`:

- **Fast axis line** (`Generate Fast axis ramp for DOE scan`, `Fast Axis Cubic
  Ramp Coeffs`, `Calc Cubic portion of fast axis Ramp`): cubic acceleration of
  `Updates/Pix * ceil(Fract.Smoothing * pixels)` points (a3 = -tau*range/3,
  a2 = tau*range, overshoot 2*tau*range/3), the linear sweep of
  `pixels * Updates/Pix` points, the mirrored deceleration, then the flyback:
  a cubic of `Updates/Pix * ceil(Fract.Flyback * tau * pixels)` points, a linear
  return of `Updates/Pix * ceil(Fract.Flyback * pixels)` points, and the closing
  cubic. X Single Direction off = X Bidirectional: the return is the forward
  line mirrored.
- **AO rate** (`number of points and points per second that Ramp needs`,
  `compute Min Rate needed for Flyback`, `Compute AO rate from Cycle Time`,
  `Check AO rate for ramp wave`, `Max AO Rate` = 1000 kHz): the larger of
  "ON points / exposure" and "line points / cycle time" (Option A; Option B
  with extra counts only when a Z settle time is longer than the X flyback),
  capped at 1000 kHz by reducing Updates/Pix to an even number, then rounded
  to the FPGA's 25 ns tick (`Calculate DMA Settings`). Pixel/ms on the panel is
  the resulting AO kHz / Updates/Pix.
- **Slow axes** (`Generate S curve flyback for slow axes and tile array`,
  `Compute S curve number of points` = return points IQ 3): Z galvo / Z piezo
  constant per slice, moving to the next slice's value along the S-curve over
  the last return-points/3 points of the line.
- The dither galvo keeps the triangle of `Generate Triangular Waveform`.

Live on the panel now: Updates/Pix, Fractional Flyback, Fract. Smoothing,
X Single Direction, Dither Triangle Pulses, Dither Fract. Flyback. Still
display-only: the delays (all 0 -> "No shift" in `Number of extra counts
needed for galvo inertia shift`), Sweep period, Duty, # of Integrations, DOE,
AOTF pulse / sweep / cycle fields (our AOTF is a DC level for the run), Z wave
Sweep, Wait for Zsettle (no Z settle in our FPGA run), Dual View, Sine
waveform, Z Piezo Selector, Linked / XZcrrct.

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
