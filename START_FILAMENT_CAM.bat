@echo off
REM ┌──────────────────────────────────────────────────────────────┐
REM │  Filament Winding CAM — One-click Launcher                   │
REM │  Double-click this file to start the application.            │
REM │                                                              │
REM │  First time? Run install_deps_windows.bat first.            │
REM └──────────────────────────────────────────────────────────────┘

setlocal EnableDelayedExpansion
cd /d "%~dp0"

REM ── Locate Python ───────────────────────────────────────────────────────────
set "PYTHON="
for %%P in (python3.exe python.exe) do (
    if "!PYTHON!"=="" (
        where %%P >nul 2>nul
        if !ERRORLEVEL! == 0 set "PYTHON=%%P"
    )
)

if "!PYTHON!"=="" (
    echo.
    echo  ERROR: Python not found.
    echo.
    echo  Please install Python 3.11+ from https://python.org
    echo  and check "Add Python to PATH" during installation.
    echo.
    echo  Then run install_deps_windows.bat before re-launching.
    pause
    exit /b 1
)

REM ── Check PySide6 is installed ──────────────────────────────────────────────
!PYTHON! -c "import PySide6" >nul 2>nul
if !ERRORLEVEL! NEQ 0 (
    echo.
    echo  ERROR: PySide6 not installed.
    echo.
    echo  Run install_deps_windows.bat first, then try again.
    echo.
    pause
    exit /b 1
)

REM ── Launch ───────────────────────────────────────────────────────────────────
!PYTHON! app_launcher.py

if !ERRORLEVEL! NEQ 0 (
    echo.
    echo  Application exited with an error.
    echo  See TROUBLESHOOTING.md for common fixes.
    echo.
    pause
)

endlocal
