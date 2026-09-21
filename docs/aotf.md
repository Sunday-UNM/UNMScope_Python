# AOTF excitation — what is wired, what was measured, what is not

**Measured on the deployed bitfile 2026-09-04, FPGA only**
(`spikes/32_aotf_registers_on_scope.py`; galvo channels clamped to
±100 mV with 0 content, AOTF test levels 164 / 82 counts = 50 / 25 mV for
under a second each). Code: `src/unmscope/hardware/fpga_trigger.py`
(`set_aotf_levels`, `read_aotf_out`, `safe_state`), `config/calibration.py`
(`Aotf`), the Acquire path in `gui/main_window.py`.

## What the GUI does now

Every enabled Excitation row (any number, with Power > 0 -- see "The
operator decides which channels are driven" below) sets its AOTF channel
level at Acquire and clears it at Stop / Disconnect / any failed arm:

```
row 0 (637) -> AOTF ch 0 = AO5      row 2 (488) -> AOTF ch 2 = AO7
row 1 (561) -> AOTF ch 1 = AO6      row 3 (405) -> AOTF ch 3 = AO3
level = Power % / 100 x (Max - Min) + Min   from [AOTF Limits (V)] = 0 .. 5 V
counts = volts x 3276.7                     (the FPGA's +-10 V / 16-bit DAC)
```

So 488 nm at 100 % writes `AOTF ch (V)` = {ch 2: 16384}, all other
channels 0, and the FPGA's `AOTF ch out (V)` reads the same back. The
GUI log line: `AOTF: 488 nm at 100 % -> AOTF ch 2 = 5.000 V (16384
counts); other channels 0.`

Row → channel is the identity. LabVIEW routes through each row's `Laser
ch` field, which is 0 for every row in the saved `SPIMProject.cfg`, and
its "Cycle lasers = None" mode pins the channel to 0 — i.e. on the rig
LouisXIV puts every laser on channel 0. The user's decision (2026-09-04):
the physical patch from AOTF channel to laser is what will be changed, so
the software keeps one channel per row.

**The operator decides which channels are driven.** Whatever Excitation
rows are ticked with Power > 0 % get their AOTF channel driven --
including more than one. LouisXIV's `HHMI - Check that only 1 laser is
selected.vi` REFUSED to start unless exactly one was on; on the user's
instruction (2026-09-07) that is a log line now, not a block, and a run
with nothing ticked is a legitimate dark run for checking waveforms.
The port does not force any AOTF channel to 0 V on its own; the only
automatic zeroing left is on Stop and reset.

**Simulate-on-FPGA** (Simulated camera + real FPGA): the AOTF is driven
exactly as in any other mode. **Changed 2026-09-07 on the user's
instruction**: *"Your automatically forcing the AOTF to 0V is throwing
off my trouble shooting process."*

Every AOTF level used to be forced to 0 and the gate refused
(`AOTF on?` = False) alongside the galvo clamp. That conflated two
different things. The AO clamp stops the **mirrors and the piezo**
moving; it has never had anything to do with the AOTF, and zeroing it
removed the one trace a simulate-on-FPGA run is usually being watched
for -- the channels simply read flat, with no indication why.

Now nothing in the software zeroes or gates the AOTF on its own:

| switch | what it is | default |
|---|---|---|
| `aotf_levels={ch: counts}` | the DC level in `AOTF ch (V)` | written as given, clamped run or not |
| `allow_aotf_gate` | the `AOTF on?` permission in the AO limits | `True` -- pass `False` to refuse it deliberately |

The AO clamp itself is unchanged and still verified by readback, and
levels still return to 0 V at Stop and reset.

## What the bitfile was measured to do

| experiment | result |
|---|---|
| write `AOTF ch (V)` = {ch0: 164, ch2: 82}, default modes (`AOTF Mode` = 2 "Set AOTF", `AOTF ch Mode` = 2 "Set AOTF ch") | `AOTF ch out (V)` reads {164, 0, 82, 0} **immediately and stays there** — a DC level, no gating needed |
| same levels during a free run with the per-point gate in the stream | out stays {164, 0, 82, 0} for the whole run |
| `AOTF ch Mode` = 0 or `AOTF Mode` = 0 ("Start/Run Wvfrm"), sweep mode Sync or Step, with the AOTF clock registers set | out = 0 — the AOTF waveform engine needs configuration we do not have (its channel FIFO is never loaded) |
| the FPGA Scope (`AI data` stream, all 29 columns) throughout | the AOTF **never appears**: columns 8–13 carry the galvo/tiling/filter values and 16–22 (AOTF0–6 flags) stayed 0 in every run |
| `safe_state()` | levels 0, `AOTF level to set` False, readback 0 |

The stream's `AOTF on?` bit (the 7th field of the `AO DMA` cluster) can
be set from the prefix slots — `AO DMA` read True while a gate was
streamed — but in the working mode the level does not depend on it.

## The time model, honestly

**KNOWN WRONG as of 2026-09-08.** The user measured AOTF 2 at the pin
during a run: a flat **1.92 V** -- the correct level for 488 nm at 38.5 %
(0.385 x 5 V), so the row -> channel -> volts -> counts -> pin path is
right -- but flat. Their words: *"this should be a modulating synchronised
square voltage."* So the DC-for-the-whole-run model described below is not
what this rig needs.

**Resolved the same day by measurement, not argument: it cannot be fixed
in software on this bitfile** -- see "MEASURED: the engine is not
reachable on this bitfile (spikes/38)" below. What WAS fixed is that a
ticked row now drives its channel immediately rather than only at
Acquire. Three things stack up to produce the flat trace:

1. `louisxiv_waveform.aotf_gate()` returns all ones when `AOTF cycle` is
   "None" (this rig's setting). Built from a 2026-09-07 read of LouisXIV's
   own X Waveform graph -- now contradicted, see that function's docstring.
2. ~~The gate is kept out of the packed AO words~~ -- FIXED. It is now
   streamed as the cluster's `AOTF on?` bit; it needs no analog slot,
   being one bit per point.
3. The AOTF **clock engine** is unreachable on this bitfile (measured,
   table below) -- but LouisXIV does not use it either, so this does not
   block the blanking.

Item 1 is a wrong assumption in our code and is marked as such in the
function. Items 2 and 3 are properties of the deployed bitfile and are not
addressable from the host.


"On for the whole acquisition." The level is written just before the FPGA
is armed and zeroed the moment the run stops. There is **no per-frame
blanking** (off during the galvo flyback) from Python on this bitfile:
LabVIEW's design does that either per point in the AO stream (source
build, where AOTF0–3 were columns of the AO DMA cluster — the deployed
build has Tiling/Filter there instead) or with the AOTF clock engine
(`AOTF Delay (Ticks)` / `AOTF pulse width (ticks)` / `AOTF # of points per
trigger`), which produced nothing here. Getting that engine going is the
next step if flyback blanking matters; LabVIEW's rule for it is "on
during the linear forward sweep, off for the entire return move, plus a
galvo-lag delay from the X pixel size" (`HHMI - Pockel Cell or AOTF
Linear Ramp.vi`, `HHMI - SPIM Calc AOTF delay.vi`).

## How to verify on the rig

- `python -u spikes/32_aotf_registers_on_scope.py` — the register-level
  proof, FPGA only.
- **Physically confirmed on the pin (2026-09-04):** AOTF ch 2 -> AO7,
  metered with a BNC on that channel while the software stepped it
  0 / 1 / 2.5 / 5 V. The voltage tracked on the instrument, register and
  FPGA `AOTF ch out (V)` indicator agreeing at each step. This is the first
  AOTF (and first analog) output verified at the pin, not just internally;
  the ±10 V / 16-bit DAC scale (3277 counts/V) is now confirmed for AO7.
  ch 0/1/3 (AO5/AO6/AO3) were not on the meter but are register-identical.
  NB a *steady* small level is easy to miss on a meter — step it (a moving
  reading is unmistakable) rather than holding a fixed DC value.
- Not measured: per-channel power calibration (LouisXIV can load an "AOTF
  power cal" V-vs-mW table; the GUI uses the linear percent map).

## The AOTF clock engine, read out of the source (2026-09-08)

Read in answer to "this should be a modulating synchronised square
voltage". Sources: the FPGA block diagrams under
`VI_Diagrams/FPGA code/FPGA VIs/AOTF Clock/` and `.../AOTF channel/`, the
host VIs under `SPIM LV8.6 VIs/Calibration/AOTF delay/`, and the register
table inside the deployed bitfile
`UNMScope_Source/bin/data/SPIMFPGAProject_SPIM_MAIN_VI.lvbitx`.

### The engine is a square-wave generator, and it is in the deployed bitfile

`HHMI - Execute AOTF Clock Waveform state.vi` carries its own timing
diagram on the block diagram. Per fast-axis step:

```
X Galvo    ___|‾‾‾‾‾‾‾‾ Step 1 ‾‾‾‾‾‾‾|‾‾‾‾‾ Step 2 ...
              ^ Start time            ^ next Start time
AOTF Clock ______|‾|__________________|‾|_____|‾|____
             <-->  Pulse width      <-->
             AOTF Delay             AOTF Delay
              (turn AOTF "on")       (turn "off")   (turn "on")
AOTF       ‾‾‾‾‾‾‾‾‾|________________|‾‾‾‾‾‾‾‾‾‾‾‾‾‾
           <----- AO ticks between points ----->
```

So the AOTF output is a square wave phase-locked to the AO point clock,
with a programmable lead (`AOTF Delay`) and high time (`Pulse width`) --
exactly the "modulating synchronised square voltage" that is expected.
Diagram notes, verbatim: *"When AOTF Delay is < Pulse width, only 1st
pulse is performed"* and *"This .vi is expected to run in a Single-Cycle
Timed Loop."*

State machine (`AOTF Waveform state to execute`): **Wait for trigger** ->
rising edge on `AOTF start trigger` -> **Output Wave**; emits
`AOTF ch step trigger` to the channel loop and `Start time (Ticks)`.
While waiting it holds the AOTF clock line low.

### The `AOTF ch FIFO` holds channel numbers, not levels

`HHMI - AOTF Clock Control Loop.vi`, case `AOTF Mode` = "Start/Run Wvfrm":
reads `AOTF ch FIFO` at `Address`, and `Data` goes to the **global**
`AOTF ch` ("AOTF channel to be used for this step will be set on the
global variable until 1st AOTF clock pulse finishes", "Globals set in the
same tick"). Then `AOTF ch > 0` AND ... gates the global `Start sweep?`.
The FIFO address increments on "1st pulse complete?".
`HHMI - AOTF ch FIFO size.vi` = **4** entries -- one per step of a laser
cycle, which is what the GUI's "Cycle lasers" control is for.

**Consequence:** with the FIFO unloaded, `AOTF ch` reads 0, `> 0` is
False, `Start sweep?` never asserts and nothing is output. That is
exactly what `spikes/32` measured when it put `AOTF Mode` / `AOTF ch Mode`
into mode 0 and got 0 V out.

### MEASURED: the engine is not reachable on this bitfile (spikes/38)

A PB-register hypothesis was raised first -- the deployed bitfile has
`AOTF # of points per trigger PB` and `AOTF pulse width (ticks) PB`
twins, and `Trigger blast #s = {1,0}` routes every trigger through the
`... PB` bank whose defaults are 0, which is exactly what caused the
phantom "Buffer Underflow" on the AO path. **It is wrong.** `spikes/32`
experiment D already wrote both PB copies together with the plain ones
and got 0 V, and `spikes/38` confirmed it again.

`spikes/38` then swept the full 3x3 mode matrix on the real card with the
clock registers AND the previously-unwritten `AOTF Sweep settings` cluster
populated (10 ms on / 10 ms off), judging the target channel against the
requested level and cross-checking AI scope column 19:

| `AOTF Mode` (clock loop) | `AOTF ch Mode` (channel loop) | result |
|---|---|---|
| any of Set / Start-Run / Stop-Config | **Set AOTF ch** | DC 6308 counts = 1.925 V |
| any of Set / Start-Run / Stop-Config | Start/Run Wvfrm | 0 V |
| any of Set / Start-Run / Stop-Config | Stop/Config Wvfrm | 0 V |

Two things fall out of that table:

1. **`AOTF ch Mode` alone decides the output.** `AOTF Mode` changed
   nothing in any of the nine cells, so the AOTF **Clock** loop is not
   consuming its register in this build -- consistent with the
   checked-out FPGA top level (`HHMI - SPIM FPGA Main VI.vi`)
   instantiating only the AO, AI and Shutter loops.
2. **The channel loop's waveform mode starves.** Put in Start/Run it
   outputs 0 forever: the `Start sweep?` global that would release it is
   gated on an `AOTF ch FIFO` read, and nothing ever loads that FIFO.

The FIFO cannot be loaded from the host either -- the bitfile declares
exactly three DMA channels: `Wvfrm2` (HostToTarget I64), `Get Exp` and
`AI data`. Nor can the AOTF DACs be driven from the AO stream: all eight
analog outputs are spoken for (AO0 Z Galvo, AO1 X Galvo, AO2 Z Piezo, AO4
Dither Galvo, AO3/AO5/AO6/AO7 = AOTF ch 3/0/1/2), and the two remaining
streamed slots, Tiling and Filter, have no Connector0 AO pin.

So the AOTF **clock engine** is a dead end on this bitfile.
`spikes/38_aotf_modulation_engine.result.txt` has the full log.

**But that was the wrong engine.** See the next section: LouisXIV does not
use the clock engine either -- it blanks the AOTF from the AO stream, one
bit per point.

### SETTLED 2026-09-08: the gate travels in the AO stream

The user exported LouisXIV's own "Full Waveform" (13869 points, this rig's
live settings: X range 100 um / 60 pixels, exposure 0.1 s, cycle
125.159 ms, Fractional Flyback 0.15, 201 slices, AOTF cycle = None). Its
columns are X Galvo, Z Galvo, Z Piezo, Sample Piezo, Tile, Filter
Position, **AOTF (V) Ch0..Ch6**, Dither | ZP2.

`AOTF (V) Ch2` is **binary** -- exactly two distinct values -- and notches
once per slice:

    60 points high, 9 points low, x 201 slices
    = 69 points per line, 86.957 % duty

That is `on_points` high and the return move low: on through the forward
sweep, off through the flyback, exactly what `HHMI - Pockel Cell or AOTF
Linear Ramp.vi` describes. Because the per-point AOTF datum is a single
BIT, it needs no analog slot in the DMA cluster -- it is the cluster's
seventh field, the `AOTF on?` bool. The level stays in `AOTF ch (V)`; the
stream only says whether it reaches the pin.

Two conclusions, both verified in code:

1. **The blanking is unconditional.** `aotf_gate()` used to return all ones
   for `aotf_cycle` = "None" (this rig's setting) on the strength of a
   2026-09-07 read of LouisXIV's X Waveform graph. That was wrong -- on
   that graph one cycle is 87 % high and pinned to the top of its own axis,
   so the 9-point notch never rendered. Removed.
2. **Our line model was already right.** At `fractional_flyback` = 0.15 --
   the rig's own panel value -- `build_louisxiv_waveform` produces
   total 69 / on 60 / return 9, and `aotf_gate` reproduces LouisXIV's
   exported column **point-for-point over all 13869 points**. The gate is
   now packed into the Wvfrm2 words (`waveform.AOTF_GATE_SLOT`) instead of
   being attached afterwards as a display-only channel.

**Still unconfirmed: which prefix slot is the `AOTF on?` bool.** The DMA
cluster is six I16 plus one bool and `pack_points` has eight slots, so one
of the two "prefix" slots carries it. Software cannot tell them apart --
the `AO DMA`.AOTF on? indicator reads constant True even with nothing
streamed at all (measured 2026-09-08), which is very likely what misled
`spikes/32` into reporting the gate "reaches" the card. Only a scope on
AO7 can settle it. `AOTF_GATE_SLOT` is set to `prefix0`; if AO7 does not
blank, flip it to `prefix1`.

A real 20-trigger free run with the gate streamed is healthy: 20/20
triggers, 1380/1380 AO points generated, no DMA error, 0 int-cycle
mismatches.

### What the level path does do (measured 2026-09-08)

`AOTF ch (V)` reaches `AOTF ch out (V)`, and the pin, **immediately and
unconditionally** -- no run, no arm, no gate, and `AOTF level to set`
makes no difference. So a ticked Excitation row can be live the moment it
is ticked, and since 2026-09-08 it is: `MainWindow._push_aotf_levels()`
writes the levels on every tick and every Power % change, and once at FPGA
connect, instead of only at Acquire. Verified through the real GUI path on
the real card: 38.5 % -> 6308 counts (1.925 V) on AOTF ch 2, 77 % -> 12615
counts (3.850 V), 0 when unticked, nothing acquiring throughout.

### Mode enums, from the bitfile itself (authoritative, not inferred)

| register | offset | 0 | 1 | 2 |
|---|---|---|---|---|
| `AOTF Mode` (U16) | 350 | Start/Run Wvfrm | Stop Wvfrm | **Set AOTF** <- ours |
| `AOTF ch Mode` (U16) | 314 | Start/Run Wvfrm | Stop/Config Wvfrm | **Set AOTF ch** <- ours |

### The delay is a calibration lookup, not a formula

`HHMI - SPIM Calc AOTF delay.vi`: `AOTF delay (us)` is a Waveform-Config
field where **-1 means auto**; auto calls
`HHMI - SPIM Convert X pix size to AOTF delay.vi`, which **linearly
interpolates** X pixel size (um) against a `um` / `delay` calibration
table supplied by the caller. That table is NOT in `SPIMProject.ini`
(only `[AOTF Settings]` labels and `[AOTF Limits (V)]` 0..5 V are) --
where it lives is still open.

### Caveats -- read before trusting the above

1. **The diagrams are not the deployed build.** The typedef paths inside
   the running bitfile point at `E:\Users\Gerard\...\LouisXIV_GIT\
   lightsheet_distributed\`, while the checked-out tree is dmilkie's. The
   register names match exactly, so the logic is very likely the same, but
   it is not proven identical.
2. **The checked-out FPGA top level does not even instantiate this
   engine.** `HHMI - SPIM FPGA Main VI.vi` references only
   `HHMI - AO Control Loop.vi`, `HHMI - FPGA AI Loop.vi` and
   `HHMI - Shutter Control Loop.vi` -- no AOTF loop. The deployed bitfile
   plainly does carry the AOTF registers, so the two builds differ here.
3. **No host VI in this source ever writes the clock registers.**
   `AOTF Delay (Ticks)`, `AOTF pulse width (ticks)` and
   `AOTF # of points per trigger` appear ONLY inside the `.lvbitx`, in no
   `.vi` at all. So this source build of LouisXIV does not drive the clock
   engine either -- worth reconciling against the modulation seen on the
   rig.

### The cheap decisive test (not run -- needs sign-off, it drives real AO)

On the bench, during an otherwise normal free run: set `AOTF Mode` = 0 and
`AOTF ch Mode` = 0, write `AOTF Delay (Ticks)`, and write **both** the
plain and PB copies of `AOTF pulse width (ticks)` and
`AOTF # of points per trigger`; load the `AOTF ch FIFO` with a non-zero
channel. Watch AO7 (AOTF ch 2). If the PB reading is right, a square wave
appears where 1.92 V DC is today.

## MEASURED ON REAL HARDWARE: the AOTF / trigger / piezo relationship

Source: the user's FPGA-Scope export of a live LouisXIV acquisition
(2026-09-08), 1,048,575 samples at **5 us**, 5.2429 s, channels X Galvo,
Int Cycle Trigger, D4 Cam Ext Trig Out, AOTF 0, AOTF 1, Z Galvo, Z Piezo,
Dither Galvo. AOTF 1 was ticked deliberately to show the relationship.
Averaged over all **27 cycles**.

| quantity | measured |
|---|---|
| cycle (trigger to trigger) | **200.084 ms**, jitter sd **1.8 us** |
| `D4 Cam Ext Trig Out` high | **0.1000 ms** — the hard-coded 100 us pulse |
| `Int Cycle Trigger` high | 0.1000 ms, same period |
| AOTF 1 rising vs D4 rising | **+1.3 us** (sd 2.2 us) — *simultaneous* |
| AOTF 1 falling | **+173.987 ms** (sd 2 us) |
| AOTF 1 high time | 173.99 ms → **87.0 % duty** |
| Z piezo moves | **+173.982 → +179.782 ms** |
| X galvo peak (flyback begins) | ≈ +171 ms |

One cycle, to scale:

```
                 0                                              174  180     200 ms
Cam trig (D4)    |______________________________________________________________
AOTF 1           ‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾______________
X Galvo          //////////////////////////////////////////////\\\\\\\\\\\\\\\\\
Z Piezo          ______________________________________________---______________
                                                                ^ laser off AND
                                                                  piezo step, together
```

Three facts worth keeping:

1. **The laser turns on with the camera trigger**, to within one 5 us
   sample. Not delayed.
2. **The laser turns off and the Z piezo starts moving at the same
   instant** — 173.987 vs 173.982 ms. Not sequenced with a settle gap
   before the move; the settle happens *after*, in the remaining ~20 ms.
3. **87.0 % duty at a 200 ms cycle**, against 86.957 % (60/69) computed
   from the other export at a **125.159 ms** cycle. The gate scales with
   the line; it is not a fixed time. That is what `aotf_gate()` produces.

The 100 us trigger width agrees with `HHMI - Generate External Camera
Trigger.vi` ("set trigger high for 100us"), and the simultaneity of the
trigger and the AO block start agrees with `HHMI - Trigger Manager`
issuing both every cycle.

**The AI scope stream carries the gated AOTF output.** That is how this
was measured, and it means our own Waveforms tab can verify AOTF
modulation without a physical probe — correcting the claim earlier in this
document that only a scope on AO7 could settle it.

## FIXED 2026-09-16: gate channel selection and on-duration

Two bugs the user found in the "AOTF Digital Gate" box (`main_window.py`,
`_start_acquisition`):

1. **Enable ignored the Excitation rows.** The call site hard-coded
   `channels=(0, 1, 2, 3)` regardless of which Excitation row was ticked, so
   checking Enable drove all four AOTF terminals -- an unticked row's
   channel should stay at 0 V. Fixed: the gate now only covers
   `tuple(sorted(aotf_levels))`, the exact channel set
   `_aotf_levels_for_run()`/`_push_aotf_levels()` already use (ticked row,
   Power > 0). Enable with nothing ticked now logs and does nothing, rather
   than starting a gate over no channels.

2. **On-duration used the camera's raw `exposure_s`, not the swept
   fraction.** `start_aotf_digital_gate(period_s, exposure_s, ...)` held the
   gate open for `lead_s + exposure_s` after each trigger. That is only
   right if the camera's configured exposure happens to equal the forward
   sweep length, which is not what the measured relationship above says:
   the laser is on for `on_points / total_points` of the cycle (87.0 % here),
   not for a fixed exposure time. Fixed: the call site now passes
   `line.on_points / rate.ao_rate_hz` (`line.total_points` if bidirectional)
   from the very `lx` the run just armed -- the same quantity `aotf_gate()`
   uses, so the digital gate's on/off edges track the actual forward-sweep
   /retrace split of this waveform instead of an unrelated camera setting.
   `gap_s` (period_s - on-duration), which the lead-clamp safety logic is
   based on, is now the true retrace+dead time instead of an approximation.

Both are `main_window.py`-only changes; `start_aotf_digital_gate()`'s
signature and scheduling logic are unchanged, only what is passed to it.
