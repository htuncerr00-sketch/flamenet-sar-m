#Requires -Version 5.1
<#
.SYNOPSIS
    Launch Filament Winding Desktop in simulation mode (no hardware required).

.DESCRIPTION
    Starts the PySide6 desktop application using MockESP32Link which generates
    synthetic telemetry at 1 kHz. No serial port, no ESP32 board needed.

    Prerequisites: run .\install_deps_windows.ps1 once first.

.PARAMETER args
    Any arguments are forwarded to app.main, e.g.:
      .\run_desktop_sim.ps1 --link real --port COM3 --baud 921600

.EXAMPLE
    .\run_desktop_sim.ps1
    Launches in simulation mode.

.EXAMPLE
    .\run_desktop_sim.ps1 --link real --port COM3
    Launches with a real ESP32 on COM3.
#>

[CmdletBinding(PositionalBinding = $false)]
param([Parameter(ValueFromRemainingArguments)][string[]]$PassThrough)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Launcher  = Join-Path $ScriptDir 'sim_launcher.py'

# ── Locate Python ─────────────────────────────────────────────────────────
$Python = $null
foreach ($candidate in @('python3', 'python')) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) {
        $Python = $candidate
        break
    }
}

if (-not $Python) {
    Write-Error @"
Python not found on PATH.
Install Python 3.11+ from https://www.python.org/downloads/
Make sure 'Add Python to PATH' is checked during installation.

See WINDOWS_SETUP.md for full instructions.
"@
    exit 1
}

# ── Quick version check ────────────────────────────────────────────────────
$version = & $Python --version 2>&1
Write-Host "$version found" -ForegroundColor DarkGray
Write-Host ''

# ── Launch ────────────────────────────────────────────────────────────────
Write-Host 'Starting Filament Winding Desktop  (SIMULATION MODE — no hardware needed)' `
    -ForegroundColor Cyan
Write-Host ('-' * 70)

if ($PassThrough) {
    & $Python $Launcher @PassThrough
} else {
    & $Python $Launcher
}

$exitCode = $LASTEXITCODE

if ($exitCode -ne 0) {
    Write-Host ''
    Write-Host "Application exited with code $exitCode" -ForegroundColor Red
    Write-Host ''
    Write-Host 'Common causes:' -ForegroundColor Yellow
    Write-Host '  - Missing Python packages  (run .\install_deps_windows.ps1)'
    Write-Host '  - PySide6 not installed    (pip install PySide6)'
    Write-Host '  - pyqtgraph not installed  (pip install pyqtgraph)'
    Write-Host ''
    Write-Host 'See TROUBLESHOOTING.md for detailed solutions.' -ForegroundColor Yellow
}

exit $exitCode
