# FPGA I/O map (PCIe-7852R breakout box)

Extracted directly from `UNMScope_Source\FPGA code\Reto FPGA Project.lvproj`'s
FPGA-target I/O binding table (not inferred from names) — see that file's
`Item Name="SPIM FPGA 0"` target property block if you need to re-derive
this. Confirmed against `UNMScope_Source\bin\data\SPIMFPGAProject_SPIM_MAIN_VI.lvbitx`'s
own register list (self-documenting XML — every register has a `<Name>`,
`<Datatype>`, `<Offset>`, `<SizeInBits>`).

## Physical pinout (Connector0)

| Analog Out | Signal | | Digital I/O | Signal |
|---|---|---|---|---|
| AO0 | Z Galvo | | DIO0 | Cam Ready In |
| AO1 | X Galvo | | DIO1 | Arm Input |
| AO2 | Z Piezo | | DIO2 | Shutter |
| AO3 | AOTF ch 3 | | DIO3 | Cam Ready In 2 |
| AO4 | Dither Galvo | | **DIO4** | **Cam Ext Trigger Out DO** |
| AO5 | AOTF ch 0 | | DIO5 | Perfusion |
| AO6 | AOTF ch 1 | | DIO6/7 | ChannelShutter0/1 |
| AO7 | AOTF ch 2 | | DIO8 | Fire Output |
| | | | DIO9/10/11 | AOTF ch 4/5/6 |
| | | | DIO12/14 | ChannelShutter2/3 |
| | | | DIO13 | Running |
| | | | DIO15 | spare |

AI0-AI7 exist (analog inputs) but have no distinct friendly names in the
source — generic monitoring/feedback channels.

**DIO4 = camera external hardware trigger output.** Verified with an
oscilloscope in the hardware-validation spike (see `../spikes/`) before
being relied on for real acquisition.

## FPGA bitfile / session details

- Bitfile: `UNMScope_Source\bin\data\SPIMFPGAProject_SPIM_MAIN_VI.lvbitx`
- NI-RIO resource name: `RIO0` (per the `.lvproj`; confirm in NI MAX —
  resource names depend on what's currently connected/enumerated)
- Target: PCIe-7852R (Virtex-5)
- Top-level FPGA VI: `HHMI - SPIM FPGA Main VI.vi`

## Registers relevant to this spike (exact names from the bitfile)

| Register | Type | Notes |
|---|---|---|
| `SW Version` | U32, indicator | comms sanity check |
| `AO Mode` | EnumU16, control | `nifpga` reads/writes this as a plain int, not an enum string. From the bitfile's own `StringList`: 0=`"Start/Run Wvfrm"`, 1=`"Stop Wvfrm"`, 2=`"Set AO"`, 3=`"Clear AO DMA"` |
| `Static AO to set` | Cluster, control | **verified against the actual deployed bitfile** (differs from the source block diagram, which showed AOTF0-3 as separate fields): `X Galvo, Z Galvo, Z Piezo, Dither Galvo, Tiling, Filter, AOTF on?` (the AOTF field is a single bool, not per-channel) |
| `Set F.P. (T)` | Boolean, control | apply the values above (note the space before "(T)") |
| `# of triggers` | U32, control | trigger count for a run |
| `Continuous Mode` | Boolean, control | |
| `Free run` | Boolean, control | |
| `Trigger up (ticks)` | U32, control | trigger pulse width, in FPGA clock ticks |
| `Cam Trigger delay (ticks)` | U32, control | delay before camera trigger fires |
| `Trigger Enable?` | Boolean, control | arms triggering — write last |
| `Stop` | Boolean, control | |

DMA FIFOs (not needed for this spike, noted for later): `Wvfrm2` (64-bit,
waveform stream to FPGA for full scans), `Get Exp` (1-bit), `AI data`
(16-bit signed, analog input readback).

## Camera (Hamamatsu Orca Flash 4.0) trigger configuration

Confirmed from `DCAM - Set parameters.vi` / `DCAM - Set Trigger.vi` block
diagrams, for normal light-sheet external triggering:

- Trigger Source = `EXTERNAL`
- Trigger Mode = `NORMAL`
- 1st trigger = `START EXPOSURE`
- Trigger Active = `EDGE` (`DCAM_IDPROP_TRIGGERACTIVE` = 0x100120, value 1 = EDGE)
- Trigger Polarity = `POSITIVE` (rising edge on DIO4 starts exposure)

**Ordering**: camera trigger-mode setup and buffer allocation ("Prep Acq
Complete") happen *before* the FPGA is armed (`Trigger Enable? = True`).
Follow the same order here.

## Camera driver

Using `pymmcore-plus` against the Hamamatsu DCAM device adapter already
installed with Micro-Manager on this machine
(`C:\Program Files\Micro-Manager-2.0\mmgr_dal_HamamatsuHam.dll`), rather
than raw ctypes DCAM SDK bindings — reuses a mature, already-installed
driver. No camera-specific `.cfg` existed yet (only the MM demo config) as
of 2026-08-28; one will be built as part of Stage D.

## Firing a real trigger pulse -- the corrected, verified sequence

**Confirmed working 2026-08-28** (real pulse seen on the oscilloscope,
matching real LabVIEW's own pulse): a bare `Trigger Enable?` toggle is NOT
enough. The full sequence, matching `HHMI - Set all FPGA devices with
waveform config information.vi` + `HHMI - Start FPGA device waveform.vi`:

1. Write the FULL register bundle together (a subset silently produces no
   pulse -- no error, just nothing on DIO4):
   `Cam Trigger delay (ticks)`, `# of triggers`, `Continuous Mode`,
   `Cycle(Ticks)` (no space before "("), `Trigger up (ticks)`,
   `AO Trigger delay (ticks)`, `Free run`, `AI # of channels`,
   `AI loop period (ticks)`, `AO # of points per trigger`,
   `AO ticks between points`, `Shutter Ticks "on" (Ticks)`, then
   `Set F.P. (T) = True` to apply.
2. **Critically**, also write these three clusters -- each shaped
   `{'# on': int, '# off': int}`, NOT flat registers (the diagram labels
   them "Chnl/Stack/TP Trigger #s" but the real registers are named
   differently): `Trigger #s`, `Trigger stack #s`, `Trigger blast #s`.
   All default to `{'# on': 0, '# off': 0}` -- **zero "on" count is why
   nothing fired** in earlier attempts. Set `# on: 1, # off: 0` for a
   single-trigger test.
3. Push data into the `Wvfrm2` DMA FIFO (start it, write at least a few
   dozen words -- all-zero is safe regardless of exact channel
   count/order, since it just means zero volts on every channel no matter
   how it's unpacked on the FPGA side).
4. `AO Mode = 0` ("Start/Run Wvfrm"), `Set F.P. (T) = True`.
5. Poll `AO wvfrm ready` until `True` (up to ~3s, matching LabVIEW's own wait).
6. `Trigger Enable? = True` -- **keep the host-side hold SHORT** (~10ms).
   Holding it across a `Cycle(Ticks)` boundary (~100ms in testing)
   produced a SECOND spurious pulse -- the hardware appears to keep
   re-firing on each internal cycle for as long as `Trigger Enable?` stays
   asserted, rather than firing exactly once regardless of hold time. This
   was diagnosed by direct comparison against real LabVIEW + oscilloscope.
7. `Trigger Enable? = False`.

Working script: `../spikes/03b_fpga_trigger_with_waveform.py`. The
earlier, simpler `03_fpga_trigger_test.py` (bare `Trigger Enable?` toggle,
no waveform engine) reliably produces NO pulse -- kept around as a
documented negative example, not a working method.

## Multi-trigger / continuous mode: the real architecture (found in source, 2026-08-29)

We initially tried to brute-force multi-trigger bursts by trial and error
against real hardware (see git history for `spikes/07_multi_trigger_burst.py`)
and hit a real, reproducible failure: any hold of `Trigger Enable?` longer
than one `Cycle(Ticks)` period underflowed the `Wvfrm2` FIFO (confirmed via
`AO DMA Error` = `Buffer Underflow: True`, `AO wvfrm ready` dropping to
`False` and getting stuck there for the rest of the session -- recoverable
only with `session.reset()` + `session.run()`, matching LabVIEW's own
Reset/Run FPGA init sequence). This happened **regardless of buffer size**
(96 words or 20,000 words), which was the tell that a one-time write was
never going to work.

**The actual answer, found in `FPGA code\Host to FPGA\DMA\AO\`**:
- `HHMI - AO Host generation loop.vi`: a persistent background loop
  ("Loop till error or Stop is pressed") that runs for the ENTIRE scan,
  checking "Next block ready to write?" and incrementally writing one
  block at a time to the AO DMA channel -- not a single upfront write.
- `HHMI - Duplicate AO array to fill empty spaces in DMA buffer.vi`: when
  there's no new real per-stack waveform data ready yet, it just
  duplicates the last computed block to keep the buffer topped up.
- `Setup AO DMA buffer.vi`: buffer is sized in **blocks** (`Points/block`
  = `AO rate (Hz)` x a chosen `Sec to transmit block` duration), with a
  host-side "# of blocks to buffer" look-ahead depth, and the FPGA-side
  `Wvfrm2.Configure` sets the actual hardware FIFO depth (matches the
  16389-element depth found in the `.lvproj`'s FIFO definition).
- `HHMI - Clear AO DMA Host and FPGA buffers.vi` is the proper recovery
  call for a stuck/underflowed state (equivalent to what we found
  `AO Mode = 3` "Clear AO DMA" partially does, plus `session.reset()`).

**Implication for the Python side**: any acquisition longer than one quick
pulse (Z-stack burst, Continuous mode) needs an analogous periodic refill
loop feeding the `Wvfrm2` FIFO for the duration of the run, not a single
`fifo.write()` before firing. This is being implemented in
`src/unmscope/hardware/fpga_trigger.py`.

### Observed pulse counts across every attempt (oscilloscope, DIO4)

Even WITH the background refill thread + `fifo.configure(16000)` + a
3-block pre-seed, the observed pulse count stays pinned at **1 (sometimes
2)** and is completely unresponsive to:
- `# of triggers` (tried 1 and 5)
- `Trigger #s` / `Trigger stack #s` / `Trigger blast #s` `# on` (tried 1 and 5)
- how long `Trigger Enable?` is held (tried 10 ms and 700 ms)

**Conclusion: none of those registers is the pulse-count control.** Do NOT
keep tuning `Trigger #s`/`# of triggers` by trial and error against real
hardware -- that was a dead end. (The multi-agent background investigation
that was supposed to settle this hit an account session limit and returned
nothing at all -- reading the source directly, by hand, is what actually
found the answer below.)

## The real mechanism (found by reading `FPGA code\FPGA VIs\Triggering\`, 2026-08-29)

Read directly (not inferred): `HHMI - Trigger Manager.vi`, `HHMI -
Generate External Camera Trigger.vi`, `HHMI - Internal Cycle Sync
Trigger.vi`, `HHMI - Trigger count.vi`, `HHMI - Camera Trigger Module.vi`.

- **DIO4 pulse width is a hardcoded ~100us** (a fixed `4000`-tick constant
  inside `Generate External Camera Trigger.vi`, comment: *"set trigger
  high for 100us"*). It is NOT controlled by `Trigger up (ticks)` at all --
  our whole earlier mental model of that register was wrong.
- **`Cycle(Ticks)` drives a periodic internal sync trigger** (`Int Sync
  (Cycle Only)`, generated by `Internal Cycle Sync Trigger.vi`, which takes
  `Cycle(Ticks)` directly) -- this is the real repeat-rate control, one
  trigger event per `Cycle(Ticks)` period. Our original instinct that
  `Cycle(Ticks)` sets the repeat rate was actually correct.
- **`Trigger up (ticks)` is a holdoff/deadtime**, not a pulse width and not
  the cycle period -- `Trigger Manager.vi`'s own top-of-diagram comment:
  *"Once triggered, don't look for next trigger until Trigger up (ticks)
  have elapsed."*
- **THE BUG**: every one of our earlier attempts set `Cycle(Ticks)` and
  `Trigger up (ticks)` to the SAME value (4,000,000). That puts the
  holdoff's expiry and the next periodic sync trigger in a race against
  each other on every single cycle -- whether a given `Int Sync` event
  survives (gets accepted) or gets silently swallowed (arrives just before
  the holdoff clears) depends on FPGA-loop-iteration-scale timing jitter.
  This fully explains the erratic, unpredictable 1-2-pulses-per-attempt
  pattern seen across every prior test, including the original "single
  pulse" test (which "worked" only because a single pulse never needs a
  second cycle to survive the race).
- **`HHMI - Camera Trigger Module.vi`** (a `Wait for Cam Ready?` gate on
  DIO0 "Cam Ready In") has NO callers anywhere in the FPGA source tree --
  it's dead/unused code. Not a real lead; don't chase a DIO0 handshake.
- **Fix**: make the holdoff meaningfully shorter than the cycle period,
  e.g. `Cycle(Ticks) = 4,000,000` (100ms) with `Trigger up (ticks) =
  400,000` (10ms) -- comfortable margin, no race. Implemented in
  `src/unmscope/hardware/fpga_trigger.py` as separate `CYCLE_TICKS` /
  `HOLDOFF_TICKS` constants (previously both were the same
  `TRIGGER_UP_TICKS` value -- that conflation was the bug).
- Still open: exactly where "# of images desired" gets compared to stop a
  bounded burst (`Trigger Manager.vi`'s own comment references this but
  the comparison isn't visible in that one diagram) -- not yet needed for
  Continuous mode testing, revisit for bounded Z-stack bursts.

## Oscilloscope discrepancy, resolved (2026-08-29)

After the native-burst investigation, several single-trigger and static-
voltage re-tests showed NOTHING on the oscilloscope, despite internal FPGA
diagnostics (`# AO generated` incrementing correctly, same signature as
earlier confirmed-successful fires) strongly suggesting the hardware was
actually working. This was a real, multi-attempt scare -- worth recording
the resolution process:

1. Internal register diagnostics (`spikes/12_internal_register_scope.py`)
   showed the FPGA's own state transitioning exactly as expected on every
   fire, even when the external scope showed nothing -- this was the key
   signal that pointed at the *observation* (scope/probe) rather than the
   hardware or code.
2. Switched to the simplest possible external test: a SUSTAINED 1V DC step
   on X Galvo (AO1) instead of a ~100us pulse, removing all timing
   sensitivity (`spikes/13_static_voltage_scope_check.py`). This also
   initially showed nothing (three attempts) despite ground being
   confirmed connected and a labeled BNC panel -- pointing at wrong-BNC
   selection as the most likely cause.
3. Root cause: the user was very likely probing the wrong BNC on the
   panel (never fully confirmed which label was actually being probed).
   Once reseated/reselected, the static 1V step became visible and
   measured correctly (~1V, confirming the +/-10V <-> +/-32767 counts
   scale assumption).
4. Final combined confirmation (`spikes/14_combined_galvo_and_trigger_check.py`):
   with both AO1 (X Galvo) and DIO4 (Cam Ext Trigger) connected
   simultaneously, a run showing "X Galvo steady 1V -> drop to 0V as
   arming -> one Cam Trigger pulse" was confirmed visible end-to-end on
   both channels together.

**Lesson for next time a signal "disappears"**: check the internal FPGA
diagnostic registers FIRST (cheap, no hardware risk, decouples "is the
hardware working" from "is the observation tool working") before assuming
a real regression. A sustained DC-level test is a much more robust probe-
identification tool than a narrow pulse.

## Staged validation (see `../spikes/`)

1. `01_fpga_connect.py` — **done, 2026-08-28.** Opened a real nifpga
   session against `RIO0`, read `SW Version` = 3 (comms confirmed), found
   `AO Mode` was already `2` ("Set AO"), wrote/confirmed the all-zero safe
   state on the real `Static AO to set` cluster, closed cleanly. This is
   where the `AO Mode` int-enum and the real `Static AO to set` field names
   above were corrected against actual hardware. No camera involved.
2. `03b_fpga_trigger_with_waveform.py` — **done, 2026-08-28.** Real,
   clean, single ~3V pulse per second confirmed on the oscilloscope, using
   the corrected sequence above. See "Firing a real trigger pulse" above.
2. `02_static_ao_test.py` — hold a known voltage on X Galvo (AO1) for
   oscilloscope verification.
3. `03_fpga_trigger_test.py` — fire one trigger pulse, verify on DIO4 with
   the oscilloscope. No camera.
4. `04_camera_trigger_setup.py` — arm the Orca4 for external/edge trigger,
   no FPGA firing yet.
5. `05_roundtrip_test.py` — **done, 2026-08-28.** DIO4 physically wired to
   the Orca Flash 4.0's external trigger BNC. Camera armed EXTERNAL/EDGE/
   POSITIVE via `startSequenceAcquisition`, FPGA fired one pulse using the
   corrected sequence -- frame arrived 230ms after the trigger (matching
   the 100ms exposure + readout), a clean/consistent result (unlike the
   ~19.7s lag or outright failures seen with the old broken trigger
   sequence). **Confirmed end-to-end: the camera is triggered by the
   FPGA's real external trigger line, through our own Python code.**

Each stage requires explicit hardware confirmation before moving to the
next.
