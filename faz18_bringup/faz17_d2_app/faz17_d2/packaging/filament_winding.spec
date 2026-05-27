# filament_winding.spec — PyInstaller spec for Windows .exe build
# ===================================================================
# Build with:
#   cd faz17_d2/
#   pyinstaller packaging/filament_winding.spec --clean
#
# Output:
#   dist/FilamentWinding/FilamentWinding.exe (one-folder, faster startup)
#
# To build a single-file version (slower startup), set ONEFILE=True below.

ONEFILE = False

import sys
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

# Resolve project root from this spec's location
SPEC_DIR = Path(SPECPATH).resolve()
ROOT = SPEC_DIR.parent

block_cipher = None

# Hidden imports — PyInstaller often misses Qt + pyqtgraph plugins
hiddenimports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "pyqtgraph",
    "pyqtgraph.opengl",
    "pyqtgraph.opengl.GLViewWidget",
    "pyqtgraph.opengl.items.GLMeshItem",
    "pyqtgraph.opengl.items.GLLinePlotItem",
    "pyqtgraph.opengl.items.GLScatterPlotItem",
    "OpenGL.GL",
    "OpenGL.GLU",
    "numpy",
    "sqlite3",
    "zlib",
] + collect_submodules("pyqtgraph")

datas = []
# Include any user-facing data files (icons, defaults, etc.) here
# datas += [(str(ROOT / "assets"), "assets")]

a = Analysis(
    [str(ROOT / "app" / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "scipy",
        "PIL",
        "pandas",
        # Strip unneeded Qt modules
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtNetwork",
        "PySide6.QtMultimedia",
        "PySide6.QtBluetooth",
        "PySide6.QtSql",
        "PySide6.QtTest",
        "PySide6.QtQml",
        "PySide6.QtQuick",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

if ONEFILE:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
        name="FilamentWinding",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,        # GUI app → no console
        disable_windowed_traceback=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
else:
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name="FilamentWinding",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
    )
    coll = COLLECT(
        exe, a.binaries, a.zipfiles, a.datas,
        strip=False, upx=False, name="FilamentWinding",
    )
