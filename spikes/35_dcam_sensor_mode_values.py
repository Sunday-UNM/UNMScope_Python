"""Which SENSOR MODE / READOUT DIRECTION values will this Orca actually take?

Follow-up to spike 34. `DCAM - Set Sensor Mode.vi` (hidden frames exported
2026-09-05) shows LouisXIV writes raw DCAM numbers, not strings:

    Normal Scan  -> SENSOR MODE      = 1   (AREA)
    Light Sheet  -> SENSOR MODE      = 12  (PROGRESSIVE)
    Split View   -> SENSOR MODE      = 14  (DUALLIGHTSHEET)
    Dual LS      -> SENSOR MODE      = 16
    Rolling Top  -> READOUT DIRECTION = 1  (FORWARD)
    Rolling Bot  -> READOUT DIRECTION = 2  (BACKWARD)

Spike 34 found the adapter offers only AREA / SPLIT VIEW for SENSOR MODE and
only DIVERGE for READOUT DIRECTION. DCAM narrows a property's valid set
according to the camera's current state, so the question is whether the
light-sheet values appear once the camera is in another mode.

This walks every allowed SENSOR MODE, re-enumerating both properties in each,
and tries the names LouisXIV's numbers correspond to. Restores AREA at the end.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from unmscope.hardware.camera import OrcaFlash4Camera  # noqa: E402

SENSOR, READOUT = "SENSOR MODE", "READOUT DIRECTION"
#: The DCAM SDK spellings for the numbers LouisXIV writes.
WANTED = ["AREA", "PROGRESSIVE", "SPLIT VIEW", "SPLITVIEW",
          "DUAL LIGHT SHEET", "DUALLIGHTSHEET", "LIGHTSHEET", "LIGHT SHEET"]


def show(mmc, label, prop):
    return f"{prop} = {mmc.getProperty(label, prop)!r} allowed={list(mmc.getAllowedPropertyValues(label, prop))}"


def main() -> int:
    cam = OrcaFlash4Camera()
    cam.connect()
    mmc, label = cam._mmc, cam.DEVICE_LABEL
    try:
        start = mmc.getProperty(label, SENSOR)
        print(f"at rest:\n  {show(mmc, label, SENSOR)}\n  {show(mmc, label, READOUT)}")

        for mode in list(mmc.getAllowedPropertyValues(label, SENSOR)):
            print(f"\n=== with SENSOR MODE = {mode!r} ===")
            try:
                mmc.setProperty(label, SENSOR, mode)
            except Exception as e:
                print(f"  refused: {e}")
                continue
            print(f"  {show(mmc, label, SENSOR)}")
            print(f"  {show(mmc, label, READOUT)}")
            print(f"  TRIGGER ACTIVE allowed={list(mmc.getAllowedPropertyValues(label, 'TRIGGER ACTIVE'))}")
            print(f"  ScanMode = {mmc.getProperty(label, 'ScanMode')!r}"
                  f"  ReadoutTime = {mmc.getProperty(label, 'ReadoutTime')!r}")

        mmc.setProperty(label, SENSOR, start)

        print(f"\n=== can the light-sheet spellings be written directly? ===")
        for v in WANTED:
            try:
                mmc.setProperty(label, SENSOR, v)
                got = mmc.getProperty(label, SENSOR)
                print(f"  {v!r:22} accepted -> now {got!r}")
                mmc.setProperty(label, SENSOR, start)
            except Exception as e:
                print(f"  {v!r:22} refused ({str(e).splitlines()[0][:90]})")

        print(f"\n=== ScanMode 1 vs 2 (the adapter's own slow/fast switch) ===")
        for sm in ("1", "2"):
            try:
                mmc.setProperty(label, "ScanMode", sm)
                print(f"  ScanMode={sm}: ReadoutTime={mmc.getProperty(label, 'ReadoutTime')!r}"
                      f"  {show(mmc, label, SENSOR)}")
            except Exception as e:
                print(f"  ScanMode={sm}: refused {e}")
        mmc.setProperty(label, "ScanMode", "1")
        mmc.setProperty(label, SENSOR, start)
        print(f"\nrestored: {show(mmc, label, SENSOR)}")
    finally:
        cam.disconnect()
        print("disconnected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
