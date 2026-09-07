# AOTF excitation — what is wired, what was measured, what is not

**Measured on the deployed bitfile 2026-09-04, FPGA only**
(`spikes/32_aotf_registers_on_scope.py`; galvo channels clamped to
±100 mV with 0 content, AOTF test levels 164 / 82 counts = 50 / 25 mV for
under a second each). Code: `src/unmscope/hardware/fpga_trigger.py`
(`set_aotf_levels`, `read_aotf_out`, `safe_state`), `config/calibration.py`
(`Aotf`), the Acquire path in `gui/main_window.py`.

## What the GUI does now

The one enabled Excitation row (exactly one with Power > 0 is enforced,
LouisXIV's rule) sets its AOTF channel level at Acquire and clears it at
Stop / Disconnect / any failed arm:

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
