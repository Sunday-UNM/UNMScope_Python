"""Ask the Orca what its SENSOR MODE values and subarray units really are.

ROADMAP item 10. Two things have been ASSUMED in the port since the camera
tab was built:

  * ``OrcaFlash4Camera.SENSOR_MODE_VALUES`` maps LouisXIV's four mode names
    onto the DCAM strings AREA / PROGRESSIVE / SPLIT VIEW, guessed from the
    DCAM docs rather than read off this camera.
  * ``roi.DEFAULT_POSITION_UNIT`` and ``DEFAULT_SIZE_UNIT`` are both 4,
    which is what the Flash 4.0 manual says, but the adapter has never been
    asked.

This connects, prints every property the adapter exposes with its allowed
values and limits, then measures the subarray units empirically: set an ROI
at deliberately awkward offsets and sizes and see what the driver coerces
them to. The step it snaps to IS the unit, whatever the manual claims.

Takes the camera exclusively -- LouisXIV must be closed. Read-only apart
from the ROI, which is restored to full frame at the end.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from unmscope.hardware.camera import OrcaFlash4Camera  # noqa: E402
from unmscope.hardware.camera import other_camera_holders  # noqa: E402

INTERESTING = ("SENSOR MODE", "SENSORMODE", "ScanMode", "SUBARRAY", "Binning",
               "TRIGGER", "READOUT", "Exposure", "CameraName", "CameraID")


def main() -> int:
    holders = other_camera_holders()
    if holders:
        print(f"WARNING: these processes may already hold DCAM: {holders}")

    cam = OrcaFlash4Camera()
    cam.connect()
    mmc, label = cam._mmc, cam.DEVICE_LABEL
    try:
        info = cam.info
        print(f"connected: {info.name}  serial {info.serial}  {info.width}x{info.height}\n")

        names = list(mmc.getDevicePropertyNames(label))
        print(f"=== {len(names)} properties ===")
        for n in names:
            value = mmc.getProperty(label, n)
            allowed = list(mmc.getAllowedPropertyValues(label, n))
            line = f"  {n} = {value!r}"
            if allowed:
                line += f"   allowed: {allowed}"
            elif mmc.hasPropertyLimits(label, n):
                line += (f"   range: {mmc.getPropertyLowerLimit(label, n)}"
                         f" .. {mmc.getPropertyUpperLimit(label, n)}")
            print(line)

        print("\n=== the ones the port depends on ===")
        for n in names:
            if any(k.lower() in n.lower() for k in INTERESTING):
                allowed = list(mmc.getAllowedPropertyValues(label, n))
                print(f"  {n} = {mmc.getProperty(label, n)!r}"
                      + (f"  allowed: {allowed}" if allowed else ""))

        print("\n=== subarray units, measured ===")
        print("  request (x, y, w, h)      ->  driver returns")
        # Offsets 1..9 and sizes with a deliberate remainder. The step the
        # driver snaps each field to is the position / size unit.
        probes = [(1, 1, 2046, 2046), (2, 2, 1022, 1022), (3, 3, 510, 510),
                  (5, 5, 254, 254), (7, 7, 130, 130), (9, 9, 66, 66),
                  (100, 200, 301, 401), (1023, 1023, 1025, 1025)]
        seen_pos, seen_size = set(), set()
        for x, y, w, h in probes:
            try:
                mmc.setROI(x, y, w, h)
                gx, gy, gw, gh = mmc.getROI()
            except Exception as e:
                print(f"  ({x:5d},{y:5d},{w:5d},{h:5d})  ->  refused: {e}")
                continue
            print(f"  ({x:5d},{y:5d},{w:5d},{h:5d})  ->  ({gx:5d},{gy:5d},{gw:5d},{gh:5d})")
            seen_pos.update((gx, gy))
            seen_size.update((gw, gh))

        def unit_of(values):
            """Largest n in 1..64 that divides every observed value."""
            nonzero = [v for v in values if v]
            if not nonzero:
                return None
            return max((n for n in range(1, 65)
                        if all(v % n == 0 for v in nonzero)), default=1)

        print(f"\n  every returned position divides by: {unit_of(seen_pos)}")
        print(f"  every returned size divides by:     {unit_of(seen_size)}")
        print("  (roi.py currently assumes position unit 4, size unit 4)")

        mmc.clearROI()
        print(f"\n  restored to full frame: {mmc.getROI()}")
    finally:
        cam.disconnect()
        print("\ndisconnected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
