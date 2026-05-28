#Requires -Version 5.1
<#
.SYNOPSIS
    Filament Winding CAM — One-click Launcher (PowerShell)

.DESCRIPTION
    Double-click this file (or right-click → Run with PowerShell) to start
    the application.  First-time users should run install_deps_windows.ps1.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location $PSScriptRoot

# ── Locate Python ─────────────────────────────────────────────────────────────
$Python = $null
foreach ($c in @('python3', 'python')) {
    if (Get-Command $c -ErrorAction SilentlyContinue) { $Python = $c; break }
}
if (-not $Python) {
    Write-Host ''
    Write-Host ' ERROR: Python not found.' -ForegroundColor Red
    Write-Host ''
    Write-Host ' Install Python 3.11+ from https://python.org'
    Write-Host ' Check "Add Python to PATH" during installation.'
    Write-Host ''
    Write-Host ' Then run install_deps_windows.ps1 before re-launching.'
    Read-Host 'Press Enter to exit'
    exit 1
}

# ── Check PySide6 ─────────────────────────────────────────────────────────────
& $Python -c 'import PySide6' 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host ''
    Write-Host ' ERROR: PySide6 not installed.' -ForegroundColor Red
    Write-Host ''
    Write-Host ' Run install_deps_windows.ps1 first, then try again.'
    Read-Host 'Press Enter to exit'
    exit 1
}

# ── Launch ────────────────────────────────────────────────────────────────────
Write-Host 'Starting Filament Winding CAM…' -ForegroundColor Cyan
& $Python app_launcher.py

if ($LASTEXITCODE -ne 0) {
    Write-Host ''
    Write-Host " Application exited with code $LASTEXITCODE" -ForegroundColor Red
    Write-Host ' See TROUBLESHOOTING.md for common fixes.'
    Read-Host 'Press Enter to exit'
}
exit $LASTEXITCODE
