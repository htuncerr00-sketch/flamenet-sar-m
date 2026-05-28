@echo off
title Filament Winding CAM
cd /d "%~dp0"

REM ── Prefer venv if it exists ─────────────────────────────────────────────
set "PY="
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
    goto :found
)

REM ── Fall back to system Python ───────────────────────────────────────────
for %%P in (py.exe python3.exe python.exe) do (
    if not defined PY (
        where %%P >nul 2>nul && set "PY=%%P"
    )
)

:found
if not defined PY (
    echo.
    echo  ERROR: Python not found.
    echo.
    echo  Run create_venv.bat to set up the environment,
    echo  or install Python 3.11+ from https://www.python.org/
    echo.
    pause
    exit /b 1
)

REM ── Quick dependency check ───────────────────────────────────────────────
"%PY%" -c "import PySide6, pyqtgraph, numpy" 2>nul
if errorlevel 1 (
    echo.
    echo  Required packages are not installed.
    echo.
    echo  Run create_venv.bat to install all dependencies automatically.
    echo.
    pause
    exit /b 1
)

REM ── Launch ───────────────────────────────────────────────────────────────
"%PY%" app_launcher.py
set "EXIT_CODE=%errorlevel%"

if %EXIT_CODE% neq 0 (
    echo.
    echo  The application exited with code %EXIT_CODE%.
    echo  Check logs\ for details or see TROUBLESHOOTING.md
    echo.
    pause
)
exit /b %EXIT_CODE%
