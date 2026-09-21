# PyInstaller spec for the UNMScope GUI (python -m unmscope.gui).
#
# Build (from UNMScope_Python\, with the py -3.11 env that has the app's
# deps installed -- see pyproject.toml):
#   py -3.11 -m PyInstaller packaging\UNMScope.spec --noconfirm
#
# Output: dist\UNMScope\UNMScope.exe (onedir -- see the note below on why
# onedir, not onefile).
#
# What this does NOT bundle, and why that is correct, not an oversight:
#   - The FPGA bitfile and LouisXIV's SPIMProject.ini/support files: read
#     from fixed absolute paths (config/paths.py: D:\UNM_Lightsheet\... or
#     C:\UnmScopeOpen\...), not from anything inside this package. Those
#     paths must exist on whatever machine runs this exe; packaging cannot
#     and should not change that.
#   - UNMScope's own settings (SPIMProject.ini copy, waveform config JSON):
#     written to the user's home directory (~/.unmscope) at first run,
#     already independent of where the code/exe lives.
#   - The NI-RIO/FlexRIO driver (nifpga.py talks to it via ctypes, dlopen-ing
#     a system DLL, not something pip-installed) and Micro-Manager's device
#     adapter DLLs (pymmcore-plus installs those separately via `mmcore
#     install`). Neither ships inside a pip wheel, so neither can ship
#     inside this exe -- they are a driver install on the target machine,
#     exactly as today with the unfrozen script.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

# repo layout: UNMScope_Python/packaging/UNMScope.spec, source under ../src
ROOT = Path(SPECPATH).resolve().parent
SRC = ROOT / "src"
ENTRY = SRC / "unmscope" / "gui" / "__main__.py"

# nifpga: pure-Python ctypes bindings, no PyInstaller hook exists for it.
# pymmcore / pymmcore_plus: pymmcore has one compiled extension
# (_pymmcore_swig*.pyd) and pymmcore_plus has enough submodules/plugin-style
# discovery (_discovery.py) that static import analysis alone is a risk --
# collect_all is the defensive, standard fix for exactly this shape of
# problem rather than hand-listing hiddenimports and hoping.
datas, binaries, hiddenimports = [], [], []
for pkg in ("nifpga", "pymmcore", "pymmcore_plus"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [str(ENTRY)],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    excludes=["tkinter", "pytest", "pytest_asyncio"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="UNMScope",
    console=False,          # windowed GUI app -- flip to True temporarily to see a crash traceback
    icon=None,
)

# onedir, not onefile: onefile re-extracts the whole bundle to a temp dir on
# every launch, which is slower and is a known source of trouble for native
# DLL search paths (NI/DCAM-style drivers that expect siblings on disk) --
# onedir keeps everything on disk in one folder, which is the safer default
# for an app with this many native dependencies. The folder is still just
# "copy it and run UNMScope.exe" on another machine.
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="UNMScope",
)
