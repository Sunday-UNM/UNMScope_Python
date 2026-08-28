"""
Camera connectivity spike: try to load the Hamamatsu DCAM adapter (already
installed with Micro-Manager 2.0 on this machine) via pymmcore-plus, and
snap one frame with whatever's actually connected.

Micro-Manager's own CoreLogs (C:\\Program Files\\Micro-Manager-2.0\\CoreLogs\\)
show this exact adapter DID successfully initialize in July 2024 but failed
every attempt in March 2025 ("Native module failed to load (6)") -- most
likely because the camera wasn't connected/powered at that time. Device
Manager right now shows "Hamamatsu C11440-42U32" present/OK (note: this is
a USB3 Orca Flash4.0 V3, NOT the C11440-22CU Camera Link V2 the LabVIEW
docs describe -- the physical camera appears to have been swapped/upgraded
at some point).
"""
from pymmcore_plus import CMMCorePlus, find_micromanager

ADAPTER_DIR = find_micromanager()


def main():
    mmc = CMMCorePlus()
    print("pymmcore-plus device interface version:", mmc.getAPIVersionInfo())

    print(f"Using pymmcore-plus-managed adapter dir: {ADAPTER_DIR}")
    mmc.setDeviceAdapterSearchPaths([ADAPTER_DIR])

    print("\nLoading HamamatsuHam_DCAM device...")
    mmc.loadDevice("Camera", "HamamatsuHam", "HamamatsuHam_DCAM")
    print("Loaded (not yet initialized).")

    print("Initializing device...")
    mmc.initializeDevice("Camera")
    print("Initialized OK.")

    mmc.setCameraDevice("Camera")
    print("\nCamera properties:")
    for prop in mmc.getDevicePropertyNames("Camera"):
        try:
            val = mmc.getProperty("Camera", prop)
            print(f"  {prop} = {val}")
        except Exception as e:
            print(f"  {prop} = <error: {e}>")

    print("\nSnapping one image...")
    mmc.snapImage()
    img = mmc.getImage()
    print(f"Got image: shape={img.shape} dtype={img.dtype} min={img.min()} max={img.max()} mean={img.mean():.1f}")

    mmc.unloadAllDevices()
    print("\nUnloaded all devices cleanly.")


if __name__ == "__main__":
    main()
