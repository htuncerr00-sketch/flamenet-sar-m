@echo off
title Filament Winding CAM
cd /d "%~dp0"

REM ── Find Python (tries py launcher first, then python3, then python) ──────────
set "PY="
for %%P in (py.exe python3.exe python.exe) do (
    if not defined PY (
        where %%P >nul 2>nul && set "PY=%%P"
    )
)

REM py.exe launcher: make sure it points to a real Python, not just the store stub
if defined PY (
    %PY% --version >nul 2>nul || set "PY="
)

if not defined PY (
    echo.
    echo  Filament Winding CAM could not start.
    echo.
    echo  Python is installed but not reachable from the command line.
    echo.
    echo  Kolay cozum:
    echo    1. Baslat menu -^> "Python" ara -^> "Python 3.14" uygulamasini ac
    echo    2. Acildiktan sonra bu pencereyi kapat
    echo    3. START_FILAMENT_CAM.bat dosyasina tekrar cift tikla
    echo.
    echo  Alternatif cozum - PATH'e ekle:
    echo    1. Baslat -^> "Ortam degiskenleri duzenle" ara
    echo    2. Kullanici PATH degiskenini sec, Duzenle
    echo    3. Yeni ekle: C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python314\
    echo    4. Tamam -^> Tamam -^> bu bat dosyasini tekrar cift tikla
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
