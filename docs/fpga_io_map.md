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

## Staged validation (see `../spikes/`)

1. `01_fpga_connect.py` — **done, 2026-08-28.** Opened a real nifpga
   session against `RIO0`, read `SW Version` = 3 (comms confirmed), found
   `AO Mode` was already `2` ("Set AO"), wrote/confirmed the all-zero safe
   state on the real `Static AO to set` cluster, closed cleanly. This is
   where the `AO Mode` int-enum and the real `Static AO to set` field names
   above were corrected against actual hardware. No camera involved.
2. `02_static_ao_test.py` — hold a known voltage on X Galvo (AO1) for
   oscilloscope verification.
3. `03_fpga_trigger_test.py` — fire one trigger pulse, verify on DIO4 with
   the oscilloscope. No camera.
4. `04_camera_trigger_setup.py` — arm the Orca4 for external/edge trigger,
   no FPGA firing yet.
5. `05_roundtrip_test.py` — combine C+D: fire the FPGA trigger, confirm one
   frame arrives.

Each stage requires explicit hardware confirmation before moving to the
next.
