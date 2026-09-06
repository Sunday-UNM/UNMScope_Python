"""Crop the raw window grabs in shots_raw/ into the images the deck embeds.

Explorer grabs (01..08) keep the breadcrumb strip and the file list and
nothing else. The Notepad grab (09) keeps the editor and its status bar, and
drops the tab strip, which carries whatever else happened to be open.
"""
from PIL import Image
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "shots_raw"    # full-window grabs from the capture_*.ps1 scripts
DST = HERE / "shots_crop"
DST.mkdir(exist_ok=True)

# --- Explorer windows -------------------------------------------------------
X0, X1 = 224, 986       # right of the navigation pane, left of the window edge
ADDR = (48, 84)         # the breadcrumb strip
LIST_TOP = 136          # the column header row
SCAN_L, SCAN_R = X0 + 6, X0 + 500   # Name/Date/Type only: scanning the full
                                    # width catches the corner view-mode
                                    # buttons and every crop comes out equal
PAD = 26

# --- Notepad window ---------------------------------------------------------
# The window rect includes an invisible ~8px shadow margin, through which
# whatever is behind shows up in the grab, so trim it off all four sides.
NP_X0, NP_X1 = 18, 812
NP_TOP = 46             # below the tab strip, at the File/Edit/View menu
NP_BOTTOM_TRIM = 12     # above the shadow margin, keeping the status bar


def crop_explorer(im):
    W, H = im.size
    px = im.load()
    last = LIST_TOP
    for y in range(LIST_TOP, H - 40):
        row = [px[x, y] for x in range(SCAN_L, SCAN_R, 3)]
        if any(abs(r - 238) > 22 or abs(g - 238) > 22 or abs(b - 238) > 22 for r, g, b in row):
            last = y
    bottom = min(H, last + PAD)
    addr = im.crop((X0, ADDR[0], X1, ADDR[1]))
    lst = im.crop((X0, LIST_TOP, X1, bottom))
    w = X1 - X0
    out = Image.new("RGB", (w, addr.height + 10 + lst.height), (255, 255, 255))
    out.paste(addr, (0, 0))
    out.paste(lst, (0, addr.height + 10))
    return out


def crop_notepad(im):
    W, H = im.size
    return im.crop((NP_X0, NP_TOP, NP_X1, H - NP_BOTTOM_TRIM))


for f in sorted(SRC.glob("*.png")):
    im = Image.open(f).convert("RGB")
    out = crop_notepad(im) if f.name.startswith("09_") else crop_explorer(im)
    p = DST / f.name
    out.save(p, optimize=True)
    print(f"{f.name}: {out.size[0]} x {out.size[1]}  {p.stat().st_size//1024} KB")
