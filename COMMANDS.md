# Commands cheat-sheet

Everything you need to run, compare, and check this project. Run all of
these from the repo root (`H:\UNM_Lightsheet\UNMScope_Python`).

---

## Run the apps

**The Python GUI**

```bash
python -m unmscope.gui
```

Window title: `UNMScope -- LouisXIV (Python)`.
Nothing touches hardware until you click **Connect** (Camera / FPGA).
Closing the window runs the clean shutdown path (stops acquisition,
disconnects camera + FPGA), so close it rather than killing it.

**The real LabVIEW app** (for comparison, and still the daily driver)

```bash
"H:\UNM_Lightsheet\UNMScope_Source\bin\LouisXIV.exe"
```

The front-panel window is titled `SPIM MAIN V4.107.100`. Note the
process also has a hidden dummy window -- tools must match on the title,
not on the process's "main window".

Don't Connect in both apps at once: they'd fight over the same camera
and FPGA.

---

## Check the GUI against the LabVIEW original

This is the important one. It screenshots both windows, measures the
image regions, and prints a reference-vs-ours table with deltas.

```bash
python tools/compare_to_labview.py
```

Both apps must be running **and on matching tabs** (both on Images, or
both on Stack Projections) -- it can only compare what's on screen.

Useful flags:

```bash
python tools/compare_to_labview.py --tolerance 4          # stricter
python tools/compare_to_labview.py --save out/            # keep both PNGs
python tools/compare_to_labview.py --fill f0f0f0          # region fill colour
```

Exits 0 on PASS, 1 on FAIL, 2 if it couldn't find a window. If it says
it can't bring a window to the front, click that window once and re-run
-- it refuses to guess, because the two windows overlap and a bad
capture silently compares one app against itself.

---

## Diagnose a layout problem

**What is forcing the window to be too big?** Builds the window
headlessly and prints every widget's size hints, so you can see which
one is demanding the space:

```bash
python -c "import sys; sys.path.insert(0,'src'); from PySide6.QtWidgets import QApplication; from unmscope.gui.main_window import MainWindow; app=QApplication([]); w=MainWindow(); print(w.minimumSizeHint())"
```

**Does it still import / parse after an edit?**

```bash
python -c "from unmscope.gui import main_window; print('ok')"
```

**Tests**

```bash
python -m pytest
```

---

## Install

```bash
pip install -e ".[dev]"
```

---

## Where things are

| What | Where |
|---|---|
| The GUI | `src/unmscope/gui/main_window.py` |
| Camera / FPGA drivers | `src/unmscope/hardware/` |
| Hardware spikes (one-off scripts) | `spikes/` |
| Live FPGA panel | `tools/fpga_live_panel.py` |
| LabVIEW-comparison tool | `tools/compare_to_labview.py` |
| FPGA pinout + register map | `docs/fpga_io_map.md` |
| Plan / status | `ROADMAP.md` |
| Rules for working on this repo | `CLAUDE.md` |
| Exported LabVIEW VI diagrams | `H:\UNM_Lightsheet\VI_Diagrams\index.html` |

---

## Reference measurements (the real front panel)

Measured off the live `SPIM MAIN V4.107.100` window. Use these as the
target; re-measure rather than guessing if you need something not listed.

| Element | Size / position |
|---|---|
| Window | 1381 x 931 |
| Left panel (Scan Setup etc.) | 406 wide |
| Right panel | 975 wide |
| Camera canvas | 497 x 441, starts 5px below the tab border |
| Reserved right of canvas | 16px (vertical scrollbar) |
| Reserved below canvas | ~55px (scrollbar + progress bar) |
| Images-tab left tool strip | 80 wide |
| Max Counts panel | 123 wide |
| Display-options panel | 214 wide, ~14px gap from the Max Counts panel |
| MIP boxes (XY/YZ/XZ) | 245 x 196 each |
| MIP label + save button | in a gutter to the LEFT of each box |
| Empty image/MIP fill colour | `#f0f0f0` (NOT black) |

---

## Hardware spikes added 2026-09-03 (need the real camera / FPGA)

```bash
python -u spikes/17_read_reset_defaults.py
```
Read-only: every FPGA register right after reset+run, recorded to
`docs/fpga_reset_defaults.json`.

```bash
python -u spikes/19_free_run_trigger.py --seconds 10 --camera
```
The main check: FPGA free run with the Orca counting frames. Prints
`VERDICT: PASS` only if the FPGA's own trigger counter matches the
expectation and the camera delivered a frame per trigger. `--count 20`
runs a bounded burst instead; `--exposure 0.02` a faster train.

```bash
python -u spikes/20_gui_free_run_headless.py
```
The real GUI, headless, on real hardware: Z stack, Continuous, Z stack.

```bash
python -u spikes/19b_arm_variants.py
```
Bisects an arm failure (`AO wvfrm ready` never True) one change at a time.

```bash
python -u spikes/18_probe_dcam_isolated.py
```
Camera open + snap, step by step, in its own process (crash isolation).
