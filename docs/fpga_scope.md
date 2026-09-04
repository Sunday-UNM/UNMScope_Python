# FPGA Scope — the internal digital oscilloscope

**Decoded and verified on hardware 2026-09-03, FPGA only** (no camera, no
galvo; every AO channel at 0 V). Code: `src/unmscope/hardware/fpga_scope.py`
(`FpgaScope`), CLI: `tools/fpga_scope_monitor.py`, decode spike:
`spikes/25_ai_fifo_decode.py`, raw result: `docs/ai_fifo_decode_result.json`.

## What it is

LouisXIV's `HHMI - AI buffer.vi` plots the FPGA's `AI data` DMA FIFO. The
FPGA's AI loop (`HHMI - FPGA AI Loop.vi`) samples, every
`AI loop period (ticks)`, the eight real analog inputs AND a set of
internal signals into ONE I16 array of 29 elements, and
`HHMI - Send AI data to DMA.vi` streams the first `AI # of channels` of
them. Reading that stream in Python gives a software oscilloscope clocked
by the FPGA itself. It is what replaces the physical scope for all timing
verification, and it is the observer for the FPGA-only "simulate on FPGA"
mode.

## The packing — hypothesis from the source build, CONFIRMED on the deployed bitfile

Reference signal: the free-run trigger at 100 ms with `Trigger up` =
10 ms, so two digital signals with known periods and widths were in the
stream. Every column behaved exactly as the source diagrams predict:

| col | signal | measured (20 kS/s run) |
|---|---|---|
| 0–7 | Connector0/AI0..AI7 | floating inputs: means −0.5…+9 mV, noise σ ≈ 0.25 mV |
| 8–13 | AO values the engine outputs: X Galvo, Z Galvo, Z Piezo, Dither Galvo, AOTF0, Filter desired | all 0 (AO at zero) |
| 14 | `Int Sync (Cycle Only)` | 0/4096, period 100.002 ms, **high 10.000 ms** |
| 15 | `Cam Ext Trigger Out DO` = DIO4 read back | 0/4096, period 100.002 ms, **high 0.100 ms** |
| 16–22 | AOTF0..AOTF6 (digital) | constant 0 |
| 23 | Perfusion? | 0 |
| 24 | Shutter | 0 |
| 25–28 | Channel Shutter 0..3 | 0 |
| 29+ | (probe with `AI # of channels` = 40) | constant 0 → the array is 29 long |

Digital signals are bit-shifted by 12 in the FPGA (`4096` = True) "so
they display ok on the buffer graph". LouisXIV's default `Active Channels`
(8, 14, 15, 16, 17, 9, 10, 11) = X Galvo, Int Sync, DIO4, AOTF0, AOTF1,
Z Galvo, Z Piezo, Dither Galvo — consistent with this map.

## Rates the host keeps up with (2 s captures, no `AI Error`)

| `AI loop period` | rate | channels | I16/s | max backlog (4M buffer) | timing resolution |
|---|---|---|---|---|---|
| 2000 ticks | 20 kS/s | 29 | 580 k | 725 | 50 µs |
| 400 ticks | 100 kS/s | 29 | 2.9 M | 3 335 | 10 µs |
| 200 ticks | 200 kS/s | 16 | 3.2 M | 3 440 | 5 µs |

`FpgaScope` defaults: 16 channels (everything that varied) at 100 kS/s,
5 s ring buffer (16 MB). The reader thread checks `AI Error`
(`I/O Error` / `Buffer Underflow` — the AI loop's *Late?* bit /
`DMA Timeout`) twice a second.

## The oscilloscope result

| resolution | DIO4 period (requested 100.000 ms) | spread | high time |
|---|---|---|---|
| 50 µs | 100.0017 ms | sd 9 µs (one-sample quantisation) | 0.100 ms |
| 10 µs | **100.0000 ms** | **0.0000 ms** | 0.100 ms |
| 5 µs | 100.0003 ms | sd 1.1 µs | 0.100 ms |

The free-run trigger is locked to the FPGA clock: no host jitter at all,
as `trigger_free_run_plan.md` predicted. `tools/fpga_scope_monitor.py`
prints this once a second for as long as you like.

## Using it

```python
ctrl = FpgaTriggerController(); ctrl.connect()
scope = FpgaScope(ctrl)                     # shares ctrl's nifpga session
scope.start()                               # writes AI # of channels / AI loop period / Free run,
                                            # starts the FIFO + reader thread, and sets ctrl.ai_* so
ctrl.start_free_run(0.1, 0.08)              # ... every later arm keeps the stream configured
snap = scope.snapshot(seconds=1.0)          # (n, 16) int16, oldest first; snap.analog_volts(i), snap.digital(15)
stats = scope.trigger_stats()               # PeriodStats of DIO4 on the FPGA's clock
scope.stop()                                # FIFO off, AI # of channels = 0, Free run = False
```

Register facts: `AI # of channels` and `AI loop period (ticks)` are read
directly by the FPGA AI loop (no `Set F.P.` latch needed, written with one
anyway); `Free run` is the AI free-run flag (unrelated to trigger
repetition). **Measured: the stream only flows while the waveform engine
is armed** (`start_free_run()`); with the scope started and no trigger
armed, 3 s produced no frames even with `Free run = True`. The first
trigger pulse therefore sits at sample 0 of a capture (no rising edge to
detect), and between acquisitions the Waveforms tab keeps showing the
last capture.
`start_free_run()` writes all three from the controller's `ai_*`
attributes, so arming never silently switches a running scope off.

## GUI: the Waveforms tab (`src/unmscope/gui/scope_view.py`)

Modelled on LouisXIV's FPGA Scope (the `HHMI - AI buffer.vi` front
panel): black graph with the dotted grid, Volts against Time (s), a legend
of per-channel checkboxes with colour swatches (LouisXIV's default
`Active Channels` pre-selected), "# of seconds to buff", "Clear",
"# points acq", plus a status line with the DIO4 period/jitter from the
FPGA's clock and the stream health. Every column is drawn in volts
(counts × 10/32768), so the digital flags show as 1.25 V steps exactly as
in LabVIEW. Long windows are decimated per pixel column with a min/max
envelope, so a 100 µs pulse stays visible in a 5 s window.

The scope starts when the FPGA connects and stops when it disconnects;
"Scope streaming" pauses it. Verified headlessly on the FPGA alone with a
10 Hz train (`spikes/27_gui_waveforms_scope_headless.py`); the rendered tab
is `docs/waveforms_tab_fpga_scope.png`. Pixel-matching this tab against the
live LabVIEW panel (the standard in `CLAUDE.md`) has NOT been done — the
reference VI was not running; it is modelled on the exported panel image.

## Not done yet

- Real waveform content on the AO columns (8–13) — they will show the
  galvo/piezo commands once the `Wvfrm2` words carry something. Note the
  AI loop samples `AO DMA` AFTER the range check, so with the "simulate on
  FPGA" clamp at 0 the columns read 0 whatever the words say; verifying
  waveform content on the scope needs a small non-zero clamp.
- Analog inputs: the eight AI channels are wired in the pinout but have
  no friendly names in the LabVIEW source; volts = counts × 10 / 32768.
