# How LouisXIV's Cam exp / Cycle time / Custom Cycle Time actually work

Source read of 2026-09-06, from the LabVIEW VIs and SPIM MAIN's 392 hidden
event/state frames. Written up because it **contradicts what the Python port
currently does**, and the contradiction should not be lost.

> **STATUS: UNVERIFIED.** The adversarial verify pass (two independent
> checks per claim -- one refuting, one re-deriving) never ran: it died on a
> model usage limit after the readers and synthesizer finished. So these are
> **single-path readings with frame citations, not confirmed facts**. Every
> claim below is falsifiable by opening the cited frame. Do NOT change the
> widgets on this alone -- re-run the verify pass first. The house rule is
> that LouisXIV's source is the authority, and an unverified reading of it
> is not yet a reading of it.

## Why this matters: three ways the port disagrees

| | Python does now | LouisXIV, per this read |
|---|---|---|
| **Cam exp** | typing it SETS the camera exposure | it is WRITTEN FROM the camera; typing it only rebuilds the waveform and never reaches DCAM |
| **Cycle time, typing** | ticks Custom Cycle Time for you | does NOT tick Custom; the tick is a separate choice |
| **Cycle time, computed** | `exposure x 1.27` | `max(exposure, camera_cycle - 500 ns)` -- the camera's own frame period, no flyback fraction anywhere |

The 1.27 came from the user's own working value (0.1 -> 0.127) and it does
fix the dropped frames, measured on the Orca. But if this read is right, it
is not how LouisXIV arrives at a cycle time, and the 27% is a property of
this rig's galvo rather than a constant in the source. Worth reconciling:
`camera_cycle` for a 100 ms full-frame SYNCREADOUT exposure is ~100 ms, so
LouisXIV's floor would give ~100 ms, NOT 127 -- yet 0.127 is what the user
runs and 0.100 is what halved the count on a sub-array. That tension is
unresolved and is the first thing the verify pass should attack.

## Claims, as read (question / status / evidence)

### Q1 -- established

'Cam exp (s)', 'Cycle time (s)' and 'Custom Cycle Time' are three ordinary elements of the typedef cluster HHMI - SPIM Waveform cluster.ctl, and SPIM MAIN's 'Waveform' is a cluster CONTROL, so all three are user-typable controls (white numeric boxes / checkbox), never indicators. They are overwritten only by whole-cluster or property writes: the 'Calculate Waveforms' state writes SCAN Make Wavefrm's output cluster back over the control (local 'Waveform'), and the engine's 'Set Camera' state writes 'Cam exp (s)'.Val(Sgnl) and 'Cycle time (s)'.Value from the 'Camera Recalc Wvfrm?' sub-VI.

*Evidence:* HHMI - SPIM Waveform cluster / hidden/HHMI - SPIM Waveform clusterp.png (all numerics white, Custom Cycle Time checkbox); HHMI - SPIM Make Ramp Waveform / ...Waveformp.png (In cluster fields RGB 255 vs Out cluster RGB 221); SPIM MAIN / adv_low_level_waveform_config/SPIM MAINp.png; SPIM MAIN / SPIM MAINd2.png (Waveform control -> SCAN Make Wavefrm -> output -> local 'Waveform' write + GLOBAL 'Waveform Config'); SPIM MAIN / SPIM MAINd340.png (Cam exp (s) Val(Sgnl), Cycle time (s) Value, 6x crop by the SPIM MAIN reader)

### Q1 -- established

Cam exp (s) is NOT the exposure the Linear waveform is built for. Make Ramp Waveform copies it into the 'Exposure (sec)' field of the Waveform timing cluster, but in HHMI - Min AO rate needed that field is left UNWIRED: the single DBL input of '# that Ramp needs' (HHMI - SPIM number of points and points per second that Ramp needs, whose only DBL control is 'Exposure (sec)') is fed from a junction on the 'Cycle (sec)' row. So rule 1 ('finish ON pattern during exposure') is really ON points / Cycle time, and Cam exp has no influence on the AO rate or on Time Per Trigger. Two readers reported Exposure as wired; the 6x crop shows the junction dot on the Cycle row and no wire leaving the Exposure row, agreeing with the third reader's 8x zoom.

*Evidence:* HHMI - Min AO rate needed / HHMI - Min AO rate neededd.png (this synthesis: 6x crop (395,115)-(565,295): orange wire into '# that Ramp needs' branches at a junction dot on 'Cycle (sec)'; 'Exposure (sec)' row has no wire); HHMI - SPIM number of points and points per second that Ramp needs / ...needsp.png (controls: Bidirectional, AO points per fast line, Exposure (sec), points used for fast axis return, Virtual Confocal); HHMI - SPIM Make Ramp Waveform / ...Waveformd.png (Cam exp (s) -> 'Exposure (sec)' bundle)

### Q1 -- established

The only production consumers of Cam exp (s) are: (a) HHMI - Generate settings for AO and AI FPGA, case '1 exp per' = "Point" ('PSF mode'): AO clock rate = 1 / Cam exp (s) -> 'AO ticks between points'; in "Z plane" the DMA AO rate is used and Cam exp is ignored; (b) SPIM MAIN 'Calculate Waveforms': Cam exp (s) x 1000 -> X Wvfrm graph Cursor.PosX (display only); (c) HHMI - Camera times to waveform times: compared (!=) with the camera's Exp(s) to raise 'Wvfrm needs to be recalc'd?'. No DCAM or camera VI ever receives it.

*Evidence:* HHMI - Generate settings for AO and AI FPGA / ...FPGAd.png (Point case: 1/x on Cam exp (s)) and hidden/...FPGAd1.png ('Z plane', 'Normal imaging'); SPIM MAIN / SPIM MAINd2.png (Unbundle Cam exp (s) -> x1000 -> X Wvfrm Cursor.PosX); HHMI - Camera times to waveform times / ...timesd.png ('Current Waveform Cam exp (s)' -> != -> OR -> Wvfrm needs to be recalc'd?)

### Q1 -- established

Cam exp (s) is WRITTEN from the camera, not the other way round. Set Camera (engine) -> 'Camera Recalc Wvfrm?' = HHMI - Calculate if camera needs the waveform to be recalculated -> 'Camera Wvfrm times' = HHMI - Camera times to waveform times: Cam exp (s) out = Camera settings.Exp(s) (the sensor-mode case is selected by an enum CONSTANT 'Normal Scan', so the "Light Sheet" branch with 'Lightsheet Exposure Adj %' is unreachable). SPIM MAIN writes it with Val(Sgnl) only when (Cycle time out != current) OR (Cam exp out != current) OR global 'Force Recalc? (F)'; the signalling write fires [104] 'Waveform': Value Change -> 'Calculate Waveforms'. A second writer exists inside calibration/focus scan modules: HHMI - Change waveform and update camera.vi bundles Cam exp (s) <- Exp(s) and Cycle time (s) <- Cycle(s) from the camera wrapper's Setup output, unconditionally ('Set exposure time.', 'Recalc waveform with new timing.'), and its callers' returned Waveform cluster is written to the Waveform global at scan end.

*Evidence:* HHMI - Camera times to waveform times / ...timesd.png (this synthesis: 6x crop (360,90)-(510,150): 'Normal Scan' enum with dark corner flag and no terminal border wired to the '?' selector; Exp(s) tunnel straight through to 'Cam exp (s)'); hidden/...timesd1.png (Light Sheet case, dead); HHMI - Calculate if camera needs the waveform to be recalculated / ...recalculatedd.png; SPIM MAIN / SPIM MAINd340.png + d342.png (gated True case, empty False); HHMI - Change waveform and update camera / ...camerad.png (Unbundle Exp(s)/Cycle(s) -> Bundle Cam exp (s)/Cycle time (s) -> SCAN Make Wavefrm); grep of binaries: callers = Adaptive Optics Config, Focus Z Piezo, tiling galvo cal (2), Z step lookup cal, RelZ autofocus (2) scan modules; SPIM MAIN / SPIM MAINd360/d364/d365/d366.png (Waveform global written from those SCAN VIs)

### Q1 -- established

The DCAM exposure is programmed ONLY from the Camera tab: Camera-tab 'Cam settings' cluster (Exposure (ms)) -> [18] 'Cam settings': Value Change -> 'Camera Chgd Settings' -> Send to ENGINE {'Set Camera','Update Cam'} -> Set Camera -> 'Camera Set' (HHMI - Set camera: Camera User Settings ref + Fractional Flyback Time from the Waveform global, nothing else from the Waveform cluster) -> HHMI - User settings to Camera settings (Exp(s) = Exposure (ms)/1000, Cycle(s) = Cycle time (ms)/1000) -> camera wrapper 'Setup' -> HHMI - DCam Module -> DCAM - Setup -> DCAM - Set parameters -> DCAM - Set Exposure (controls: handle, Sensor Mode, Exp(s), Readout width, ROI). None of these references HHMI - SPIM Waveform cluster.ctl. DCAM - Set Exposure in external-trigger Normal Scan does SET EXPOSURE then GET EXPOSURE and returns the readback; the electronic-shutter / cycle logic runs only in free run.

*Evidence:* SPIM MAIN / SPIM MAINd124.png ([18] Cam settings), SPIM MAINd340.png (Camera Set inputs); HHMI - Set camera / HHMI - Set camerad.png (Camera GUI->Camera settings -> All Cam INI Settings -> For loop 'Camera wrapper' method 'Setup'; this synthesis 5x crop (640,195)-(900,330)); HHMI - User settings to Camera settings / ...Settingsd.png; DCAM - Setup / DCAM - Setupd.png ('DCam Set Parms', unbundle Exp(s)/Fractional Flyback Time/Sensor Mode/# of Exps Desired); DCAM - Set Exposure / DCAM - Set Exposurep.png, hidden/...d2.png, d3.png; Python string extraction of DCAM - Set parameters.vi / DCAM - Set Exposure.vi / DCAM - Read cycle times.vi (no Waveform typedef reference)

### Q1 -- partial

Whether the Exp(s) that comes back to Set Camera (and hence into Cam exp (s)) is the REQUESTED Camera-tab exposure or the camera's read-back exposure (100.0421 ms) is not established: DCAM - Setup re-bundles only '# of Pixels', 'L ROI', 'Cycle(s)', 'Cycle(Hz)' into the Setup Settings it returns and sends GET EXPOSURE only to an 'exposure time' indicator; DCAM - Set parameters.vi (which receives the cluster and calls Set Exposure) has no render. The Camera tab's 'Actual' cluster is a separate 'Read Actual' readback refreshed by 'Update Cam' every 0.25 s and never copied into the Waveform cluster.

*Evidence:* DCAM - Setup / DCAM - Setupd.png (this synthesis: 2.5x crops (980,200)-(1560,480) and (500,200)-(1000,480): Bundle-by-Name {# of Pixels, L ROI, Cycle(s), Cycle(Hz)}; GET EXPOSURE -> 'exposure time' DBL indicator only); SPIM MAIN / hidden_frames/SPIM MAINd346.png (Update Cam, 'Read Actual' -> 'Actual')

### Q2 -- established

Cycle time (s) is a control element of the Waveform cluster (typeable), silently overwritten (non-signalling Value write) by every Set Camera, and used unchanged by the waveform code: Make Ramp Waveform copies it into the timing cluster's 'Cycle (sec)' with no min/max/rounding; Generate/Coerce SPIM Waveform never touch it; HHMI - Get Timeout or Cycle Time also uses GLOBAL Waveform Config.Cycle time (s) + Extra as the image-wait timeout; SPIM MAIN shows it x1000 as 'System Cycle Time (ms)' (fed to the time-series / progress estimates).

*Evidence:* SPIM MAIN / SPIM MAINd340.png (Cycle time (s) > Value); HHMI - SPIM Make Ramp Waveform / ...Waveformd.png (Cycle time (s) -> 'Cycle (sec)'); HHMI - Coerce SPIM Waveform / ...Waveformd.png (+hidden d1, d2: only Spiezo.Pixels, Updates/Pixel=1, Z.Pixels, SI sweep duty rebundled); Get Timeout or Cycle Time / ...Timed.png; SPIM MAIN / SPIM MAINd2.png (Unbundle Cycle time (s) -> x1000 -> 'System Cycle Time (ms)'), d32.png, d79.png

### Q2 -- established

With Custom Cycle Time OFF, Cycle time (s) := max(Camera settings.Exp(s), Camera settings.Cycle(s) - 500 ns) (HHMI - Camera times to waveform times, "Normal Scan","Split View" case which always runs; comment: 'Make sure there is some gap between the end of the waveform and the next trigger. otherwise X galvo waveform and/or the AOTF waveform will miss the trigger.'); False case of the Custom Cycle Time structure passes that value straight through. There is no exposure x 1.27, no exposure + flyback, no AO-point rounding and no upper limit.

*Evidence:* HHMI - Camera times to waveform times / ...timesd.png (500n constant -> subtract; first Max&Min top output only; this synthesis 5x crop (680,280)-(870,400): case tunnel 1 = max output, tunnel 2 = Current Waveform Cycle time) and hidden/...timesd2.png (False: pass-through)

### Q2 -- established

The camera 'Cycle(s)' in that formula is the DCAM read-back frame period, not the Camera tab's Cycle time (ms): DCAM - Setup calls the 'Cycle time' sub-VI (DCAM - Read cycle times, per the VI-name string in its binary) after Set Parms and bundles two of its outputs into 'Cycle(s)' and 'Cycle(Hz)' of the returned Setup Settings (identical wiring in the "Normal Scan",Default and "Light Sheet" cases), overwriting the Cycle time (ms)/1000 that HHMI - User settings to Camera settings had put there. DCAM - Read cycle times (Orca4.0, external trigger): SYNCREADOUT -> min(10 s, max(GET EXPOSURE readback, (vsize/2 + 18) x 1H)); EDGE -> readback + (vsize/2 + 10) x 1H; free run -> INTERNAL FRAME INTERVAL; 1H = 9.74436 us. For the user's 100 ms exposure this makes the Custom-OFF Cycle time ~= the actual exposure (0.100042 s) -- the same number the Camera tab's 'Actual' shows. Which of the two sub-VI outputs is Cycle (s) vs Cycle (Hz) rests on the connector pane (not readable).

*Evidence:* DCAM - Setup / DCAM - Setupd.png (this synthesis: 5x crop (990,240)-(1090,500) and 9x crop (1000,425)-(1120,475): 'Cycle time' sub-VI top-terminal output -> case tunnel at y=479, right-side outputs -> tunnels at y=446/454; Exp(s) -> unused tunnel y=407, Fractional Flyback Time -> unused tunnel y=429) and hidden/DCAM - Setupd10.png ("Normal Scan", Default: left 454 -> right 454, left 479 -> right 446; 6x); DCAM - Read cycle times / ...timesd.png + hidden d1-d4; Orca4 / Orca4.0 - Calculate SyncReadout Exposure Time / ...d.png, Orca4.0 - Calculate EdgeTrigger Exposure Time / ...d.png; HHMI - User settings to Camera settings / ...Settingsd.png

### Q2 -- established

With Custom Cycle Time ON, Cycle time (s) := max( max(Exp(s), Cycle(s) - 500 ns), typed Cycle time ) -- the typed value is used as the waveform cycle if it is at or above the camera-derived floor and is raised to the floor otherwise; it is never lowered, never rounded to AO points, never tick-quantised and has no maximum. The write happens only when the result differs from the current Cycle time (or Force Recalc), so a typed 0.127 s with a 100 ms exposure is simply left alone.

*Evidence:* HHMI - Camera times to waveform times / ...timesd.png (True case: second Max&Min of tunnel 1 and 'Current Waveform Cycle time (s)', top output -> 'Cycle time (s)'); SPIM MAIN / SPIM MAINd340.png ('Wvfrm need to be recalc'd?' gate)

### Q2 -- partial

The 0.2 s -> 200.084 ms coercion is not applied to Cycle time (s) at all; it is the Scan Setup 'Time Per Trigger (ms)' = Points per exposure / AO Rate (kHz), where AO Rate = max(ON points, Min # of points per trigger) / Cycle time (both rules divide by Cycle, see Q1) capped at 1000 kHz, and Points per exposure is the actual fast-axis array length (after 'Extend waveform to include the time slow axis needs to return', DIV 2 if bidirectional). Hence Time Per Trigger = Cycle time x (Points per exposure / min points) >= Cycle time -- an integer-point-count excess (1.00042 = about one extra point in ~2400), which is the 'rounded up to whole AO points' the user observed. The exact point counts that give 84 us are not recoverable from the renders; note the numerical coincidence 200.084/200 = 100.0421/100 (actual/requested exposure) that one reader used to argue for a camera-derived origin -- with both rules proportional to 1/Cycle the exposure cannot enter, so that reading is rejected.

*Evidence:* HHMI - SPIM Time per exp / ...expd.png (Points per exposure / AO Rate (kHz)); HHMI - SPIM Calc Single Fast Line Ramp / ...Rampd.png ('Time, counts per exp' after 'Extend waveform...'); HHMI - SPIM compute Min Rate needed for Flyback / ...Flybackd.png (Option A = MinPts / Cycle); HHMI - Min AO rate needed / ...neededd.png (Cycle -> '# that Ramp needs', this synthesis 6x crop); HHMI - Compute AO rate from Cycle Time / ...Timed.png (Max&Min -> /1000 -> Check AO Max Rate RAMP); Waveform/HHMI - Check AO rate for ramp wave / ...waved.png (formula node, 1000 kHz cap only); HHMI - SPIM Generate 1 Line Ramp / ...Rampd.png (Time Per Trigger (ms) bundle)

### Q3 -- established

Exactly one case structure in the whole exported tree tests 'Custom Cycle Time': the True/False case inside HHMI - Camera times to waveform times (see Q2). It is never unbundled, compared or used as a selector in HHMI - Generate/Coerce/Recalc SPIM Waveform, the Linear ramp VIs, Make Ramp Waveform, Time per exp, Min AO rate needed, the four FPGA settings VIs, Calculate DMA Settings, Camera trigger settings, DCAM - Setup / Set Exposure / Read cycle times, Change waveform and update camera, or any of SPIM MAIN's 392 frames (all 124 event cases [0]-[123] are accounted for; [79] is the visible 'Generate New Sim Images' case). Consequently the FPGA trigger period is max(Waveform.Cycle time (s), camera Cycle(s)) whether or not the box is ticked ('use max(cam,custom wvfrm cycle time)').

*Evidence:* HHMI - Camera times to waveform times / ...timesd.png ('Custom Cycle Time' TF -> '?' selector); all frames listed by the eight readers; grep -il 'custom cycle' over all *d*.png.txt OCR sidecars (hits only in *p.png.txt); SPIM MAIN / SPIM MAINd82.png ([79]); HHMI - Generate trigger settings for FPGA / ...FPGAd.png (Max&Min comment)

### Q3 -- partial

GUI handling of an edit to any of the three: there is NO per-element event case (the numbering runs [104] 'Waveform' -> [105] 'Waveform.Fractional Flyback Time'); [104] 'Waveform': Value Change only pushes 'Calculate Waveforms' (skipped on First Call). 'Calculate Waveforms' runs SCAN Make Wavefrm (HHMI - Generate SPIM Waveform) with the whole cluster, writes its output cluster back over the control (the three fields pass through Coerce/Generate unchanged), writes GLOBAL 'Waveform Config'/'Waveform Params', then pushes 'Check bounds', which calls Make Wavefrm again and sends ENGINE ['Enable-disable','Set Camera'] only if (a boolean output of Make Wavefrm -- a second boolean, on the icon's top terminal, distinct from the one driving the 'Out of bounds?' case; by elimination 'recalc?') OR (imgs/stack changed) AND not scanning. So: typing Cycle time does NOT tick Custom Cycle Time; ticking/unticking Custom does NOT change Cycle time by itself; typing Cam exp does NOT change the camera exposure. Each edit just rebuilds the waveform; the engine re-imposes camera-derived values at the next Set Camera (startup, Reset HW, any Camera-tab 'Cam settings' edit, ROI, Dual View, Alignment Mode, Configure Stack, and Check bounds when the condition holds). Whether Check bounds' second Make Wavefrm call reports recalc? = True right after such an edit (i.e. whether the revert is immediate) is not established.

*Evidence:* SPIM MAIN / hidden_frames/SPIM MAINd235.png, d236.png ([104]), d237.png ([105]); SPIM MAIN / SPIM MAINd2.png (Calculate Waveforms); SPIM MAIN / hidden_frames/SPIM MAINd4.png (this synthesis: 8x crop (30,235)-(135,335): green dotted wire from Make Wavefrm's TOP edge -> OR gate; bottom-edge boolean -> 'Out of bounds?' selector; 3x crop (80,225)-(720,345): OR with imgs/stack != feedback, AND NOT Scan Mode & Status, True case {Enable-..., Set Camera} -> Send to ENGINE); HHMI - Generate SPIM Waveform / ...Waveformd.png (outputs 'Out of bounds?' and 'recalc?'); SPIM MAIN / SPIM MAINd309.png, d310.png, d124.png, d193.png, d138.png, d112.png, d23.png (Set Camera triggers)

### Q3 -- established

Effect of an exposure change (Camera tab): [18] 'Cam settings' -> Camera Chgd Settings (HHMI - Coerce Camera Settings Exposure or Cycle Time couples Exposure (ms) and Cycle time (ms) of the Camera-tab cluster: whichever changed bounds the other, Andor-Max-FPS based, Inf upper bound) -> Set Camera. Custom OFF: Cycle time (s) := max(new Exp, new Cycle(s) - 500 ns) -- it simply tracks the camera. Custom ON: Cycle time (s) unchanged unless the new floor exceeds the typed value, in which case it is raised to the floor. In both modes Cam exp (s) := Exp(s) with Val(Sgnl), which queues 'Calculate Waveforms'. A typed Cam exp survives only until the next Set Camera; it never reaches the camera.

*Evidence:* SPIM MAIN / SPIM MAINd124.png; HHMI - Coerce Camera Settings Exposure or Cycle Time / ...Timed.png + hidden d1-d4; HHMI - Camera times to waveform times / ...timesd.png + hidden d2; SPIM MAIN / SPIM MAINd340.png

### Q4 -- established

'# of Integrations' has no coupling to Cycle time / Cam exp: the standalone '# of integrations' control re-signals '# of Integrations' (the cluster element) so the whole-cluster event fires; the value multiplies images per stack ('# imgs Config': '%d images x %d integrations = %d exposures per stack'; HHMI - SPIM Generate Slow axis array for ramp -> 'Slow Ax steps'); it is not unbundled in any timing VI. 'Z motion' likewise: its Value Change re-signals and sends 'Enable-disable'; it is read only to decide 'Make steps?' (== 'Z galvo & piezo') and by HHMI - SPIM Enable disable depending on aquisition state (S motion / Z motion enable, Link disabled, etc.) -- never in a Cycle time / Cam exp computation.

*Evidence:* SPIM MAIN / SPIM MAINd85.png ([2] '# of integrations' -> '# of Integrations'.Val(Sgnl)); SPIM MAIN / hidden_frames/SPIM MAINd4.png (Format string); HHMI - SPIM Generate Slow axis array for ramp / hidden/...rampd1.png; SPIM MAIN / hidden_frames/SPIM MAINd247.png ([112] Z motion); GUI/HHMI - SPIM Enable disable depending on aquisition state / ...stated.png; grep 'z motion' over all *d*.png.txt

### Q4 -- established

'1 exp per' is set by the acquisition mode (plain Value write, no event): 'Point' for Virtual Confocal, XZ PSF and Calibrate XZ & Vslit, 'Z plane' for every other mode; the Acq-mode handler's frame 2 also rebundles it into the Waveform global. Its only timing effect is in HHMI - Generate settings for AO and AI FPGA: 'Point' -> AO clock = 1 / Cam exp (s) (one AO point per exposure, 'PSF mode'); 'Z plane' -> DMA AO rate. It never alters Cycle time.

*Evidence:* SPIM MAIN / SPIM MAINd89.png, d90-d98.png, d99-d107.png, d109/d110.png; HHMI - Generate settings for AO and AI FPGA / ...FPGAd.png + hidden/...FPGAd1.png

### Q4 -- established

'Wait for Zsettle?' (set by the 'Z settle?' ring [119] via Val(Sgnl), by 'Sweep Z?' [93] via Value, and by 'Set Axis Parameters') never changes Cycle time or Cam exp; it changes what must fit inside the cycle: Make Ramp Waveform sets 'msec to settle Z' = 0 ('No settle') or 'Z DELAY'(Zpiezo.Pixel Size) ('Wait'/'Skip imgs'); compute Min Rate needed for Flyback then uses Option B rate = (MinPts - return pts)/(Cycle - Zsettle) when X flyback < Z settle ('Wait': faster AO rate + add'l AO counts) or requests ceil((Zfb - Xfb)/Cycle) extra images ('Skip imgs', written to SCAN '# imgs to skip'). Enable-disable frame 3 hides 'Z settle?' when Sweep Z? is on.

*Evidence:* SPIM MAIN / hidden_frames/SPIM MAINd285-d288.png, d222.png, SPIM MAINd57.png, d335.png; HHMI - SPIM Make Ramp Waveform / ...Waveformd.png + hidden d1; HHMI - SPIM compute Min Rate needed for Flyback / ...Flybackd.png + hidden d1, d2; HHMI - SPIM Calc Single Fast Line Ramp / ...Rampd.png

### Q5 -- partial

None of the three is ever greyed individually (no property node on them anywhere). They are greyed together with their parent: the engine's 'Enable-disable' state, frame 5, writes the enum from 'Scan state enable/disable' (= HHMI - SPIM Enable disable depending on scan state) to the Disabled property of the whole 'Waveform' cluster, the 'Cam settings' and 'Actual' clusters, Acq mode and ~20 other controls, and disables the Scan tab page; 'Exit' re-enables the Waveform cluster. That VI reads 'Scan Mode & Status': its Default case outputs 'Disabled and Grayed Out' and an 'Enabled' constant feeds the single non-default case (not exported; by elimination the 'No scan/Idle' state), so the three fields are editable only while idle and greyed during any scan/acquisition. Frame 2 only toggles the visibility of Pixel/ms, Fractional Flyback, Fract. Smoothing vs Galvo Freq, Sine Overshoot, Slow axis Return for Linear/Sinusoidal.

*Evidence:* SPIM MAIN / hidden_frames/SPIM MAINd337.png (frame 5), d388.png (Exit), d334.png (frame 2), d332.png (frame 1); GUI/HHMI - SPIM Enable disable depending on scan state / ...stated.png (Scan Mode & Status Read -> case, Default -> 'Disabled and Grayed Out' -> 'Disabled if scan in progress?', == -> 'Disabled? (Acquiring?)'; 'Enabled' constant into the case; no hidden folder); all other VI frames read by the readers (no Disabled writes)

### Q6 -- established

FPGA cycle: HHMI - Generate trigger settings for FPGA unbundles Waveform In.'Cycle time (s)' and Camera Read Settings.'Cycle(s)', takes their MAX ('use max(cam,custom wvfrm cycle time)'), passes it unchanged through the "Orca2.8","Orca4.0" case and converts with 'S TCK' (Convert seconds to ticks = round(s x 40 MHz), 25 ns/tick): Cycle (ticks) = round(max(Cycle time, camera Cycle) x 40e6), e.g. exactly 8,000,000 for 0.2 s. Exp (ticks) = 0 for the Orca; Trigger up (ticks) = 4000 (0.0001 s panel default; the FPGA also hard-codes a 100 us camera pulse); Shutter ticks on = ticks(max cycle x # of Exps Desired). On the FPGA 'Internal Cycle Sync Trigger' counts Cycle(Ticks) and 'Trigger Manager' issues the camera trigger and AO start every cycle. Because Cycle time is never below Cycle(s) - 500 ns, with Custom OFF the trigger period equals the camera cycle and the AO waveform cycle is 500 ns shorter; with Custom ON the trigger period is the typed value.

*Evidence:* HHMI - Generate trigger settings for FPGA / ...FPGAd.png (6x zooms of the Max&Min, Orca case, bundle), hidden d1-d8; Convert seconds to ticks / ...ticksd.png; HHMI - FPGA Clock Frequency / ...Frequencyd.png (40M); FPGA VIs/Triggering/HHMI - Generate External Camera Trigger / ...d.png (4000); HHMI - Internal Cycle Sync Trigger / zoom_ics_3x.png; HHMI - Trigger Manager / zoom_tm_2x.png

### Q6 -- established

Time Per Trigger (ms) is NOT consumed by the FPGA path. The AO side takes Waveform Parameters.'AO Rate (kHz)' -> Calculate DMA Settings: AO ticks between points = round(40e6 / (AO Rate x 1000)) ('Round timing to nearest tick'), AO # Points per trigger = '# of Pulses per Trigger'; 'Time Per Trigger (ms)', 'AO per Trigger', 'Time Per WvFrm' are unread by Configure FPGA for waveform / Generate settings for FPGA / Generate settings for AO and AI FPGA / Generate trigger settings. Time Per Trigger itself is filled in HHMI - SPIM Generate 1 Line Ramp from 'CALC 1 FAST RAMPS' (HHMI - SPIM Calc Single Fast and AOTF Line Ramps -> Calc Single Fast Line Ramp -> Time per exp) as Points per exposure / AO Rate (kHz), with AO Rate = max(N_on, MinPts)/Cycle time (see Q2), so it is the AO waveform's real duration, slightly above Cycle time by an integer-point ratio.

*Evidence:* Low Level/HHMI - Calculate DMA Settings / ...Settingsd.png; HHMI - Configure FPGA for waveform / ...waveformd.png; HHMI - Generate settings for FPGA / ...FPGAd.png (Waveform Parameters unwired); HHMI - SPIM Generate 1 Line Ramp / ...Rampd.png; HHMI - SPIM Calc Single Fast and AOTF Line Ramps / ...Rampsd.png; HHMI - SPIM Time per exp / ...expd.png

### other -- established

Conflicts resolved by opening frames: (1) Camera times to waveform times selector = enum constant (reader B), not the camera's Sensor Mode (reader A). (2) Min AO rate needed: 'Exposure (sec)' unwired, Cycle feeds '# that Ramp needs' (the FPGA reader), not Exposure (the two Linear readers). (3) The 200.084 ms: neither 'AO-rate coerced so an integer number of points spans the cycle' (reader A) nor 'scaled by the camera's actual/requested exposure ratio' (reader B) is supported; it is Points per exposure / (max(N_on, MinPts)/Cycle) with both counts integers. (4) DCAM - Setup does write the read-back Cycle(s)/Cycle(Hz) into the settings cluster (closing that gap), but leaves Exp(s) to DCAM - Set parameters (still a gap). (5) 'Scan state enable/disable' IS rendered under GUI/ as HHMI - SPIM Enable disable depending on scan state (the readers searched for the icon caption).

*Evidence:* This synthesis' crops listed in the Q1-Q5 entries; SPIM MAIN.vi binary string 'HHMI - SPIM Enable disable depending on scan state.vi'

## The synthesizer's proposed Python change

Recorded verbatim, NOT applied.

```
Repo: H:\UNM_Lightsheet\UNMScope_Python (read-only here; the edits below are what to apply).

Behaviour to port (from the spec):
  * Cam exp (s): typeable control, but typing it only rebuilds the waveform; it never sets the camera. The engine overwrites it with the camera's Exp(s) (signalling -> waveform recalc) whenever the camera is (re)configured. It does not affect the AO rate (Min AO rate needed leaves 'Exposure (sec)' unwired).
  * Cycle time (s): typeable control; typing it does NOT tick Custom. Engine write (non-signalling) on every camera (re)configuration: floor = max(Exp(s), camera_cycle - 500 ns); Custom OFF -> floor; Custom ON -> max(floor, typed). No 1.27 factor, no rounding, no maximum.
  * camera_cycle = DCAM - Read cycle times: SYNCREADOUT min(10 s, max(exposure, (vsize/2+18)*1H)); EDGE exposure + (vsize/2+10)*1H; 1H = 9.74436 us. No safety margin in LouisXIV.
  * FPGA trigger period = max(Cycle time, camera_cycle) -> round(x 40e6) ticks. The AO waveform is built for Cycle time (already 500 ns short of the trigger period).
  * Greying: all three follow the whole cluster (idle: enabled; scanning: disabled+greyed).

--- a/src/unmscope/config/waveform_config.py
+++ b/src/unmscope/config/waveform_config.py
@@
-#: Flyback allowance used when Cycle time is not set by hand: the fraction of
-#: the exposure the X galvo needs to get home before the next cycle. 0.27 is
-#: the user's own working value on this rig (100 ms exposure -> 127 ms cycle),
-#: within the 10-30% they measured empirically.
-DEFAULT_FLYBACK_FRACTION = 0.27
+#: HHMI - Camera times to waveform times.vi: the gap LouisXIV leaves between the
+#: end of the AO waveform and the next camera trigger ("otherwise X galvo
+#: waveform and/or the AOTF waveform will miss the trigger").
+CYCLE_GAP_S = 500e-9
+#: Orca4.0 - Calculate SyncReadout/EdgeTrigger Exposure Time.vi: one line time (1H).
+ORCA_LINE_TIME_1H_S = 9.74436e-6
+SYNCREADOUT_EXTRA_LINES = 18   # DCAM - Read cycle times -> Orca4.0 Calc SyncRdt Exp
+EDGE_EXTRA_LINES = 10          # DCAM - Read cycle times -> Orca4.0 Calc EdgeTrig ExpTime
+MAX_CAMERA_CYCLE_S = 10.0      # 'max exp' upper bound in the SyncReadout VI
+
+
+def camera_cycle_s(exposure_s: float, vsize: int, sync_readout: bool,
+                   line_time_s: float = ORCA_LINE_TIME_1H_S) -> float:
+    """``DCAM - Read cycle times.vi`` (external trigger, Normal Scan): the camera's
+    own frame period for the programmed exposure. No margin -- LouisXIV has none."""
+    half = vsize // 2
+    if sync_readout:
+        return min(MAX_CAMERA_CYCLE_S, max(exposure_s, (half + SYNCREADOUT_EXTRA_LINES) * line_time_s))
+    return exposure_s + (half + EDGE_EXTRA_LINES) * line_time_s
+
+
+def engine_times(exposure_s: float, camera_cycle: float, typed_cycle_s: float,
+                 custom_cycle_time: bool) -> tuple[float, float]:
+    """``HHMI - Camera times to waveform times.vi`` ("Normal Scan","Split View" case --
+    the selector is a constant, the Light Sheet branch is dead code), as called by
+    SPIM MAIN's 'Set Camera' state: returns (Cam exp (s), Cycle time (s))."""
+    floor = max(exposure_s, camera_cycle - CYCLE_GAP_S)
+    cycle = max(floor, typed_cycle_s) if custom_cycle_time else floor
+    return exposure_s, cycle
@@ class WaveformConfig:
-    cam_exp_s: float = 2.0                  # indicator: the camera exposure
-    #: The trigger-to-trigger period. This is the number that actually times
-    #: the acquisition, NOT the exposure: the X galvo has to fly back to its
-    #: resting position before the next cycle can start, and that mechanical
-    #: return takes real time. On this rig, MEASURED by the user in LouisXIV,
-    #: 27 ms on a 100 ms exposure works; 10-30% is the usual band.
-    #: Read-only unless ``custom_cycle_time``, exactly as LouisXIV has it.
-    cycle_time_s: float = 0.0
+    #: Control element of the cluster (HHMI - SPIM Waveform cluster.ctl). LouisXIV
+    #: overwrites it with the camera's Exp(s) on every 'Set Camera' (SPIM MAIN d340,
+    #: Val(Sgnl)); typing it never reaches the camera. Only used for the PSF-mode
+    #: AO clock (1 exp per = Point) and the X Wvfrm cursor.
+    cam_exp_s: float = 2.0
+    #: Control element; the AO waveform cycle. Overwritten (plain Value) on every
+    #: 'Set Camera' with engine_times(): max(Exp, camera cycle - 500 ns), or
+    #: max(that, the typed value) when custom_cycle_time. The FPGA trigger period
+    #: is max(cycle_time_s, camera cycle) (HHMI - Generate trigger settings for FPGA).
+    cycle_time_s: float = 0.0
@@
-    #: Tick to type your own Cycle time instead of taking the computed one.
+    #: Tested in exactly one place in LouisXIV (Camera times to waveform times):
+    #: keeps a typed Cycle time that is >= the camera-derived floor.
     custom_cycle_time: bool = False

--- a/src/unmscope/gui/waveform_config_panel.py
+++ b/src/unmscope/gui/waveform_config_panel.py
@@ module docstring
-Live (...) and -- gated by
-the Custom Cycle Time tick -- Cycle time, which is the trigger period itself
-(see main_window's acquire path). Indicators (read-only, refreshed by the
-window): Pixel/ms, Cam exp, Cycle time while Custom is off, the axis
-sub-clusters.
+Cam exp (s), Cycle time (s) and Custom Cycle Time are plain control elements of
+the cluster: always typeable while the cluster is enabled, no per-element event
+in LouisXIV ([104] "Waveform": Value Change just queues Calculate Waveforms), and
+re-imposed by the engine's Set Camera through set_engine_times() -- Cam exp from
+the camera exposure (signalling: emits `changed`), Cycle time from
+waveform_config.engine_times() (silent). Typing Cycle time does NOT tick Custom;
+typing Cam exp does NOT change the camera. Indicators (read-only): Pixel/ms, axes.
@@ class WaveformConfigPanel(QWidget):
     changed = Signal(object)          # WaveformConfig, after any live edit
-    exposure_edited = Signal(float)   # seconds: Cam exp typed on THIS page (LouisXIV lets you)
@@ __init__
-        # Cam exp is TYPEABLE here, in seconds, as on LouisXIV's page -- it is
-        # where the user set 0.1 -- and drives the Scan Setup exposure through
-        # `exposure_edited`. The window pushes the exposure back into it under
-        # _updating, so the two never chase each other.
+        # Cam exp: typeable, but LouisXIV never sends it to the camera; the
+        # engine overwrites it from the camera exposure (set_engine_times).
         self.cam_exp_s = self._dspin(_r(82, 318, 63, 18), 0.0001, 60.0, 4)
         self.cam_exp_s.setEnabled(True)
-        self.cam_exp_s.valueChanged.connect(self._on_cam_exp_edited)
+        self.cam_exp_s.valueChanged.connect(self._on_edit)
-        # Cycle time is the trigger period and is ALWAYS typeable. Typing a
-        # value ticks Custom Cycle Time for you -- the tick sits at the bottom
-        # of the page, nowhere near this field, and the user should not have
-        # to find it first. Untick Custom to go back to the computed value.
+        # Cycle time: typeable; a typed value only survives the next Set Camera
+        # if Custom Cycle Time is ticked AND it is >= the camera-derived floor.
         self.cycle_time_s = self._dspin(_r(82, 337, 63, 18), 0.0, 60.0, 4)
         self.cycle_time_s.setEnabled(True)
-        self.cycle_time_s.valueChanged.connect(self._on_cycle_time_edited)
+        self.cycle_time_s.valueChanged.connect(self._on_edit)
@@ def config(self)
         return replace(self._cfg,
                        ...
+                       cam_exp_s=self.cam_exp_s.value(),
                        custom_cycle_time=self.custom_cycle_time.isChecked(),
                        cycle_time_s=self.cycle_time_s.value())
@@
-    def set_indicators(self, *, cam_exp_s: float | None = None, cycle_time_s: float | None = None,
-                       pixel_per_ms: float | None = None, axes: dict[str, AxisSettings] | None = None) -> None:
-        """Refresh the read-only fields from the window (exposure, trigger
-        period, AO rate / Updates per Pixel, and the Scan Setup axes)."""
-        if cam_exp_s is not None:
-            self._cfg = replace(self._cfg, cam_exp_s=cam_exp_s)
-            self._updating = True
-            try:
-                self.cam_exp_s.setValue(cam_exp_s)
-            finally:
-                self._updating = False
-        if cycle_time_s is not None and not self.custom_cycle_time.isChecked():
-            # Never overwrite a period the user typed; this is the computed
-            # one, and it is only an indicator while Custom is off.
-            self._cfg = replace(self._cfg, cycle_time_s=cycle_time_s)
-            self._updating = True
-            try:
-                self.cycle_time_s.setValue(cycle_time_s)
-            finally:
-                self._updating = False
+    def set_engine_times(self, cam_exp_s: float, cycle_time_s: float) -> None:
+        """SPIM MAIN 'Set Camera' (d340): 'Cam exp (s)'.Val(Sgnl) and
+        'Cycle time (s)'.Value from Camera Recalc Wvfrm?. Written in BOTH Custom
+        modes (the caller already applied engine_times()); Cam exp signals a
+        recalc when it actually changes, Cycle time is silent."""
+        signal = abs(cam_exp_s - self.cam_exp_s.value()) > 1e-12
+        self._cfg = replace(self._cfg, cam_exp_s=cam_exp_s, cycle_time_s=cycle_time_s)
+        self._updating = True
+        try:
+            self.cam_exp_s.setValue(cam_exp_s)
+            self.cycle_time_s.setValue(cycle_time_s)
+        finally:
+            self._updating = False
+        if signal:
+            self.changed.emit(self.config())
+
+    def set_indicators(self, *, pixel_per_ms: float | None = None,
+                       axes: dict[str, AxisSettings] | None = None) -> None:
+        """Refresh the read-only fields (Pixel/ms, the Scan Setup axes)."""
@@
-    def _on_cam_exp_edited(self, value: float) -> None:
-        if self._updating:
-            return
-        self.exposure_edited.emit(float(value))
-        self._on_edit()
-
-    def _on_cycle_time_edited(self, value: float) -> None:
-        if self._updating:
-            return
-        if not self.custom_cycle_time.isChecked():
-            self.custom_cycle_time.blockSignals(True)
-            self.custom_cycle_time.setChecked(True)
-            self.custom_cycle_time.blockSignals(False)
-        self._on_edit()
+    # (Cam exp / Cycle time edits go straight to _on_edit: LouisXIV's [104]
+    # "Waveform": Value Change -> Calculate Waveforms, nothing else.)

--- a/src/unmscope/hardware/camera.py
+++ b/src/unmscope/hardware/camera.py
@@ class Camera
+    def cycle_time_s(self, exposure_ms: float) -> float:
+        """``DCAM - Read cycle times.vi``: the camera's own frame period for
+        this exposure in the current trigger mode (no safety margin -- this is
+        LouisXIV's number; the port's measured margin stays in trigger_period_ms)."""
+        from unmscope.config.waveform_config import camera_cycle_s
+        h = self.info.height if self.info is not None else 2048
+        return camera_cycle_s(exposure_ms / 1000.0, h,
+                              self.trigger_active == self.TRIGGER_SYNCREADOUT,
+                              line_time_s=self.line_time_ms() / 1000.0)
     (LouisXIV feeds GET EXPOSURE's read-back into this formula; when a camera is connected pass
      self.get_exposure_ms() instead of the requested value if you want 100.0421 rather than 100.)

--- a/src/unmscope/gui/main_window.py
+++ b/src/unmscope/gui/main_window.py
@@ imports
-from unmscope.config.waveform_config import ..., DEFAULT_FLYBACK_FRACTION, ...
+from unmscope.config.waveform_config import ..., engine_times, ...
@@ __init__ (tabs)
-        # Cam exp typed on the Low-Level Waveform Config page (seconds) is the
-        # camera exposure, as in LouisXIV; the Scan Setup spin is in ms.
-        self.utilities_tab.waveform_panel.exposure_edited.connect(
-            lambda seconds: self.exposure_spin.setValue(seconds * 1000.0))
+        # LouisXIV: Cam exp (s) never drives the camera; the camera drives it
+        # (see _push_engine_times).
@@
+    def _push_engine_times(self) -> tuple[float, float]:
+        """LouisXIV 'Set Camera' -> Camera Recalc Wvfrm? -> Camera times to
+        waveform times: rewrite Cam exp (s) and Cycle time (s) on the cluster
+        page from the camera. Call wherever LouisXIV sends 'Set Camera': camera
+        connect, every Camera-tab change (exposure, ROI, binning), stack
+        configuration, and the Acquire set-up."""
+        panel = self.utilities_tab.waveform_panel
+        cfg = panel.config()
+        exposure_ms = float(self.exposure_spin.value())
+        cam = self.camera
+        cam_cycle = (cam.cycle_time_s(exposure_ms) if cam is not None and cam.is_connected
+                     else exposure_ms / 1000.0)
+        cam_exp_s, cycle_s = engine_times(exposure_ms / 1000.0, cam_cycle,
+                                          cfg.cycle_time_s, cfg.custom_cycle_time)
+        panel.set_engine_times(cam_exp_s, cycle_s)
+        return cam_exp_s, cycle_s
@@ def on_exposure_changed(self, value: float):
-        # Cam exp on the Low-Level Waveform Config page is the same number in
-        # seconds, so it follows the spin whether or not a camera is connected
-        # (the panel takes it under its own guard, so this cannot loop).
-        self.utilities_tab.waveform_panel.set_indicators(cam_exp_s=value / 1000.0)
         if self._blocking_op is not None:
             return
         ...
         try:
             self.camera.set_exposure_ms(value)
         except Exception as e:
             self._log(...)
         self.camera_tab.refresh_actuals()
+        self._push_engine_times()          # [18] Cam settings -> Set Camera
@@ acquire set-up (was lines ~1970-1998)
-        exposure_ms = self.exposure_spin.value()
-        wcfg_period = self.utilities_tab.waveform_panel.config()
-        if wcfg_period.custom_cycle_time and wcfg_period.cycle_time_s > 0:
-            cycle_ms = wcfg_period.cycle_time_s * 1000.0
-        else:
-            cycle_ms = exposure_ms * (1.0 + DEFAULT_FLYBACK_FRACTION)
-        camera_floor_ms = self.camera.trigger_period_ms(exposure_ms)
-        period_ms = max(cycle_ms, camera_floor_ms)
-        if period_ms > cycle_ms + 1e-9:
-            self._log(f"Cycle time {cycle_ms:.3f} ms is below the camera's minimum "
-                      f"{camera_floor_ms:.3f} ms; using the minimum.")
-        period_s = period_ms / 1000.0
+        exposure_ms = self.exposure_spin.value()
+        typed = self.utilities_tab.waveform_panel.config()
+        cam_exp_s, cycle_s = self._push_engine_times()          # Set Camera writes
+        if typed.custom_cycle_time and cycle_s > typed.cycle_time_s + 1e-9:
+            self._log(f"Custom Cycle time {typed.cycle_time_s*1e3:.3f} ms is below the camera's "
+                      f"floor; LouisXIV raises it to {cycle_s*1e3:.3f} ms.")
+        cam_cycle_s = self.camera.cycle_time_s(exposure_ms)
+        # HHMI - Generate trigger settings for FPGA: Cycle (ticks) = max(cam, custom wvfrm cycle time)
+        period_s = max(cycle_s, cam_cycle_s)
+        # PORT-ONLY DEVIATION (flag for the user): keep the measured safety margin
+        # (spikes/19; Camera.SAFETY_MARGIN_MS) as an extra floor. LouisXIV has none.
+        floor_s = self.camera.trigger_period_ms(exposure_ms) / 1000.0
+        if floor_s > period_s:
+            self._log(f"trigger period raised from {period_s*1e3:.3f} to {floor_s*1e3:.3f} ms (port safety margin)")
+            period_s = floor_s
+        period_ms = period_s * 1000.0
+        cycle_s_for_waveform = cycle_s                           # the AO waveform is built for Cycle time (s)
@@ build call (was ~line 2060)
-                exposure_s=exposure_s, cycle_s=period_s, ...
+                exposure_s=exposure_s, cycle_s=cycle_s_for_waveform, ...
@@ after the build (was ~line 2085)
-        self.utilities_tab.waveform_panel.set_indicators(
-            cam_exp_s=exposure_s, cycle_time_s=period_s, pixel_per_ms=lx.rate.pixel_per_ms,
-            axes={...})
+        self.utilities_tab.waveform_panel.set_indicators(pixel_per_ms=lx.rate.pixel_per_ms, axes={...})
   Also call self._push_engine_times() after a successful camera connect and from the ROI/binning
   handlers (LouisXIV [73] ROI and Configure Stack both send 'Set Camera').
   Greying: wherever the window disables the Camera tab's settings for a run, disable
   utilities_tab.waveform_panel as a whole (Enable-disable frame 5 greys the 'Waveform',
   'Cam settings' and 'Actual' clusters together); never the three fields individually.

--- a/src/unmscope/hardware/louisxiv_waveform.py
+++ b/src/unmscope/hardware/louisxiv_waveform.py
@@ docstring
-    rate needed for exposure = ON points / exposure
+    rate needed for exposure = ON points / CYCLE TIME   (HHMI - Min AO rate needed leaves the
+                                timing cluster's 'Exposure (sec)' unwired and feeds 'Cycle (sec)'
+                                into '# that Ramp needs'; Cam exp does not enter the AO rate)
@@ def ao_rate_for_line(...)
-    r_exp = min_rate_for_exposure(line.on_points, exposure_s)
+    r_exp = min_rate_for_exposure(line.on_points, cycle_s)     # exposure_s kept for the API, unused as in LouisXIV
@@ def build_louisxiv_waveform(...)
-    rate = ao_rate_for_line(line, exposure_s, max(1e-6, cycle_s - cycle_margin_s), update_rate,
+    # The 500 ns gap is already inside cycle_s (engine_times); do not subtract it again.
+    rate = ao_rate_for_line(line, exposure_s, max(1e-6, cycle_s), update_rate,
   (if cycle_margin_s exists for another documented reason, keep it but default it to 0.)

--- tests
tests/test_utilities_tab.py
  - test_typing_a_cycle_time_ticks_custom_for_you -> test_typing_a_cycle_time_does_not_tick_custom:
      panel.custom_cycle_time.setChecked(False); panel.cycle_time_s.setValue(0.127)
      assert not panel.custom_cycle_time.isChecked() and panel.config().cycle_time_s == approx(0.127)
  - test_cam_exp_typed_on_the_waveform_page_sets_the_exposure -> ..._does_not_touch_the_exposure:
      exposure_spin at 100 ms; panel.cam_exp_s.setValue(0.05); assert exposure_spin == 100;
      w.exposure_spin.setValue(50); assert panel.cam_exp_s.value() == approx(0.05) (camera -> Cam exp)
  - test_the_computed_cycle_time_never_overwrites_a_typed_one -> test_set_engine_times_writes_both_modes:
      Custom ON, typed 0.127, engine_times(0.1, 0.1000421, 0.127, True) == (0.1, 0.127);
      engine_times(0.1, 0.1000421, 0.05, True)[1] == approx(0.1000416); Custom OFF -> approx(0.1000416);
      panel.set_engine_times(0.1, 0.5) overwrites in both modes; `changed` emitted only when Cam exp changed.
tests/test_gui_flow_fake_fpga.py::test_trigger_period_is_the_cycle_time_not_the_exposure
  - Custom OFF: period == max(camera.cycle_time_s(100), port floor), i.e. ~100 ms in SYNCREADOUT, NOT 127;
    panel.cycle_time_s reads max(0.1, cycle-500e-9).  Custom ON 0.127 -> 127; 0.200 -> 200;
    0.050 -> raised to the floor with the 'below the camera's floor' log.
tests/test_waveform_config.py: add camera_cycle_s() cases (SYNCREADOUT 2048 rows, 100 ms -> 0.1;
  1 ms -> (1024+18)*9.74436e-6 = 10.15 ms; EDGE 100 ms -> 100 + 10.07 ms) and engine_times() cases.

Note for the user (not a code change): LouisXIV's Custom-OFF default reproduces exactly the
period the port had measured as too short (waveform cycle = camera cycle - 500 ns, trigger
period = camera cycle ~= exposure in SYNCREADOUT). LouisXIV's user copes by ticking Custom
and typing 0.127 s -- which is what the live panel showed today -- and the .cfg autosave keeps
that. So persist custom_cycle_time=True / cycle_time_s=0.127 in the port's saved WaveformConfig
instead of a 27% factor; do not re-add a derived default.
```

## Explicitly not established

- Q2 exact rounding arithmetic (0.2 s -> 200.084 ms): the 'Check AO Max Rate RAMP' sub-VI called by HHMI - Compute AO rate from Cycle Time (and by HHMI - SPIM Check AO rate) is not in the export tree (no folder named like it under VI_Diagrams), so whether it caps the rate at a maximum and/or quantizes it to FPGA tick multiples is unknown; that VI plus the integer 'Points per exposure' is where the 84 us excess must come from. Look for the VI whose icon reads 'Check AO Max Rate RAMP' in SPIM LV8.6 VIs\DAQ (probably Control Modules/Utils or Constants) and export it.
- Q2 source of the camera 'Cycle(s)' that Camera times to waveform times and Generate trigger settings consume: HHMI - User settings to Camera settings fills Cycle(s) from the Camera-tab 'Cycle time (ms)'/1000, but whether the camera wrapper afterwards overwrites that field with the DCAM readback (DCAM - Read cycle times 'Cycle (s)', or the 'Read Actual' method seen in SPIM MAIN Update Cam) is not visible: HHMI - Camera Module wrapper / HHMI - Camera Module (all) and the DCAM 'Setup'/'Read Actual' method bodies were not read.
- Q1 the final DCAM - Set Exposure.vi call: I traced the Camera-tab Exposure (ms) into the Cam Settings Cluster 'Exp(s)' handed to the wrapper Setup method, but did not open the wrapper/DCAM - Setup diagrams to confirm that DCAM - Set Exposure takes exactly that field (Image Acq/High level/HHMI - Camera Module wrapper, Image Acq/DCAM/Setup/DCAM - Setup, DCAM - Set Exposure).
- Q1 caller of HHMI - Coerce Camera Settings Exposure or Cycle Time (the Exposure(ms)/Cycle time(ms) coupling of the Camera tab, Andor Max FPS based): presumably 'Camera Chgd Settings' in SPIM MAIN [18] Cam settings (d124), not verified; whether it is bypassed for the Orca is unknown.
- Q3 whether a Cam exp edit in the Waveform cluster immediately triggers Set Camera (which would overwrite the typed value): depends on the condition in SPIM MAIN 'Check bounds' (SPIM MAINd4.png, other reader's fact #68) -- not re-read here.
- Q4 the 'Z DELAY' sub-VI (msec to settle Z from Cycle time and Zpiezo.Pixel Size) and the Virtual Confocal True cases of HHMI - SPIM Time per exp (hidden d1/d2) were not read; also HHMI - SPIM Calc Single Frame Ramp Waveform (9 frames; OCR mentions '# of Integrations', 'AOTF cycle', 'Fractional Flyback Time') was not read -- it may multiply images per Z step but has no timing-cluster input per its OCR.
- Q5 body of the VI that decides which scan states disable the whole 'Waveform' cluster ('Scan state enable/disable' in SPIM MAIN Enable-disable frame 5) is not exported anywhere under VI_Diagrams; the per-mode greying of the three fields therefore stays at the whole-cluster level established by the SPIM MAIN readers.
- Q6 HHMI - Generate PB settings for FPGA hidden frames d1/d3 also mention 'Cycle time' (pulse-blast timing) and were not read; the 'Andor sCMOS' trigger case arithmetic and the disabled '(cycle-exp)/2' Trigger-Up alternative were only glanced at.
- Q6 whether the 'DMA settings'.'# of X points/Trigger' fed to 'AO # Points per trigger' equals Waveform Params 'AO per Trigger' (the VI that builds DMA settings, HHMI - Generate settings for FPGA, was not read).
- The identity of the sawtooth 'Ramp' icon in HHMI - Generate SPIM Waveform as HHMI - SPIM Generate Waveform Ramp.vi is matched by terminal pattern and OCR label ('Ramp'), not by a sub-VI list (report.html for these VIs lists only the two images).
- Q2/Q6: WHERE 0.2 s becomes 200.084 ms. Not in any of the eight assigned VIs (all exact divisions, only a 1000 kHz cap). Look in: the engine-side 'Camera Recalc Wvfrm?' sub-VI (SPIM MAINd340 writes Cam exp (s) Val(Sgnl) and Cycle time (s) Value from its outputs), HHMI - Coerce Camera Settings Exposure or Cycle Time.vi, HHMI - Camera times to waveform times.vi, HHMI - Read DCAM Settings SR.vi / DCAM - Read cycle times.vi (camera line-time quantization; note 200.084/200 = 100.0421/100 exactly), and for tick quantization HHMI - Generate settings for AO and AI FPGA.vi / HHMI - Generate trigger settings for FPGA.vi / HHMI - Configure FPGA for waveform.vi.
- Q3: which VI tests 'Custom Cycle Time'. It is never unbundled in the eight VIs and no rendered block-diagram OCR contains the label. Best candidates: HHMI - Coerce SPIM Waveform.vi (runs on every Generate SPIM Waveform call before the Last-Waveform-Config comparison; d.png + 2 hidden frames), 'Camera Recalc Wvfrm?' (SPIM MAINd340), HHMI - Coerce Camera Settings Exposure or Cycle Time.vi, HHMI - SPIM Waveform to GUI clusters.vi (its diagram OCR mentions Cam exp).
- Q1: whether the DCAM exposure is set FROM Cam exp (s) or only from the Camera tab Exposure; no camera VI is called in this layer. Look in Camera Set / DCAM - Setup.vi / HHMI - DCam Module.vi / DCAM - Set Exposure.vi.
- Q2: with Custom Cycle Time OFF, what value Cycle time (s) holds when it reaches Make Ramp Waveform (this layer just consumes the field); the writer is the engine's Set Camera state (SPIM MAINd340) via 'Camera Recalc Wvfrm?', not read here.
- Q4: coupling of '# of Integrations', '1 exp per' and 'Z motion' to Cycle time / Cam exp: none of the three is read in the eight VIs. Candidates: HHMI - SPIM Calculate number of pulses.vi ('SCAN # of pulses'), '# imgs Config' (SPIM MAINd4), 'Read Wvfrm for cntrls' (SPIM MAINd327), the 'SCAN # imgs to skip' write inside HHMI - SPIM Calc Single Fast Line Ramp.vi (AOTF cycle case), HHMI - Number of images to skip and number of data images per Z step.vi.
- Q5: per-field greying of Cam exp / Cycle time / Custom Cycle Time: no property nodes exist in these VIs; only the whole-cluster Disabled write in SPIM MAIN Enable-disable frame 5 (d337) is known, and the mode logic is inside 'Scan state enable/disable'.
- Q6: the exact connector-pane mapping of the two I32 inputs of Compute AO rate from Cycle Time as wired inside HHMI - SPIM Calc Single Fast and AOTF Line Ramp.vi (which of the two blue wires is 'AO points per fast line' vs 'points used for fast axis return'); the render shows two I32s from 'FAST AXIS' (one through the D.V. case) but the terminal order cannot be read from icons.
- Q6: the identity of the 'FAST AXIS' sub-VI inside HHMI - SPIM Calc Single Fast and AOTF Line Ramp.vi and of 'CALC RAMP FRAME' inside Generate 1 Frame Ramp (icons only; report.html files for these renders list no sub-VIs). Likely HHMI - SPIM Generate Fast axis ramp for DOE or not.vi and HHMI - SPIM Calc Single Frame Ramp Waveform.vi respectively.
- Q6: whether 'Updates / Pixel' is 1 in the live configuration; if not, Compute AO rate's use of 'Check AO Max Rate RAMP' (AO = points/ms x Updates/Pixel) multiplies the minimum rate by Updates/Pixel. Check the deployed .cfg / Waveform cluster value.
- Q6: FPGA cycle in ticks from Cycle time / Clock Rate (Hz): not derived in this layer; HHMI - Generate settings for AO and AI FPGA.vi (1 hidden frame, diagram mentions Cam exp), HHMI - Generate trigger settings for FPGA.vi (8 hidden frames, diagram mentions cycle), HHMI - Configure FPGA for waveform.vi (2 hidden frames, panel has Time Per Trigger and Custom Cycle Time).
- Q1: What value DCAM - Set parameters.vi actually wires into Set Exposure's Exp(s) (Camera tab Exposure (ms)/1000 from the 'HHMI - Camera Settings cluster', or something already coerced against Cam exp / Cycle time). No render exists for DCAM - Set parameters.vi; look in DCAM - Setup.vi (11 hidden frames exported) and HHMI - DCam Module.vi (51 frames), and at the 'Camera Set' / 'Camera Recalc Wvfrm?' sub-VIs of SPIM MAINd340 - SPIM MAIN.vi's binary directly references HHMI - Coerce Camera Settings Exposure or Cycle Time.vi, which is the prime candidate for 'Camera Recalc Wvfrm?' and for the Cam exp / Cycle time writes.
- Q1: Identity of the 'Camera wrapper' (method 'Read Actual') and 'DCam Read Enabled Settings' sub-VIs in SPIM MAINd346, and whether the 'Actual' cluster is the same typedef as the settings cluster (Exposure Time (s) / Cycle Time (s)) - the cluster contents are not visible in the frame and no matching name string was isolated in SPIM MAIN.vi.
- Q2: Whether the Cycle time (s) shown on the Adv Setup page with Custom Cycle Time OFF equals DCAM - Read cycle times' Cycle (s) (the camera's own frame period) or something else (exposure + flyback, AO-rounded). Read cycle times' scalar outputs have no consumer inside my VIs except Set Exposure's free-run branch; the bundling into the settings cluster's 'Cycle Time (s)' field is in DCAM - Setup.vi / HHMI - DCam Module.vi, and the waveform-side use is in HHMI - Camera times to waveform times.vi, HHMI - Coerce Camera Settings Exposure or Cycle Time.vi, HHMI - SPIM Time per exp.vi and HHMI - Compute AO rate from Cycle Time.vi (all exported, not in my set).
- Q2: The exact coercion that turns 0.2 s into 200.084 ms (AO-point rounding, minimum against the camera cycle, maximum) - not present in any of my VIs; look in HHMI - SPIM number of points and points per second that Ramp needs.vi / HHMI - Compute AO rate from Cycle Time.vi / HHMI - Generate SPIM Waveform.vi.
- Q3: Which case structures test Custom Cycle Time and what happens to Cycle time when exposure changes with Custom ON vs OFF - no reference in my VIs. Look in HHMI - Coerce Camera Settings Exposure or Cycle Time.vi (4 hidden frames exported; referenced directly by SPIM MAIN.vi), HHMI - Camera times to waveform times.vi (2 frames) and HHMI - Generate SPIM Waveform.vi (2 frames); confirm the field itself in HHMI - SPIM Waveform cluster.ctl's panel render.
- Q4: Any coupling of '# of Integrations', '1 exp per', 'Wait for Zsettle?' or 'Z motion' to Cycle time / Cam exp - none of these names occur in my VIs; HHMI - Calculate number of exposures for FPGA.vi and HHMI - Generate trigger settings for FPGA.vi (both read HHMI - Read Camera Settings SR.vi) are where '# of Exps Desired' is most likely derived from integrations.
- Q5: Which modes grey Cam exp / Cycle time / Custom Cycle Time - nothing in my VIs; per the established facts it is the whole 'Waveform' cluster's Disabled property set by 'Scan state enable/disable' in SPIM MAINd337.
- Q6: How Time Per Trigger and the FPGA cycle ticks are derived from Cycle time - not in my VIs; HHMI - Generate trigger settings for FPGA.vi (8 hidden frames exported) and HHMI - Generate settings for AO and AI FPGA.vi are the place, and the consumer of the global 'Trig-Acq Delay (s)' written by Orca4.0 - Calculate Light Sheet Mode Cycle Time.vi is unknown.
- Whether the settings cluster's field label is 'Cycle(s)' with caption 'Cycle Time (s)' (SPIM MAIN d32/d79 unbundle 'Cycle(s)'; my panels show 'Cycle Time (s)') - the typedef HHMI - Camera Settings cluster.ctl (Image Acq\High level\Type defs) has no render.
- Which VI writes the settings cluster's 'Exposure Time (s)' and 'Cycle Time (s)' fields into the per-camera 'DCam Settings FG n' store (the FG sub-VIs themselves are not in my set and have no renders listed).
- Q2: the exact mechanism that turns a 0.2 s Cycle time into Time Per Trigger = 200.084 ms. It is not in the four FPGA VIs (Cycle (ticks) = 8,000,000 exactly) nor in Camera times to waveform times; it must be where 'Waveform Parameters' (Time Per Trigger (ms), AO per Trigger, # of Pulses per Trigger, AO Rate (kHz)) are computed - HHMI - SPIM Make Ramp Waveform / HHMI - SPIM Generate Waveform Ramp / 'Check AO Max Rate RAMP' / '# that Ramp needs' / 'Rate needed for flyback' (DAQ/Waveform/Linear), which I did not read beyond Compute AO rate from Cycle Time and the visible frame of Min AO rate needed.
- Q2: what the camera's 'Cycle(s)' fed into Camera times to waveform times contains for the Orca in sync-readout mode (readout-limited period vs exposure + readout): inside 'Orca4.0 Calc SyncRdt Exp' and the hidden free-run branch of DCAM - Read cycle times (hidden d1-d4 not read), and how DCAM - Read cycle times' output is written into the Camera Read Settings FG / 'Camera settings' cluster (Camera Set / HHMI - DCAM Settings FG not read).
- Q1: whether the 'Camera settings' cluster wired into Calculate if camera needs the waveform to be recalculated is the post-DCAM actual settings (Exp(s) = Actual exposure) or the requested settings - it comes from the 'Camera Set' output per the SPIM MAIN d340 fact; Camera Set was not read.
- Q1: the DCAM - Set Exposure call path (Camera Set / DCAM Settings - Camera 1 / HHMI - DCAM Settings FG hidden frames) - only the absence of any Cam exp -> camera path in the VIs read is established.
- Q3: whether any VI outside those read (HHMI - Recalc SPIM Waveform, SCAN - Make Waveform, Read Wvfrm for cntrls, DCAM Settings FG) also tests 'Custom Cycle Time'; the Waveform-typedef p.png (HHMI - SPIM Waveform cluster.ctl) was not opened to confirm element order.
- Q3: the caller of HHMI - Coerce Camera Settings Exposure or Cycle Time (assumed 'Camera Chgd Settings' from SPIM MAIN d124) and whether the Camera-tab 'Cycle time (ms)' it coerces is displayed anywhere on the Camera tab.
- Q4: the arithmetic inside 'Rate needed for flyback' and '# that Ramp needs' (how Wait for Zsettle? = Wait / Skip imgs / No settle changes the required AO rate or extra images), and the caller of HHMI - Generate settle settings for FPGA / how its 'Settle time' cluster reaches the FPGA (not called from the four FPGA VIs).
- Q4: where '# of Integrations' enters '# of Exps Desired' / GLOBAL '# imgs' (SPIM MAIN '# imgs Config' and Camera Set, not read here).
- Q5: entirely - which modes grey Cam exp / Cycle time / Custom Cycle Time; look in SPIM MAIN 'Enable-disable' frame 5 (d337) and the 'Scan state enable/disable' sub-VI, and the Waveform cluster's own Disabled property.
- Q6: which FPGA registers each field of 'FPGA Setup Settings' lands in ('Dev Config' bridge VI and the FPGA-side 'HHMI - SPIM FPGA Main VI' / Globals not read); whether the FPGA tolerates an AO waveform per trigger longer than Cycle (ticks) (Trigger Manager 'Trigger type' / 'AO Image?' logic only partially visible).
- Q6: that 'Trigger Up (s)' = 0.0001 s is the saved default (the panel render shows the current panel value; the caller leaves the terminal unwired, so the default is what runs).
- Q6: hidden frames of HHMI - Camera trigger settings for FPGA (other camera types / sensor modes) were not exported, so Trigger delay (ticks) and Cam Trigger delay (ticks) for non-Orca4.0 / non-Light-Sheet cases are unknown.
- Identity of sub-VIs is by icon caption + connector match only: the report.html of every VI in this set contains just the VI's own panel and diagram images and no sub-VI list.
- Q1: Whether Cam exp (s) is ever WRITTEN from the camera's exposure in the waveform chain -- not in any VI I read; the other readers' d340 shows 'Camera Recalc Wvfrm?' writing Cam exp (s) via Val(Sgnl) inside Set Camera, so the answer is inside that sub-VI (probably HHMI - Change waveform and update camera.vi, exported with 0 hidden frames) and/or HHMI - Camera times to waveform times.vi / HHMI - Coerce Camera Settings Exposure or Cycle Time.vi.
- Q1: Whether DCAM - Set Exposure.vi is ever fed from Cam exp (s) rather than from the Camera tab's Exposure -- none of my VIs calls DCAM - Set Exposure; look in HHMI - DCam Module.vi (51 hidden frames), DCAM - Setup.vi (11 frames) and DCAM - Set Exposure.vi (10 frames), and at what fills 'Exposure Time (s)' of the DCAM 'Setup Settings' cluster (HHMI - DCAM Settings FG p.png shows the field but no writer).
- Q2: The value Cycle time (s) takes when Custom Cycle Time is OFF (camera cycle from DCAM - Read cycle times? exposure plus readout?) -- Make Ramp Waveform only copies whatever is already in the cluster into Cycle (sec); the source is upstream: 'Camera Recalc Wvfrm?' in Set Camera (d340) writes Cycle time (s).Value, and HHMI - Generate SPIM Waveform.vi / HHMI - Coerce SPIM Waveform.vi (2 hidden frames each) may also set it before Make Ramp Waveform is called.
- Q2: The exact coercion when Custom Cycle Time is ON (minimum vs camera cycle, maximum) -- not in my VIs; look in HHMI - Coerce Camera Settings Exposure or Cycle Time.vi (4 frames), HHMI - Camera times to waveform times.vi (2 frames), HHMI - Coerce SPIM Waveform.vi.
- Q2/Q6: The AO-point rounding that turns 0.2 s into 200.084 ms -- Time Per Trigger (ms) is produced by the 'CALC 1 FAST RAMPS' icon in HHMI - SPIM Generate 1 Line Ramp.vi, whose file is most likely HHMI - SPIM Calc Single Fast and AOTF Line Ramp.vi or ...Ramps.vi (exported, 2 hidden frames); the point counts come from HHMI - SPIM number of points and points per second that Ramp needs.vi ('# that Ramp needs' icon) and HHMI - SPIM compute Min Rate needed for Flyback.vi ('Rate needed for flyback' icon), and the rate clamp from 'Check AO Max Rate RAMP' (HHMI - SPIM Check AO rate / Min AO rate needed callee). The arithmetic inside those was not read.
- Q3: Which VI tests Custom Cycle Time -- it is absent from every waveform-generation VI I read (see facts); candidates in order of likelihood: the 'Camera Recalc Wvfrm?' sub-VI in Set Camera (d340), HHMI - Coerce Camera Settings Exposure or Cycle Time.vi, HHMI - Camera times to waveform times.vi, HHMI - Generate SPIM Waveform.vi, HHMI - Coerce SPIM Waveform.vi, HHMI - SPIM Time per exp.vi.
- Q3: What happens to Cycle time when Cam exp changes with Custom ON vs OFF -- follows from the previous item; not visible in my frames.
- Q4: Whether '# of Integrations' or '1 exp per' change Cycle time / Cam exp anywhere -- they are cluster fields never unbundled in the ramp chain I read; the '# imgs Config' / 'SCAN # of pulses' path (HHMI - SPIM Calculate number of pulses.vi) and Set Camera's 'Camera Set' are where integrations could enter the camera cycle.
- Q5: Which scan states grey the Waveform cluster -- inside 'Scan state enable/disable' (other readers' d337); nothing in d235-d238 or the Adv page render shows a per-field Disabled write.
- Q6: Conversion of Time Per Trigger / AO Rate into FPGA ticks -- not in any VI I read; HHMI - Generate settings for AO and AI FPGA.vi (1 frame), HHMI - Generate trigger settings for FPGA.vi (8 frames), HHMI - Configure FPGA for waveform.vi (2 frames) consume the 'Waveform Params' global written in SPIM MAINd2.
- Identity of the 'SCAN Make Wavefrm' icon as HHMI - Generate SPIM Waveform.vi: SPIM MAIN's report.html lists no sub-VIs, so this remains a name-based inference (the Recalc VI calls the same icon with a Waveform cluster + Force Recalc? Boolean, consistent with Generate SPIM Waveform's role).
- Identity of the 'Z DELAY' icon in Make Ramp Waveform hidden frame d1 (which VI computes msec to settle Z from Zpiezo.Pixel Size) -- caption only.
- Which of the three DBL outputs of 'CALC 1 FAST RAMPS' maps to Time Per Trigger (ms) vs AO Rate (kHz) vs X Range (um) at the sub-VI's connector -- only the bundle destinations are readable in HHMI - SPIM Generate 1 Line Rampd.png.
- Whether the camera settings cluster field 'Cycle(s)' seen in SPIM MAINd79 is the same field as 'Cycle Time (s)' of the DCAM 'Setup Settings' cluster (HHMI - DCAM Settings FG p.png) -- labels differ; not established.
