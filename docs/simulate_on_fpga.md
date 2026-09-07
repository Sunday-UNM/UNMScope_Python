# "Simulate on FPGA" mode

**Verified 2026-09-03, FPGA only** — no camera, no galvo, nothing else
connected. `spikes/26_gui_simulate_on_fpga_headless.py` (both trigger
modes) drives the real GUI code path.

## What it is

The GUI's **Simulated** camera backend + the **real** FPGA. The FPGA
free-runs the trigger train exactly as in production; the simulated
camera produces one synthetic frame per real FPGA edge, fed from the
controller's `# of triggers read` callback (`SimulatedCamera.external_trigger()`),
with the same EDGE / SYNCREADOUT accounting the Orca showed (spikes/24).
So Z stacks, Continuous Scan, the stop grace period, the warm-up discard —
the whole acquisition flow — run on real hardware timing without a camera.

The mode is implicit: backend = Simulated **and** FPGA connected. The
status pill reads `ACQUIRING (SIM on FPGA)` and the log says so.

## Nothing may move: the AO clamp

Every AO output is clamped by the FPGA's own limit registers,
`AO Limit Max (counts)` / `AO Limit Min (counts)` (clusters over X Galvo,
Z Galvo, Z Piezo, Dither Galvo, Tiling, Filter + `AOTF on?`). The FPGA
range-checks every DMA point and every static value against them
(`Output DMA.vi` / `Range Check A0 Values.vi`), so 0/0 forces 0 counts on
every channel whatever the waveform words say.

**The AOTF is NOT part of this clamp** (changed 2026-09-07, user's
instruction, laser module confirmed off). `AOTF on?` used to be forced
False here as well; it is now passed `True` by the GUI so the laser
modulation is visible on the live trace like every other channel. The AO
clamp freezes the galvos and the piezo -- that is all it was ever for.
See docs/aotf.md.

- Written at FPGA **connect** when the camera is simulated
  (`FpgaTriggerController.set_ao_clamp(True)`) and again at **every arm**
  (`start_free_run(clamp_ao=True)`), read back and verified each time.
- If the readback does not show the clamp, the arm is **refused**.
- A real-camera arm writes full scale (±32767) back, so a simulated run
  can never leave a real one clamped.
- Compile-time default is full scale (`docs/fpga_reset_defaults.json`).

## Results (headless GUI, SimulatedCamera + real FPGA)

| mode | Z stack | Continuous 5 s | Z stack | AO limits 0 at connect / while armed |
|---|---|---|---|---|
| SYNCREADOUT | 10 frames / 11 triggers | 65 frames / 66 triggers (1 warm-up discarded) | 10 / 11 (1 warm-up discarded) | yes / yes |
| EDGE | 10 / 10 | 62 / 62 | 10 / 10 | yes / yes |

## Related

- `docs/fpga_scope.md` — the FPGA Scope is the observer for this mode:
  it shows the trigger train and (later) the clamped AO columns 8–13.
- `SimulatedCamera.reacts_to_dio4 = False` is what tells the GUI the
  camera cannot see real edges: no stale-frame logic, no open-exposure
  marking at FPGA connect, and the software-fed trigger path.
