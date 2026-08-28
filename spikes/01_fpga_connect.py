"""
Stage A -- FPGA connect + safe state.

Opens a real nifpga session against the compiled SPIM FPGA bitfile, reads
`SW Version` as a comms sanity check, then writes an all-zero `Static AO to
set` (mirrors LabVIEW's `HHMI - SPIM Set Safe State.vi`). No camera
involved. Safe by construction: zero volts on every analog channel.

See ../docs/fpga_io_map.md for the register/pinout reference this is built
from.
"""
import nifpga

BITFILE = r"H:\UNM_Lightsheet\UNMScope_Source\bin\data\SPIMFPGAProject_SPIM_MAIN_VI.lvbitx"
RESOURCE = "RIO0"


def main():
    print(f"Opening FPGA session: bitfile={BITFILE} resource={RESOURCE}")
    with nifpga.Session(bitfile=BITFILE, resource=RESOURCE) as session:
        print("Session opened OK.")

        info = session.get_fpga_vi_signature() if hasattr(session, "get_fpga_vi_signature") else None
        if info:
            print("VI signature:", info)

        sw_version = session.registers["SW Version"]
        print("SW Version register value:", sw_version.read())

        # Safe state: AO Mode = "Set AO", Static AO to set = all zero, then apply.
        ao_mode = session.registers["AO Mode"]
        static_ao = session.registers["Static AO to set"]
        set_fp = session.registers["Set F.P. (T)"]  # note the space before "(T)"

        print("\nCurrent AO Mode:", ao_mode.read())
        print("Current Static AO to set:", static_ao.read())

        # NOTE: ground-truth from the *actual deployed bitfile* (read back
        # above), which differs from what the source block diagrams showed
        # (no per-channel AOTF0-3 fields here -- just a single "AOTF on?"
        # bool). Real hardware always wins over static analysis.
        zero_cluster = {
            "X Galvo": 0,
            "Z Galvo": 0,
            "Z Piezo": 0,
            "Dither Galvo": 0,
            "Tiling": 0,
            "Filter": 0,
            "AOTF on?": False,
        }

        # AO Mode is a plain EnumU16, not enum-string-aware in nifpga here --
        # write the integer. StringList from the bitfile XML: 0="Start/Run
        # Wvfrm", 1="Stop Wvfrm", 2="Set AO", 3="Clear AO DMA".
        AO_MODE_SET_AO = 2

        print("\nSetting AO Mode = 'Set AO' (2) and Static AO to set = all-zero (safe state)...")
        ao_mode.write(AO_MODE_SET_AO)
        static_ao.write(zero_cluster)
        set_fp.write(True)

        print("Safe state written. Reading back:")
        print("  AO Mode:", ao_mode.read())
        print("  Static AO to set:", static_ao.read())

    print("\nSession closed cleanly.")


if __name__ == "__main__":
    main()
