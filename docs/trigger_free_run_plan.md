# Trigger timing: why we jitter, and the FPGA free-run path out

**Written 2026-09-03.** Source: multi-agent read of the LabVIEW FPGA VIs
and the exported diagrams in `H:\UNM_Lightsheet\VI_Diagrams\`, plus
host-side benchmarks measured on this machine. **No hardware was touched
to produce this document.** Everything here labelled MEASURED was measured
in software only; everything about FPGA behaviour is read from source and
is **not yet hardware-verified**.

Supersedes two claims in `fpga_io_map.md` — see "Corrections" at the end.

> **Update, later on 2026-09-03: the free run is now HARDWARE-VERIFIED
> and in production use by the GUI.** Jump to "Hardware verification,
> 2026-09-03" at the end for what actually happened, including two arm
> failures this plan did not predict and the camera-side findings.

---

## The short version

The user asked: *"Aren't these things already figured out in the LabVIEW
software?"* **Yes.** LouisXIV arms the FPGA **once per scan** and lets the
board's own 40 MHz counter time the entire pulse train. We re-arm **once
per pulse** from a Python thread, which throws away the hardware timebase
entirely and substitutes Windows thread scheduling as the trigger clock.

| Approach | Period jitter |
|---|---|
| LabVIEW: arm once, FPGA free-runs | ±1 tick = **±0.000025 ms** (INFERRED from source) |
| Ours, before 2026-09-03 fix | ~**15.6 ms** (MEASURED — coarse Windows tick via `Event.wait`) |
| Ours, after 2026-09-03 fix (`a051d95`) | ~**0.5–0.9 ms** P2P (MEASURED by an independent verifier) |

The commit `a051d95` was a real improvement, but it optimised the wrong
layer. Even a perfect host loop is ~0.4 ms; free-running is ~25 ns. That
is three to four orders of magnitude, and it is free — the hardware
already does it.

---

## How the FPGA actually generates the pulse train

Read from `UNMScope_Source\FPGA code\FPGA VIs\Triggering\`:

1. **`HHMI - Internal Trigger Sync.vi`** — a free-running modulo counter,
   one increment per 40 MHz tick:
   ```
   cond = (cnt < Cycle(Ticks)) AND Enable?
   next = cond ? cnt+1 : 0
   Sync out (Cycle Only) = cond AND (cnt < Trigger up (ticks))
   ```
   Period is exactly `Cycle(Ticks)+1` ticks, forever, **with no host
   involvement**.
2. **`HHMI - Internal Cycle Sync Trigger.vi`** runs that in its own
   parallel FPGA loop, publishing global `Int Sync (Cycle Only)`.
3. **`HHMI - Trigger Manager.vi`** edge-detects that global ANDed with
   `Triggers Enbld` → `Cam Trigger`.
4. **`HHMI - Generate External Camera Trigger.vi`** edge-detects
   `Cam Trigger` and holds DIO4 high for a hardcoded 4000-tick counter
   ("set trigger high for 100us" — which also confirms 25 ns/tick loops).
5. **`Triggers Enabled.vi`** is a case structure on `Continuous Mode`:
   False → `(# triggers read < # of triggers) AND Triggers Enbld`
   (bounded, self-disarming); True → unbounded free-run. **Both bounded
   bursts and continuous exist natively.**

### What LabVIEW does with it

- `HHMI - Start FPGA device waveform.vi` writes `Trigger Enable? = True`
  and **leaves it true** — there is no disarm in that VI.
- `Trigger Enable? = False` appears only in
  `HHMI - Stop all FPGA device waveform.vi`.
- `grep` for callers shows the start VI is called from exactly one place:
  `HHMI - SPIM Start scan.vi` — **once per scan, not once per frame**.

LabVIEW arms once and lets the FPGA free-run the whole train.

---

## Why our pulses jitter

`FpgaTriggerController.fire_single_trigger()` asserts `Trigger Enable?`
for `ENABLE_HOLD_S = 0.01 s`, then clears it — while `CYCLE_TICKS` is
4,000,000 (100 ms). **The FPGA's cycle counter never completes even one
period inside the armed window.**

Because the counter is gated by `Enable?` and sits at 0 when armed,
`Sync out` goes true immediately, so a rising edge is manufactured **at
the exact instant the host's `Trigger Enable? = True` write lands**. The
hardware timebase is completely bypassed and 100% of the period jitter is
host scheduling noise transported 1:1 onto DIO4.

An independent verifier confirmed the reset-on-enable design from the
diagrams and added a neat logical proof: *if* the counter free-ran on a
100 ms grid, a 10 ms enable window would catch an edge only ~10% of the
time — so the reliably-one-pulse-per-call behaviour we actually observe
is only consistent with reset-on-enable.

---

## Correction: the "10 ms readiness poll" theory was WRONG

Earlier in this session I claimed the `AO wvfrm ready` poll's
`time.sleep(0.01)` was a live secondary jitter source, quantizing the
fire-to-pulse delay into 0-or-10 ms buckets. **That is refuted from FPGA
source.**

`HHMI - Execute AO Waveform state.vi` has a flat sequence:
frame 1 "Wait for loop to start" `Count(Ticks) = 5`; frame 2 "Signal loop
is ready" → `AO wvfrm ready`. **5 ticks @ 40 MHz = 125 ns.** The Wvfrm2
FIFO is already seeded *before* `AO Mode = 0`, so there is nothing to wait
for. Meanwhile the host still has to issue `Set F.P. (T)` (another PCIe
round trip) plus Python overhead before its first read — by which point
the FPGA has been ready for ~10 µs.

So the poll exits on its first read essentially always (`polls == 1`) and
that sleep never executes. Contribution: **~0 ms**, not 10 ms.

`spikes/16_trigger_jitter_measure.py` still reports `polls` per pulse, so
this prediction is directly falsifiable on hardware — if `polls` is
consistently 1, this correction is confirmed.

---

## The free-run implementation

### Phase 1 — configure (all writes, then ONE `Set F.P. (T) = True`)

```
Cycle(Ticks)              = round(period_s * 40e6)                    # THE period control
Trigger up (ticks)        = round((period_s - exposure_s)/2 * 40e6)   # LabVIEW's own formula
                                                                       # 0 < value < Cycle(Ticks)
Cam Trigger delay (ticks) = 0     # LabVIEW uses ~165 us 'sync rdout offset' for Orca
AO Trigger delay (ticks)  = 0
Continuous Mode           = True  # unbounded free-run
# of triggers             = N     # only consulted when Continuous Mode is False
Trigger #s / stack #s / blast #s = {'# on': 1, '# off': 0}
Perfusion stack #s        = {'# on': 0, '# off': 0}

# NEVER WRITTEN BY OUR CODE TODAY — see "Bitfile mismatch" below
Trigger skips             = 0
Trigger Look for Arm?     = False
Trigger Check Filter?     = False
Added time (Ticks)        = 0
Frame index to add time to = 0

AI # of channels = 0; AI loop period (ticks) = 4000; Free run = False   # AI flag, see Corrections
AO # of points per trigger = 1; AO ticks between points = 4000
Shutter Ticks "on" (Ticks) = 0
Set F.P. (T) = True
```

### Phase 2 — AO engine, started ONCE

Seed `Wvfrm2`, `AO Mode = 0`, poll `AO wvfrm ready`, then **start one
persistent background refill thread for the whole run** (see landmine 1).
Refill rate needed is tiny: with `AO # of points per trigger = 1` the
engine consumes **one I64 word per pulse**, i.e. ~10 words/s at 10 Hz.

### Phase 3 — arm ONCE

```
Trigger Enable? = True; Set F.P. (T) = True     # then DO NOT TOUCH IT
```

### Phase 4 — monitor read-only, never in the pulse path

`# of triggers read`, `# of triggers ignored`, `Int Cycle Trigger`,
`AO DMA Error`, `AO wvfrm ready`, `# AO generated`.

### Phase 5 — stop ONCE

`Trigger Enable? = False`; `AO Mode = 1`; `Set F.P. (T) = True`;
`fifo.stop()`; `safe_state()`.

---

## Why the 2026-08-29 "native bursts are unreliable" verdict was premature

All of it is explainable without any hardware fault:

1. **The race was active during every one of those tests.** They set
   `Cycle(Ticks) == Trigger up (ticks) == 4,000,000`. From the Internal
   Trigger Sync equation that makes `Sync out` LOW for exactly **one
   25 ns tick per 100 ms cycle** — far too narrow for Trigger Manager's
   edge detector. That alone explains "pinned at 1 pulse, sometimes 2".
   The `CYCLE_TICKS`/`HOLDOFF_TICKS` split that fixed this was applied
   **only inside the single-pulse path** and never retried against a
   sustained hold.
2. **`# of triggers read` reading 0 was a measurement artifact.**
   `HHMI - Trigger count.vi` resets the counter whenever `Triggers Enbld`
   is False — and `fire_single_trigger()` drops `Trigger Enable?` 10 ms
   after arming. The counter is wiped before the host ever reads it. It
   reads 0 regardless of whether triggering worked. **Read it while
   armed.**
3. **`Continuous Mode` was never set True** in those tests, and
   `# of triggers` was set to 1 — under which the FPGA correctly
   self-disarms after exactly one trigger *by design*, reproducing
   "pinned at 1" with no fault required.

---

## Two landmines before switching free-run on

### 1. The AO DMA refill loop is a hard prerequisite, not an optimisation

Holding `Trigger Enable?` with only the current one-shot 48-word `Wvfrm2`
seed **will** reproduce the documented underflow: `AO DMA Error`,
`AO wvfrm ready` stuck False for the rest of the session, recoverable only
via `session.reset()` + `run()`. This was hit on real hardware before,
*regardless of buffer size* (96 to 20,000 words tried) — because nothing
was refilling at all, not because the buffer was too small.

LabVIEW's equivalent is `HHMI - AO Host generation loop.vi` +
`HHMI - Duplicate AO array to fill empty spaces in DMA buffer.vi`, a
persistent background top-up. **Build that first.** Its own scheduling
slop is harmless — it only has to stay ahead of consumption, not hit a
deadline — so it does not reintroduce host jitter into the trigger period.

### 2. The deployed bitfile is a DIFFERENT BUILD from the checked-out source

Register-XML diff of the two `.lvbitx` files:

| | Signature | Has |
|---|---|---|
| `bin\data\` (**what we load**) | `87448130D38110A595B587A752E3D560` | `Added time (Ticks)`, `Frame index to add time to`, `Int Cycle+Added Trigger`, `Trigger skips`, `Trigger Look for Arm?` |
| `FPGA code\FPGA Bitfiles\` | `7CF5BAD623920FC72B530AE4441842BC` | `Optogenetic Mode Options`, `Projection Mode`, `X Galvo Projection Mode Gain`, `Chnl/Stack/TP Trigger #s` |

`HHMI - SPIM FPGA Main VI.vi`'s diagram writes `Optogenetic Mode Options`
/ `Projection Mode` / `X Galvo Projection Mode Gain` — which exist **only
in the build we do NOT load**. And `grep -rl` across the whole source tree
returns **zero** hits for `Added time`, `Int Cycle+Added`,
`Trigger Look for Arm`, `Trigger skips`, or `Frame index`.

**Consequence: every exported diagram in `VI_Diagrams\` describes a
different build than the one actually running.** Treat FPGA-source
conclusions as strong hypotheses, not ground truth.

Two registers matter urgently:

- **`Added time (Ticks)`** (offset 464) + **`Frame index to add time to`**
  (470) — a designed-in **per-frame period modulation**. An FPGA that adds
  N ticks to the cycle for one specific frame index is, by construction, a
  mechanism that makes spacing change on some cycles and not others. A
  stale 40,000 would stretch one cycle per stack by 1 ms; 400,000 by
  10 ms. Not the current cause (we bypass the counter), but **suspect #1
  the moment free-run is switched on.**
- **`Trigger Look for Arm?`** (offset 506) — if its compile-time default
  is True, every trigger is gated on the DIO1 "Arm Input" pin, which is
  probably not wired.

**Mandatory first step:** immediately after `session.reset()` + `run()`
and *before writing anything*, read and record all five registers so the
compile-time defaults are documented once and for all. Then write known-safe
values explicitly rather than trusting defaults.

Zero-risk read-only diagnostic: with the FPGA armed and free-running, poll
`Int Cycle Trigger` (526) and `Int Cycle+Added Trigger` (530). **If the two
ever disagree, the added-time path is live.**

---

## Corrections to `fpga_io_map.md`

Both established from the VI diagrams; both currently wrong in that file:

1. **`Free run` drives the global `AI Free run`** — it is an *analog
   input* flag and has **nothing to do with trigger repetition**.
2. **`Trigger up (ticks)` is the Int-Sync pulse HIGH time, not a
   holdoff/deadtime.** `HHMI - Generate trigger settings for FPGA.vi`
   sets it to `(cycle - exposure)/2`.

---

## Verification status as originally written (superseded by the section after it)

- **MEASURED (software only):** all host-side timing numbers.
- **READ FROM SOURCE, not hardware-verified:** every claim about FPGA
  behaviour, and it is read from diagrams of a **different build** than
  the deployed bitfile (see landmine 2).
- **Adversarial verification was incomplete.** The workflow that produced
  this was cut short by session limits twice; the FPGA findings' verifiers
  largely did not run. Those that did returned "refuted" — but on
  inspection those were *scoping* refutations ("this is not a cause of the
  jitter happening right now", which is true and not disputed), **not**
  refutations of the free-run recommendation itself. One verifier
  independently *confirmed* the reset-on-enable mechanism.
- **Nothing here has been tested on the microscope.**

---

## Hardware verification, 2026-09-03 (later session) — IT WORKS

Everything below was measured on the microscope with `spikes/17`–`23` and
the GUI itself (`spikes/20_gui_free_run_headless.py`). Production code is
`FpgaTriggerController.start_free_run()` / `stop_free_run()`, and the GUI
now uses it for both Continuous Scan and Z stack.

### Prerequisite 1 — compile-time defaults (recorded)

`spikes/17_read_reset_defaults.py` → `docs/fpga_reset_defaults.json`. All
five suspects are benign: `Added time (Ticks)=0`, `Frame index to add
time to=0`, `Trigger skips=0`, `Trigger Look for Arm?=False`, `Trigger
Check Filter?=False`. Also learned: `AO DMA Timeout (ticks per read)=40`
(LabVIEW writes 100), `AO Limit Max/Min=±32767`, `AO Mode=2` (Set AO),
`AI loop period=100`, and every `... PB` register is 0. `Int Cycle
Trigger` vs `Int Cycle+Added Trigger` never disagreed across ~10,000
samples while armed: the added-time path is not live.

### What actually broke the arm, in order of discovery

| Symptom | Cause (bisected on hardware) | Fix |
|---|---|---|
| `AO wvfrm ready` never True | `Clear AO DMA` (AO Mode=3) before the FIFO: engine sits in `AO Purging=1` forever, refill thread or not (`spikes/19b_arm_variants.py`, V5/V8/V9) | don't send it; `reset()+run()` at connect is the recovery path |
| `AO DMA Error = Buffer Underflow` at t=0, `AO # points left = 65535` | `Trigger blast #s={1,0}` flags every trigger as a blast trigger → the AO engine uses `AO # of points per trigger PB` / `AO ticks between points PB`, both 0 → first point is **Late** → reported as "Buffer Underflow" (`HHMI - AO Check if Error or done.vi`: Late? → Buffer Underflow) | mirror the normal AO timing into the PB registers (`write_pb_registers=True`); `blast={0,0}` without PB also works. The DMA timeout (40 vs 100) was a red herring — tested both |

The 2026-08-29 "native bursts are unreliable" verdict is therefore fully
explained: the Cycle==Trigger-up race plus the PB-register defaults, with
the Late flag misread as a FIFO underflow. No hardware fault.

### Results

| Test | Result |
|---|---|
| continuous, 100 ms exposure, 109.7 ms period, 10 s, FPGA only | 92 triggers = expected; period bracket from the counter 108.7–109.9 ms; 0 ignored; AO points == triggers; no faults |
| bounded `n_triggers=20` | exactly 20, FPGA self-stops (`Triggers Enabled.vi`), counter holds until disarm |
| refill thread | keeps up at 1 and 100 AO points/trigger (≈900 words/s); host buffer full most of the time (timeouts are the normal state) |
| GUI Z stack ×2, Continuous 5 s (real Orca + FPGA, headless, 3 runs) | 10/10 frames, 38–39 triggers → same number of frames, 10/10 frames; auto-return to IDLE |

First-trigger latency after the enable write is between ~2.5 and ~19 ms
(fencepost analysis of run boundaries) — the counter reset by the enable
means the first edge is immediate but not instantaneous.

### Camera-side findings (these cost more time than the FPGA)

1. **Orca readout is 33.3 ms, not 9.7 ms.** The camera's own
   `ReadoutTime` property (DCAM `ScanMode=1`, full frame) says 0.0333 s.
   With the old 9.7 ms assumption every second trigger was dropped
   (47 frames from 92 pulses; intervals ≈ 2 periods). `Camera.readout_ms()`
   now queries the camera; period = exposure + readout + 0.5 ms margin.
   At exactly exposure + readout (margin 0) the camera still alternated;
   +0.5 ms was enough. `SPIMProject.ini` has `Sync Readout = TRUE` for this
   camera: LouisXIV uses DCAM's SYNCREADOUT trigger mode, which allows a
   tighter cycle than EDGE. Not ported yet — follow-up.
2. **One stale frame after the sequence starts in EXTERNAL mode** when
   the FPGA is connected (reset/run) while the camera is already armed —
   lands ~10 ms after arm, impossible for a real exposure. GUI flushes the
   buffer before arming and additionally discards anything arriving within
   half a period of the arm.
3. **Exposure lost after `stopSequenceAcquisition`** (intermittent): real
   exposure → 0 → 3.021 ms, adapter property still says 100, every later
   set is ignored; only a device re-initialisation repairs it (~0.5 s).
   Changing exposure while the sequence RUNS works. So the GUI starts the
   camera sequence once and leaves it running until Disconnect, and
   `OrcaFlash4Camera.repair_exposure_if_lost()` is the safety net.
4. A Stop now keeps polling for two periods so in-flight frames are
   counted (39/39 above instead of 141/153 before).

### Still open

- SYNCREADOUT trigger mode (what LouisXIV uses) — would make the period
  ≈ exposure instead of exposure + 33 ms.
- An access violation in the Qt event loop right after Disconnect in one
  headless run (continuous sequence stopped and device unloaded back to
  back). `disconnect()` now stops + settles first; two further runs were
  clean. See `known_issues.md`.
- Real waveform content in the `Wvfrm2` words (still all zeros); the I64
  packing is 4×I16 per word on the FPGA side (`Split I64 into 4xI16.vi`),
  ~1 word per AO point in the deployed build by the consumption numbers.
