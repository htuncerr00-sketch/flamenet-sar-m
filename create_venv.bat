@echo off
title Filament Winding CAM — Environment Setup
cd /d "%~dp0"

echo.
echo  =========================================================
echo   Filament Winding CAM — Creating Virtual Environment
echo  =========================================================
echo.

REM ── Find Python ──────────────────────────────────────────────────────────
set "PY="
for %%P in (py.exe python3.exe python.exe) do (
    if not defined PY (
        where %%P >nul 2>nul && set "PY=%%P"
    )
)

if not defined PY (
    echo  ERROR: Python not found on PATH.
    echo.
    echo  1. Download Python 3.11+ from https://www.python.org/downloads/
    echo  2. During install, check "Add Python to PATH"
    echo  3. Re-run this script.
    echo.
    pause
    exit /b 1
)

REM Validate version
for /f "tokens=2 delims= " %%V in ('"%PY%" --version 2^>^&1') do set "PY_VER=%%V"
echo  Python %PY_VER% found.
echo.

REM ── Create virtual environment ───────────────────────────────────────────
if exist ".venv\Scripts\activate.bat" (
    echo  Virtual environment already exists at .venv\
    echo  Delete .venv\ and re-run to recreate, or continue to update packages.
    echo.
) else (
    echo  Creating virtual environment in .venv\ ...
    "%PY%" -m venv .venv
    if errorlevel 1 (
        echo  ERROR: Failed to create virtual environment.
        echo  Try: python -m pip install --upgrade pip virtualenv
        pause
        exit /b 1
    )
    echo  Virtual environment created.
    echo.
)

REM ── Upgrade pip ──────────────────────────────────────────────────────────
echo  Upgrading pip...
.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
echo  pip upgraded.
echo.

REM ── Install requirements ─────────────────────────────────────────────────
echo  Installing dependencies from requirements.txt...
echo.
.venv\Scripts\python.exe -m pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo  ERROR: Dependency installation failed.
    echo  Check your internet connection and retry.
    echo  If behind a proxy: set HTTPS_PROXY=http://proxy:port
    pause
    exit /b 1
)

echo.
echo  =========================================================
echo   Setup complete!
echo.
echo   To launch the application:
echo     Double-click  run_app.bat
echo  =========================================================
echo.
pause
