from PIL import Image
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "shots_raw"    # full 1000px-wide Explorer grabs
DST = HERE / "shots_crop"
DST.mkdir(exist_ok=True)

X0, X1 = 224, 986
ADDR = (48, 84)
LIST_TOP = 136
SCAN_L, SCAN_R = X0 + 6, X0 + 500
PAD = 26

for f in sorted(SRC.glob("*.png")):
    im = Image.open(f).convert("RGB")
    W, H = im.size
    px = im.load()
    last = LIST_TOP
    for y in range(LIST_TOP, H - 40):
        row = [px[x, y] for x in range(SCAN_L, SCAN_R, 3)]
        if any(abs(r-238) > 22 or abs(g-238) > 22 or abs(b-238) > 22 for r, g, b in row):
            last = y
    bottom = min(H, last + PAD)
    addr = im.crop((X0, ADDR[0], X1, ADDR[1]))
    lst  = im.crop((X0, LIST_TOP, X1, bottom))
    w = X1 - X0
    out = Image.new("RGB", (w, addr.height + 10 + lst.height), (255, 255, 255))
    out.paste(addr, (0, 0))
    out.paste(lst, (0, addr.height + 10))
    p = DST / f.name
    out.save(p, optimize=True)
    print(f"{f.name}: {out.size[0]} x {out.size[1]}  {p.stat().st_size//1024} KB")
