#Requires -Version 5.1
<#
.SYNOPSIS
    Install Python dependencies for Filament Winding Desktop.

.DESCRIPTION
    Run this once before running run_desktop_sim.ps1.
    Requires Python 3.11+ already installed and on PATH.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

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
1. Download Python 3.11+ from https://www.python.org/downloads/
2. During install, check 'Add Python to PATH'
3. Re-run this script.
"@
    exit 1
}

Write-Host "Using: $(& $Python --version 2>&1)" -ForegroundColor DarkGray
Write-Host ''

# ── Upgrade pip ────────────────────────────────────────────────────────────
Write-Host 'Upgrading pip...' -ForegroundColor Cyan
try { & $Python -m pip install --upgrade pip } catch { Write-Warning 'pip upgrade failed, continuing.' }
Write-Host ''

# ── Required packages ──────────────────────────────────────────────────────
$required = @(
    @{ Name = 'PySide6';    Desc = 'Qt6 GUI bindings (required)' },
    @{ Name = 'pyqtgraph';  Desc = '30 FPS rolling charts (required)' },
    @{ Name = 'numpy';      Desc = 'Numerical computations (required)' }
)

$optional = @(
    @{ Name = 'PyOpenGL';  Desc = '3D winding visualization panel' },
    @{ Name = 'pyserial';  Desc = 'Real ESP32 hardware mode' }
)

Write-Host 'Installing required packages...' -ForegroundColor Cyan
foreach ($pkg in $required) {
    Write-Host "  $($pkg.Name)  — $($pkg.Desc)"
    & $Python -m pip install $pkg.Name
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to install $($pkg.Name). Check your internet connection."
        exit 1
    }
}

Write-Host ''
Write-Host 'Installing optional packages...' -ForegroundColor Cyan
foreach ($pkg in $optional) {
    Write-Host "  $($pkg.Name)  — $($pkg.Desc)"
    try {
        & $Python -m pip install $pkg.Name
        if ($LASTEXITCODE -ne 0) { throw "exit $LASTEXITCODE" }
    } catch {
        Write-Warning "  $($pkg.Name) install failed — $($pkg.Desc) will be unavailable"
    }
}

Write-Host ''
Write-Host ('=' * 60) -ForegroundColor Green
Write-Host 'All required packages installed successfully.' -ForegroundColor Green
Write-Host 'Run .\run_desktop_sim.ps1 to launch the application.' -ForegroundColor Green
Write-Host ('=' * 60) -ForegroundColor Green
