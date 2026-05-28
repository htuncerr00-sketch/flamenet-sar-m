# Release Build Guide

How to produce `release/FilamentWindingCAM.exe` — a self-contained Windows
executable that requires no Python installation on end-user machines.

---

## Prerequisites (build machine)

| Requirement | Version | Notes |
|---|---|---|
| Windows 10/11 64-bit | any | build must run on Windows |
| Python | 3.11 or 3.12 | must match target architecture (64-bit) |
| All app dependencies | latest | run `install_deps_windows.bat` first |
| PyInstaller | 6.x | installed by `build_windows_exe.py` automatically |
| Available disk | 2 GB | PyInstaller temp files + output |

---

## Build Steps

### 1. Install dependencies

```cmd
install_deps_windows.bat
```

### 2. Run the build script

```cmd
python build_windows_exe.py
```

This will:
1. Install PyInstaller if not present
2. Generate `FilamentWindingCAM.spec`
3. Run PyInstaller (takes 2–5 minutes)
4. Move output to `release/FilamentWindingCAM.exe`

### 3. Test the exe

```cmd
release\FilamentWindingCAM.exe
```

Expected: splash screen appears, dependency checks pass, home screen shows.

### 4. Create portable zip (optional)

```powershell
.\create_portable_release.ps1
```

Output: `release/FilamentWindingCAM_portable_YYYYMMDD.zip`

---

## Output Structure

```
release/
  FilamentWindingCAM.exe         ← single self-contained exe (~80 MB)
  FilamentWindingCAM_portable_*  ← zip with Python source (requires Python)
```

On first launch, the exe creates:
```
workspace/
  gcode/                         ← generated programs
  projects/                      ← recipes (SQLite)
  exports/
logs/
  YYYYMMDD/
    startup_HHMMSS.log
```

---

## Distribution

**Exe distribution (recommended for non-technical users):**
- Copy `release/FilamentWindingCAM.exe` to target machine
- No Python, pip, or any packages required
- Double-click to run

**Portable source distribution (for advanced users):**
- Extract `FilamentWindingCAM_portable_*.zip`
- Run `install_deps_windows.bat` once
- Double-click `START_FILAMENT_CAM.bat`

---

## Troubleshooting the Build

### PyInstaller fails with "ModuleNotFoundError"

Add the missing module to `hiddenimports` in `build_windows_exe.py`:
```python
hiddenimports=[
    ...,
    'your.missing.module',
],
```

### Exe crashes on target machine with "DLL load failed"

Install **Microsoft Visual C++ Redistributable 2015–2022 (x64)** on the
target machine from Microsoft's download site.

### Exe is slow to start (>10 seconds)

This is normal for one-file PyInstaller bundles — it extracts to `%TEMP%`
on first run.  Subsequent runs from the same session use the cached extraction.
To eliminate the delay, consider switching to `--onedir` mode (one folder
instead of one file) by editing the spec and removing `a.zipped_data`.

### "Failed to execute script" error

Run from a command prompt to see the full traceback:
```cmd
release\FilamentWindingCAM.exe 2>&1 | more
```

---

## Spec File Notes

The auto-generated `FilamentWindingCAM.spec` includes:
- `datas`: packages the Python source trees as data so `importlib` can find
  `real_esp32_link.py` at its expected relative path
- `hiddenimports`: PySide6 Qt plugins, pyqtgraph internals, all faz17_d1 submodules
- `console=False`: hides the terminal window for normal use
- `upx=True`: compresses the exe (requires UPX installed; silently skipped if absent)

To customize the spec, edit `FilamentWindingCAM.spec` and re-run PyInstaller:
```cmd
pyinstaller --clean --noconfirm FilamentWindingCAM.spec
```

---

## Version Numbering

Update `APP_VERSION` in `app_launcher.py` before each release:
```python
APP_VERSION = "1.1"
```

The version string appears in:
- Splash screen
- Window title bar
- Log file headers
