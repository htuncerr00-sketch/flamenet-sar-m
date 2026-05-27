@echo off
REM ===================================================================
REM build_windows.bat — Build Windows executable
REM Run from faz17_d2/ directory (parent of packaging/)
REM ===================================================================
setlocal enabledelayedexpansion

echo === Filament Winding — Windows Build ===
echo.

REM Check Python
where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: python not found on PATH
    exit /b 1
)

REM Check + install dependencies
echo --- Checking dependencies ---
python -m pip install --quiet --upgrade pip
python -m pip install --quiet PySide6==6.11.* pyqtgraph==0.14.* PyOpenGL numpy pyinstaller

REM Clean previous build
if exist build (
    echo --- Cleaning build/ ---
    rmdir /s /q build
)
if exist dist (
    echo --- Cleaning dist/ ---
    rmdir /s /q dist
)

REM Run PyInstaller
echo --- Running PyInstaller ---
python -m PyInstaller packaging\filament_winding.spec --clean --noconfirm
if errorlevel 1 (
    echo ERROR: PyInstaller failed
    exit /b 1
)

REM Sanity check
if exist dist\FilamentWinding\FilamentWinding.exe (
    echo.
    echo === BUILD SUCCESS ===
    echo Executable: dist\FilamentWinding\FilamentWinding.exe
    for %%I in ("dist\FilamentWinding\FilamentWinding.exe") do echo Size: %%~zI bytes
) else (
    echo ERROR: expected dist\FilamentWinding\FilamentWinding.exe not found
    exit /b 1
)

endlocal
