"""Pixel-compare the Python GUI against the real LabVIEW front panel.

Why this exists: layout claims made by *looking* at screenshots have been
wrong repeatedly in this project (see CLAUDE.md). This turns the check
into one command that prints numbers instead of impressions.

    python tools/compare_to_labview.py

It finds both windows, screenshots them, auto-detects the solid
light-grey image regions (the camera canvas and the XY/YZ/XZ MIP boxes
are all #f0f0f0 in both apps), matches them up by position, and prints a
reference-vs-ours table with deltas. Exits non-zero if anything is off by
more than --tolerance px, so it can gate a change.

IMPORTANT -- it can only compare what is currently on screen: put both
apps on the SAME tabs before running (e.g. both showing Images, or both
showing Stack Projections). The tool says which tab state it saw only in
the sense of what regions it found; it cannot switch tabs for you.

Requires: Pillow, numpy (pip install -e ".[dev]").
Windows-only (uses user32 + PIL.ImageGrab).
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import sys
import time

import numpy as np

try:
    from PIL import Image, ImageGrab
except ImportError:  # pragma: no cover
    sys.exit("Pillow is required: pip install pillow")

user32 = ctypes.windll.user32

# The real front panel's window title. NOTE: LouisXIV.exe also exposes a
# hidden dummy window that Process.MainWindowHandle returns instead --
# that one has a 0x0 rect and is useless. Always enumerate and match on
# this title.
LABVIEW_TITLE = "SPIM MAIN"
PYTHON_TITLE = "UNMScope"

SW_RESTORE = 9


class RECT(ctypes.Structure):
    _fields_ = [("left", wt.LONG), ("top", wt.LONG),
                ("right", wt.LONG), ("bottom", wt.LONG)]


def find_window(title_substring: str) -> tuple[int, tuple[int, int, int, int], str]:
    """Return (hwnd, (l, t, r, b), title) for the first VISIBLE top-level
    window whose title contains `title_substring` and has a real size."""
    found: list[tuple[int, tuple[int, int, int, int], str]] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        if title_substring.lower() not in title.lower():
            return True
        rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w < 200 or h < 200:  # skip the hidden dummy/proxy windows
            return True
        found.append((hwnd, (rect.left, rect.top, rect.right, rect.bottom), title))
        return True

    user32.EnumWindows(callback, 0)
    if not found:
        raise LookupError(f"no visible window matching {title_substring!r}")
    return found[0]


def raise_window(hwnd: int) -> None:
    """Actually bring `hwnd` to the front, and VERIFY that it worked.

    Windows refuses SetForegroundWindow from a process that isn't already
    foreground -- it fails *silently* and returns without raising
    anything. That is not a cosmetic problem here: these two windows
    overlap on screen, so a failed raise means you screenshot the OTHER
    app through this window's rectangle and compare it against itself.
    That produced a confident, entirely fake "+0, +0" match once already.

    AttachThreadInput to the current foreground thread lifts the
    restriction; the assert afterwards is the part that matters.
    """
    user32.ShowWindow(hwnd, SW_RESTORE)
    if user32.GetForegroundWindow() != hwnd:
        kernel32 = ctypes.windll.kernel32
        cur = kernel32.GetCurrentThreadId()
        fg_thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
        tgt_thread = user32.GetWindowThreadProcessId(hwnd, None)
        for t in (fg_thread, tgt_thread):
            if t:
                user32.AttachThreadInput(cur, t, True)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        for t in (fg_thread, tgt_thread):
            if t:
                user32.AttachThreadInput(cur, t, False)
    time.sleep(0.45)
    if user32.GetForegroundWindow() != hwnd:
        raise RuntimeError(
            "could not bring the window to the front -- refusing to "
            "screenshot, because the other app overlaps it and the capture "
            "would silently be of the WRONG window. Click the window once "
            "and re-run, or drag the two windows apart.")


def capture(hwnd: int, rect: tuple[int, int, int, int]) -> Image.Image:
    """Raise the window (verified) and grab exactly its rectangle."""
    raise_window(hwnd)
    fresh = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(fresh))
    box = (fresh.left, fresh.top, fresh.right, fresh.bottom)
    if box[2] - box[0] < 200:
        box = rect
    return ImageGrab.grab(bbox=box, all_screens=True)


def detect_boxes(img: Image.Image, fill: tuple[int, int, int], tol: int,
                 min_w: int, min_h: int) -> list[tuple[int, int, int, int]]:
    """Find solid axis-aligned rectangles of `fill` colour.

    Returns [(x, y, w, h), ...] sorted by area descending. Works by
    masking the fill colour and growing connected row-runs, then keeping
    components whose bounding box is >=95% filled (i.e. really a
    rectangle, not a ragged blob).
    """
    arr = np.asarray(img.convert("RGB")).astype(np.int16)
    target = np.array(fill, dtype=np.int16)
    mask = (np.abs(arr - target).sum(axis=2) <= tol)

    h, w = mask.shape
    labels = np.zeros((h, w), dtype=np.int32)
    next_label = 1
    # union-find over row-runs
    parent: dict[int, int] = {}

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for y in range(h):
        row = mask[y]
        x = 0
        while x < w:
            if not row[x]:
                x += 1
                continue
            start = x
            while x < w and row[x]:
                x += 1
            run = slice(start, x)
            above = labels[y - 1, run] if y > 0 else np.zeros(0, dtype=np.int32)
            neighbours = np.unique(above[above > 0])
            if neighbours.size == 0:
                lab = next_label
                parent[lab] = lab
                next_label += 1
            else:
                lab = int(neighbours[0])
                for other in neighbours[1:]:
                    union(lab, int(other))
            labels[y, run] = lab

    boxes: list[tuple[int, int, int, int]] = []
    if next_label > 1:
        flat = labels.ravel()
        roots = np.zeros(next_label, dtype=np.int32)
        for lab in range(1, next_label):
            roots[lab] = find(lab)
        resolved = roots[flat].reshape(h, w)
        for root in np.unique(resolved):
            if root == 0:
                continue
            ys, xs = np.nonzero(resolved == root)
            x0, x1 = int(xs.min()), int(xs.max())
            y0, y1 = int(ys.min()), int(ys.max())
            bw, bh = x1 - x0 + 1, y1 - y0 + 1
            if bw < min_w or bh < min_h:
                continue
            if ys.size / float(bw * bh) < 0.95:  # not a solid rectangle
                continue
            boxes.append((x0, y0, bw, bh))

    boxes.sort(key=lambda b: b[2] * b[3], reverse=True)
    return boxes


def match_boxes(ref: list[tuple[int, int, int, int]], ref_size: tuple[int, int],
                ours: list[tuple[int, int, int, int]], our_size: tuple[int, int]):
    """Pair reference boxes with ours by nearest normalised centre."""
    rw, rh = ref_size
    ow, oh = our_size
    remaining = list(ours)
    pairs = []
    for rb in ref:
        rcx = (rb[0] + rb[2] / 2) / rw
        rcy = (rb[1] + rb[3] / 2) / rh
        best, best_d = None, None
        for ob in remaining:
            ocx = (ob[0] + ob[2] / 2) / ow
            ocy = (ob[1] + ob[3] / 2) / oh
            d = (rcx - ocx) ** 2 + (rcy - ocy) ** 2
            if best_d is None or d < best_d:
                best, best_d = ob, d
        if best is not None:
            remaining.remove(best)
        pairs.append((rb, best))
    for ob in remaining:
        pairs.append((None, ob))
    return pairs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tolerance", type=int, default=8,
                    help="max acceptable per-dimension delta in px (default 8)")
    ap.add_argument("--fill", default="f0f0f0",
                    help="hex fill colour of the image regions (default f0f0f0)")
    ap.add_argument("--color-tol", type=int, default=12,
                    help="per-pixel colour distance tolerance (default 12)")
    ap.add_argument("--min-size", type=int, default=60,
                    help="ignore detected regions smaller than this (default 60)")
    ap.add_argument("--save", metavar="DIR",
                    help="also write the two screenshots into DIR")
    args = ap.parse_args()

    fill = tuple(int(args.fill[i:i + 2], 16) for i in (0, 2, 4))

    try:
        ref_hwnd, ref_rect, ref_title = find_window(LABVIEW_TITLE)
    except LookupError:
        return _fail(f"Could not find the LabVIEW window ({LABVIEW_TITLE!r}). "
                     "Start LouisXIV.exe first.")
    try:
        our_hwnd, our_rect, our_title = find_window(PYTHON_TITLE)
    except LookupError:
        return _fail(f"Could not find the Python window ({PYTHON_TITLE!r}). "
                     "Run `python -m unmscope.gui` first.")

    try:
        ref_img = capture(ref_hwnd, ref_rect)
        our_img = capture(our_hwnd, our_rect)
    except RuntimeError as exc:
        return _fail(str(exc))

    if args.save:
        import os
        os.makedirs(args.save, exist_ok=True)
        ref_img.save(os.path.join(args.save, "labview_ref.png"))
        our_img.save(os.path.join(args.save, "python_current.png"))
        print(f"screenshots written to {args.save}\n")

    print(f"reference : {ref_title!r}  {ref_img.width}x{ref_img.height}")
    print(f"ours      : {our_title!r}  {our_img.width}x{our_img.height}")
    dw = our_img.width - ref_img.width
    dh = our_img.height - ref_img.height
    print(f"window delta: {dw:+d} x {dh:+d} px")
    print()

    ref_boxes = detect_boxes(ref_img, fill, args.color_tol, args.min_size, args.min_size)
    our_boxes = detect_boxes(our_img, fill, args.color_tol, args.min_size, args.min_size)

    if not ref_boxes and not our_boxes:
        print(f"No #{args.fill} regions found in either window. If the apps are "
              "on tabs without image areas, switch both to Images or Stack "
              "Projections and re-run.")
        return 0

    pairs = match_boxes(ref_boxes, ref_img.size, our_boxes, our_img.size)

    print(f"{'region':<10} {'reference (x,y,w,h)':<26} {'ours (x,y,w,h)':<26} {'d(w,h)':>12}")
    print("-" * 78)
    worst = 0
    for i, (rb, ob) in enumerate(pairs, 1):
        name = f"box {i}"
        if rb is None:
            print(f"{name:<10} {'-- not in reference --':<26} {str(ob):<26} {'EXTRA':>12}")
            worst = max(worst, args.tolerance + 1)
            continue
        if ob is None:
            print(f"{name:<10} {str(rb):<26} {'-- missing in ours --':<26} {'MISSING':>12}")
            worst = max(worst, args.tolerance + 1)
            continue
        ddw, ddh = ob[2] - rb[2], ob[3] - rb[3]
        worst = max(worst, abs(ddw), abs(ddh))
        flag = "" if max(abs(ddw), abs(ddh)) <= args.tolerance else "  <-- OFF"
        print(f"{name:<10} {str(rb):<26} {str(ob):<26} {ddw:+5d},{ddh:+5d}{flag}")

    print()
    if worst <= args.tolerance:
        print(f"PASS - every region within {args.tolerance}px")
        return 0
    print(f"FAIL - worst deviation {worst}px (tolerance {args.tolerance}px)")
    print("Remember: numbers here are measured. Do not 'fix' layout by eye -- "
          "re-run this after each change.")
    return 1


def _fail(msg: str) -> int:
    print(msg, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
