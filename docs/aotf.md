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

**Simulate-on-FPGA** (Simulated camera + real FPGA): every AOTF level is
forced to 0 and the AO limits refuse the AOTF gate (`AOTF on?` = False),
the same way the galvos are clamped. The log says `SIMULATE ON FPGA:
AOTF forced OFF (would have been ...)`.

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
- A real check of the analog output needs a meter or a BNC loopback from
  the channel's AO pin into AI0: the FPGA Scope's AI0 column would then
  show the level at 10 µs resolution. Not done yet.
- Not measured: the physical volts-per-count on the AOTF AO pins (assumed
  the same ±10 V / 16-bit DAC as the galvo channels), and any per-channel
  power calibration (LouisXIV can load an "AOTF power cal" V-vs-mW table;
  the GUI uses the linear percent map).
