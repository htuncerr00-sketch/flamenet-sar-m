@echo off
REM install_deps_windows.bat — Install Python dependencies
REM ========================================================
REM Run this once before running run_desktop_sim.bat.
REM Requires Python 3.11+ already installed and on PATH.

setlocal EnableDelayedExpansion

REM ── Locate Python (py launcher has priority on Windows) ────────────────────
set "PYTHON="
for %%P in (py.exe python3.exe python.exe) do (
    if "!PYTHON!"=="" (
        where %%P >nul 2>nul
        if !ERRORLEVEL! == 0 set "PYTHON=%%P"
    )
)

if "!PYTHON!"=="" (
    echo ERROR: Python not found on PATH.
    echo.
    echo 1. Download Python 3.11+ from https://www.python.org/downloads/
    echo 2. During install, check "Add Python to PATH"
    echo 3. Re-run this script.
    pause
    exit /b 1
)

echo Using: !PYTHON!
echo.

REM ── Upgrade pip ────────────────────────────────────────────────────────────
echo Upgrading pip...
!PYTHON! -m pip install --upgrade pip
if !ERRORLEVEL! NEQ 0 (
    echo WARNING: pip upgrade failed, continuing anyway.
)
echo.

REM ── Install packages ───────────────────────────────────────────────────────
echo Installing core UI dependencies...
!PYTHON! -m pip install PySide6
if !ERRORLEVEL! NEQ 0 goto :pip_error

!PYTHON! -m pip install pyqtgraph
if !ERRORLEVEL! NEQ 0 goto :pip_error

!PYTHON! -m pip install numpy
if !ERRORLEVEL! NEQ 0 goto :pip_error

echo.
echo Installing optional dependencies...
!PYTHON! -m pip install PyOpenGL
if !ERRORLEVEL! NEQ 0 echo WARNING: PyOpenGL install failed. 3D winding panel may not render.

!PYTHON! -m pip install pyserial
if !ERRORLEVEL! NEQ 0 echo WARNING: pyserial install failed. Real ESP32 mode will not work.

echo.
echo =========================================================
echo All required packages installed successfully.
echo Run run_desktop_sim.bat to launch the application.
echo =========================================================
pause
exit /b 0

:pip_error
echo.
echo ERROR: pip install failed ^(exit code !ERRORLEVEL!^).
echo Check your internet connection and try again.
echo If behind a proxy: set HTTPS_PROXY=http://proxy:port
pause
exit /b !ERRORLEVEL!
