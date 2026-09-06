# Working agreements for this repo

## Visual fidelity to the LabVIEW original is a hard requirement

This project replaces LouisXIV (`SPIM MAIN.vi`). The user's standard is
that the Python GUI should be **pixel-comparable** to the real front
panel -- same window size, same widget sizes, same relative positions,
same colors. "Looks about right" is not the bar. Assume every layout
claim will be checked against the real thing.

### Never state a size, color, or arrangement from looking at a screenshot

Downscaled screenshots are actively misleading. Real errors this has
already caused in this repo:

- Called the Stack Projections MIP boxes "black". They are `#f0f0f0`.
- Built the Images tab's right-hand tools as one wide column with a
  large left margin. It is actually **two separate bordered panels**
  (~123px and ~214px) with a real gap between them.
- Measured a "29px gap between the MIP boxes" and assumed empty space.
  It is the next column's label + save-button **gutter**.

Every one of those survived because a plausible-looking read of a
thumbnail was trusted instead of measured.

### The procedure that actually works

1. **Capture the real panel** (LouisXIV.exe must be running). Its real
   front-panel window is NOT the one `Process.MainWindowHandle` returns
   -- LabVIEW-built exes expose a hidden dummy window. Enumerate top
   level windows (`EnumWindows` + `GetWindowThreadProcessId`) and pick
   the one titled like `SPIM MAIN V4.107.100`.
2. **Crop the region of interest and view it magnified** *before*
   writing any layout code. Do not reason about a region from the full
   1381x931 screenshot.
3. **Measure with pixel sampling, not eyes.** Use PIL: scan rows and
   columns for color transitions to find widget edges, and sample exact
   RGB for colors. Scan *several* lines through a region -- a single
   scan line cannot tell you a structure.
4. **Check what is inside every gap** before calling it empty.
5. **Build, screenshot the Python side, and measure it the same way.**
   Produce an explicit reference-vs-ours table of numbers. That table
   is the acceptance check, not a visual glance.
6. **Label every number as measured or assumed.** Never silently mix a
   guess (e.g. "trim the box 196 -> 180 to save space") in among real
   measurements -- if a value is a guess, say so, or go measure it.

### Screenshotting / clicking a native Win32 window

No computer-use tool covers native windows here (Browser tools are
web-only). Use PowerShell + `System.Drawing.Graphics.CopyFromScreen`
for screenshots and `user32.dll` `SetCursorPos` + `mouse_event` for
clicks. **The window loses foreground focus between separate tool
calls** (the user is often working in another window). Always
`ShowWindow(SW_RESTORE)` + `SetForegroundWindow` + re-fetch
`GetWindowRect` in the *same* call as the click and the screenshot --
a click against a stale cached rect will land on whatever is actually
in front, which has already happened here.

### Qt gotchas that silently break layout matching

- A word-wrapped `QLabel` does **not** shrink its `minimumSizeHint()`;
  it reports its full unwrapped single-line width as a minimum unless
  given `setMaximumWidth(...)`. One long placeholder subtitle forced
  the whole window ~270px wider.
- `QLabel` is itself a `QFrame` subclass, so an unscoped
  `QFrame { border: ... }` stylesheet draws a border around every label
  inside a panel. Scope panel styles with `setObjectName()` +
  `QFrame#name { ... }`.
- A tall column of group boxes can force the whole window taller than
  the target. Wrap it in a `QScrollArea` rather than shaving spacing
  pixel by pixel.
- Disabled `QPushButton`s grey out their text, which can make a glyph
  icon nearly invisible. Pin the color with a `:disabled` rule.
- To find what is forcing a size: build `MainWindow()` headlessly and
  walk the widget tree printing `.sizeHint()` / `.minimumSizeHint()`
  (recurse `QSplitter` via `.widget(i)`, `QTabWidget` via `.widget(i)`,
  else `.layout()`). This finds the culprit in minutes.

## Never read the same VI twice

`docs/vi_notes.md` is the index of every LouisXIV VI already read, with what
each one settles and where the full write-up lives. **Check it before opening
anything under `VI_Diagrams`**, and **add a row after reading a VI that is not
in it.**

Diagram renders are the most expensive thing in this project to look at:
LabVIEW's 9 px text has to be read at 2-8x zoom, and a single investigation
can run to hundreds of image reads. Re-deriving a fact this file already holds
costs real money and buys nothing. Exporting frames is cheap; *reading* them
is not.

Prefer, in order: the index -> the existing write-up it points to -> the
already-exported hidden frames -> a new export. Say what you are about to
open and why before opening a batch of renders.

## Honesty about what is real

Controls that are laid out to match the real panel but are not wired to
hardware are left **disabled/greyed**, never made to look functional.
See the `main_window.py` module docstring. Do not add explanatory
dev-note labels into the UI -- the user has removed all of them.

## Cleanup decisions (the user's, 2026-09-05) -- these override "pixel-comparable"

The port is a cleanup, not a 1:1 copy: LouisXIV accumulated junk over the
years. The user decided, tab by tab:

- Removed: the empty tabs Preferences, Adv Setup, Bckgrd, Blank, Image
  Profile, Stack Profile, Row Profile, Timing; Scan Setup's Perfusion box;
  the Camera tab's SubROIs / Dual View / Split pix #.
- The Images tab's tool strip, Max Counts and Display Options panels were
  removed and then RESTORED + WIRED (user's call): the tool strip's drawing
  buttons and camera selectors stay greyed, but the Scale mapping (Autoscale
  Z / Scale to Counts / neither), palette (Gray/Gradient/Rainbow), Frames to
  Avg, Zoom to fit, Scalebar and Text Info Overlay are all live -- see
  gui/display.py and MainWindow._render/_display_frame.
- Kept although unwired: Scan Setup's Timepoints and Multi-location boxes
  (wanted later).
- Utilities grid: 11 of the 18 tools; Align Laser, Image Reviewer, Calculate
  PSF, Auto Background, View TIF stack, Resave OME-XML TIFs and Shift Vslit
  calibration were removed (user, 2026-09-05).
- Stack Projections "Calc" is always enabled, like LouisXIV's latch button;
  with no stack in memory it only logs.
- um per V calibration opens as its own window from the Utilities grid; the
  separate "um/V Cal" left tab was removed (user, 2026-09-05).
- Low-Level Waveform Config lives under Utilities (LouisXIV: Adv Setup).

Match the real panel for what remains; do not re-add removed elements
without asking. When unsure whether an element is a keeper, ask.
