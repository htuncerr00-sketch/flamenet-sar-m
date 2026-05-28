@echo off
REM run_desktop_sim.bat — Launch Filament Winding Desktop (simulation mode)
REM =========================================================================
REM Starts the PySide6 desktop application with MockESP32Link.
REM No hardware, no serial port, no ESP32 required.
REM
REM Prerequisites: run install_deps_windows.bat once first.
REM
REM Usage:
REM   run_desktop_sim.bat                             simulation (default)
REM   run_desktop_sim.bat --link real --port COM3     real ESP32 on COM3
REM   run_desktop_sim.bat --help                      show all options
REM =========================================================================

setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"

REM ── Locate Python ─────────────────────────────────────────────────────────
set "PYTHON="
for %%P in (python3.exe python.exe) do (
    if "!PYTHON!"=="" (
        where %%P >nul 2>nul
        if !ERRORLEVEL! == 0 set "PYTHON=%%P"
    )
)

if "!PYTHON!"=="" (
    echo ERROR: Python not found on PATH.
    echo Install Python 3.11+ from https://www.python.org/downloads/
    echo Make sure "Add Python to PATH" is checked during installation.
    echo.
    echo See WINDOWS_SETUP.md for full instructions.
    pause
    exit /b 1
)

REM ── Quick Python version check ─────────────────────────────────────────────
for /f "tokens=2 delims= " %%V in ('!PYTHON! --version 2^>^&1') do set "PY_VER=%%V"
echo Python !PY_VER! found at: & where !PYTHON!
echo.

REM ── Launch ────────────────────────────────────────────────────────────────
echo Starting Filament Winding Desktop  (SIMULATION MODE — no hardware needed)
echo -------------------------------------------------------------------------

!PYTHON! "%SCRIPT_DIR%sim_launcher.py" %*

set "EXIT_CODE=!ERRORLEVEL!"
if !EXIT_CODE! NEQ 0 (
    echo.
    echo Application exited with code !EXIT_CODE!
    echo.
    echo Common causes:
    echo   - Missing Python packages  ^(run install_deps_windows.bat^)
    echo   - PySide6 not installed    ^(pip install PySide6^)
    echo   - pyqtgraph not installed  ^(pip install pyqtgraph^)
    echo.
    echo See TROUBLESHOOTING.md for detailed solutions.
    pause
)

endlocal
exit /b %EXIT_CODE%
