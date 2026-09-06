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

11 of LouisXIV's 18 tools, in its reading order; the user dropped Align
Laser, Image Reviewer, Calculate PSF, Auto Background, View TIF stack,
Resave OME-XML TIFs and Shift Vslit calibration (2026-09-05). Wired: **um per V calibration** (opens its own window, LouisXIV's "Microns per Volt Settings", `docs/um_per_volt_calibration.md`),
**Sample Stage Control** (simulated MP-285 panel, `docs/sample_stage.md`),
**Camera Debug Panel** (the `gui/camera_debug_panel.py` module docstring),
**FPGA Scope** (the Waveforms tab), **Reset HW** (LouisXIV's [69] handler
sends the engine to its "Reset HW" state -- reset the DAQ boards, reinitialise
everything; ours stops a run, closes and re-opens the FPGA and the camera
with the same backends and re-applies the settings), **HW Config**
(`docs/hw_config.md`).
Greyed until ported: View Z Lookup Table, X&Z Galvo offsets per AOTF ch,
FPGA Monitor, X Galvo Z Corrections, Imagine Optics. Their SPIM MAIN event cases are in
`hidden_frames/` ([42] FPGA Monitor, [68] Resave OME-XML Util, [69] Reset HW,
[85] Shift Vslit cal, [101] View Z Lookup, [107] X Galvo Z Corrections, ...).

## Layout, measured

Rendered reference vs ours rendered offscreen, scanned the same way.

Tools grid: measured 2 columns x 9 rows (18 buttons), 152 x 56 px at page
x = 13 / 193, y = 37 + 72 k, 0 px off on all 18; after the user's 2026-09-05
removals the grid holds 11 of those 18 (2 columns x 6 rows), same cell size
and pitch, gaps closed.

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

## Channel delays (X galvo / Z galvo / Z piezo) -- live 2026-09-05

Ported from `HHMI - SPIM Shift waveforms by galvo delay.vi` ->
`HHMI - SPIM Number of extrac counts needed for galvo inertia shift.vi`
(sic) -> `HHMI - Add counts to waveforms at beginning and end.vi`.

**The delays are not a resample or a rotate.** Each channel is padded with
*held* values -- copies of its own first sample at the front, its last at the
end -- so a channel with a larger delay starts later inside a block that
stays the same length for every channel. With `d` the signed delay in seconds
and `m` the largest `|d|`:

    total   = round(m x AO_rate)          # LouisXIV's "AO read lag (counts)"
    end_i   = round((1 - d_i / m) x total)
    begin_i = total - end_i

So the axis with the largest delay gets all its padding at the front, an
undelayed axis gets it all at the end, and every channel grows by `total`.
The AOTF/Pockels pair is not computed from a delay at all: LouisXIV wires
`begin = total, end = 0`.

Three things worth knowing:

* **At this rig's defaults the delays do nothing.** X galvo 0.02 us at a
  1000 kHz AO rate is 0.02 samples, which rounds to zero counts. They only
  bite once a delay reaches about half an AO sample -- 33 us at the 15 kHz
  rate a typical 20 ms cycle produces, or 0.5 us at the 1000 kHz cap.
* **Negative delays are refused**, a deliberate divergence. LouisXIV's
  formula gives a negative delay a *longer* block than the other channels
  (X = -4 us alone yields X (-4, 8) against Z (0, 4)), so they no longer line
  up; downstream that truncates rather than failing, and the scan comes out
  quietly misaligned. Emitting a wrong waveform is worse than stopping. The
  panel's minimum for these three fields is 0.
* **Where we diverge on timing.** LouisXIV keeps the AO rate and lets the
  waveform take longer, adding the lag to `Time Per WvFrm (sec)`. Our free
  run drives a fixed trigger period, so the padded block is re-spaced to
  occupy the same cycle instead: same shape, same relative offsets, a
  slightly shorter sample interval.

**AOTF delay stays greyed** and that is on purpose. It is a different
mechanism -- `Calibration/AOTF delay/HHMI - SPIM Calc AOTF delay.vi` and
`... Convert X pix size to AOTF delay.vi`, applied inside
`HHMI - SPIM Generate 1 Line Ramp.vi` -- not this shift. Wiring it to this
code would be wrong rather than merely incomplete.

**NOT verified on the card.** The maths is tested and the block still fits
its cycle, but no delayed waveform has been put on a scope.

## Cycle time -- live, editable (2026-09-06)

The trigger period. Behind LouisXIV's **Custom Cycle Time** tick the field is
typeable and its value is the period; unticked it is an indicator showing the
computed value, exposure x 1.27 (`DEFAULT_FLYBACK_FRACTION`, the user's
measured 27 ms on 100 ms). Either way the camera's own minimum period is a
floor. See `docs/known_issues.md` "Z stack came up short" for why this is the
number that times the acquisition and the exposure is not.
