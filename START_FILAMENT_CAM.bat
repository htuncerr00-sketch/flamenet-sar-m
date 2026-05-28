@echo off
title Filament Winding CAM
cd /d "%~dp0"

REM ── Find Python ──────────────────────────────────────────────────────────────
set "PY="
for %%P in (python3.exe python.exe) do (
    if not defined PY (
        where %%P >nul 2>nul && set "PY=%%P"
    )
)
if not defined PY (
    echo.
    echo  Filament Winding CAM could not start.
    echo.
    echo  Python was not found on this computer.
    echo.
    echo  To fix this:
    echo    1. Go to https://www.python.org/downloads/
    echo    2. Download Python 3.12 (Windows installer, 64-bit)
    echo    3. Run the installer - CHECK "Add Python to PATH"
    echo    4. Then run install_deps_windows.bat
    echo    5. Then double-click this file again
    echo.
    pause
    exit /b 1
)

REM ── Check PySide6 ────────────────────────────────────────────────────────────
%PY% -c "import PySide6" 2>nul
if errorlevel 1 (
    echo.
    echo  Filament Winding CAM could not start.
    echo.
    echo  Required packages are not installed.
    echo.
    echo  To fix this:  double-click install_deps_windows.bat
    echo  Then try again.
    echo.
    pause
    exit /b 1
)

REM ── Launch ───────────────────────────────────────────────────────────────────
%PY% app_launcher.py
if errorlevel 1 (
    echo.
    echo  The application exited with an error.
    echo  Check logs\ for details, or see TROUBLESHOOTING.md
    echo.
    pause
)
