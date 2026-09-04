# `Wvfrm2` — the AO waveform stream, decoded

**Decoded on the deployed bitfile 2026-09-03 with the FPGA Scope, FPGA
only** (`spikes/28_wvfrm2_packing_on_scope.py`, `spikes/28b_wvfrm2_prefix_slots.py`).
AO limits were set to ±328 counts (±100 mV) for these runs so the small
test values passed the FPGA's range check; nothing was connected to the
AO BNCs. Code: `src/unmscope/hardware/waveform.py`.

## How the engine consumes the stream

- `Wvfrm2` is a host→FPGA DMA FIFO of I64 words. The AO engine reads
  **two words per point**. After each trigger it outputs
  `AO # of points per trigger` points, one every `AO ticks between points`
  (4000 ticks = 100 µs was used; each point dwelt exactly 10 scope samples
  at 100 kS/s), then **holds the last point** until the next trigger.
- The first point of a block lands **30–60 µs after the DIO4 edge**
  (3–6 scope samples; `AO Trigger delay (ticks)` = 0).
- The refill thread (256-word blocks, repeating pattern) kept up without a
  fault at 200 points (400 words) per 100 ms trigger.

## The packing

Method: four words packing sixteen distinct small values, watched on the
scope's AO columns (which sample the `AO DMA` point *after* the range
check). Splitting each word into I16 slots (`Split I64 into 4xI16.vi`),
the mapping came out as:

| word | bits 63..48 | bits 47..32 | bits 31..16 | bits 15..0 |
|---|---|---|---|---|
| even (first of the pair) | **prefix0** | **prefix1** | **X Galvo** | **Z Galvo** |
| odd (second) | **Z Piezo** | **Dither Galvo** | **Tiling** | **Filter** |

Read high slot first, a point is the 8-channel row
`[prefix0, prefix1, X Galvo, Z Galvo, Z Piezo, Dither Galvo, Tiling, Filter]`
— the deployed `AO DMA` cluster order preceded by LabVIEW's two "prefix
columns" (`Setup AI DMA buffer.vi` / `Duplicate AO array...vi`:
"# of prefix columns", "Add Prefix?"). The scope's column 12 (named
"AOTF0 (AO)" in the source build) therefore carries **Tiling** in the
deployed build, and column 13 carries **Filter**.

Values are DAC counts, ±10 V over 16 bits (3276.7 counts/V), each range-
checked against `AO Limit Max/Min (counts)`.

## The prefix slots

Cycling prefix0/prefix1 through 0, 1, 255 and 32767 (`spikes/28b`, all 29
scope columns captured) changed **nothing observable**: the AOTF0–6,
Perfusion, Shutter and Channel Shutter columns stayed 0, `AOTF ch out (V)`
stayed 0, and the six AO columns followed only their own slots. Whatever
the deployed build does with them (if anything) is not visible on the
scope; **keep them 0** until a use is found. Real AOTF levels come from
the `AOTF ch (V)` register cluster, not from the stream.

## The last block of a bounded run is cut when Int Sync drops

Measured (`spikes/29b`): with `Trigger up (ticks)` = 10 ms, every block of
a bounded run completed except the **last** one, which stopped after
exactly 101 points = 10.1 ms — the moment the Int-Sync pulse went low with
triggers no longer enabled. Blocks 1..N−1 run to completion because
triggers are still enabled. `# AO generated` (which resets on
`Stop Wvfrm`) reflects this exactly: 902 for 5 × 200 points was 4 full
blocks + 101, not a counter offset.

Fix (`spikes/29c`, confirmed): make the Int-Sync high time cover the
block — `trigger_up_ticks = points × ticks_between_points + 40 000`
(block + 1 ms), clamped below `Cycle(Ticks) − 4000`. All five 880-point
blocks then completed and `# AO generated` read 4400. Nothing else
depends on the sync pulse width (the trigger is edge-detected).
`FpgaTriggerController.start_free_run(trigger_up_ticks=...)` takes it;
the GUI always passes block + 1 ms.

## First real waveform (`spikes/29c_trigger_up_covers_block.py`)

`build_scan_waveform()` — X galvo linear sweep over the exposure plus a
10 % flyback, Z galvo/piezo constant per slice and stepping per slice —
streamed as a repeating pattern of `n_slices × points_per_trigger` points.
5 slices × 880 points at 100 ms, clamp ±328 counts: every block 880 points,
X −0.0900 → +0.0900 V and Z 0 / 15 / 30 / 45 / 60 mV with **0 counts of
error** against the sent words (change-aligned sampling on the scope).
With `period_s` given the block is shortened to fit `period − 2 ms`, which
SYNCREADOUT needs (period == exposure).

## Dither galvo (AO4 / scope column 11)

`SPIMProject.ini` gives the dither axis 10 µm/V with ±5.5 V limits, and
the Dither box's three fields map onto LabVIEW's triangle generator
(`HHMI - SPIM Make Ramp Waveform.vi` "D" case →
`Generate Triangular Waveform.vi`):

| GUI field | LabVIEW | in `build_scan_waveform()` |
|---|---|---|
| Range (µm) | sweep amplitude, peak-to-peak, via 10 µm/V | `dither_range_v` |
| # Sweeps | `Dither Triangle Pulses` (fractional allowed, default 5.5) | `dither_pulses` |
| Fract. Flyback | turnaround smoothing fraction of a half period | `dither_flyback_fraction` |

`triangle_points()` reproduces the VI's construction exactly:
`round(2 × pulses)` equal linear segments of alternating direction across
the block, so 5.5 sweeps is 11 segments and the trace ends at the far
extreme. `smooth_turnarounds()` rounds each reversal over the flyback
fraction (a moving average, in place of LabVIEW's cubic overshoot fit) so
the galvo is never asked for an instantaneous reversal.

**Measured on the scope** (`spikes/31_dither_triangle_on_scope.py`, 400 mV
pk-pk × 5.5 sweeps, clamp ±500 mV, 3 blocks): column 11 showed
−193.8…+190.1 mV with 5 peaks per block in every block, against a sent
−196.2…+190.1 mV (the smoothing accounts for the rounding off 400 mV),
and the X sweep on column 8 was unchanged at 50.1 mV pk-pk.
