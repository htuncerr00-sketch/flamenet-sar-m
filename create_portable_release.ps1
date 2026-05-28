#Requires -Version 5.1
<#
.SYNOPSIS
    Create a portable zip release of Filament Winding CAM.

.DESCRIPTION
    Packages the application source + launchers into a zip that any Windows
    user can extract and run with just Python installed.

    Output: release/FilamentWindingCAM_portable_<date>.zip

    Contents of the zip:
      - app_launcher.py         ← launcher GUI
      - sim_launcher.py         ← headless simulation launcher
      - START_FILAMENT_CAM.bat  ← double-click to run
      - START_FILAMENT_CAM.ps1
      - install_deps_windows.bat
      - install_deps_windows.ps1
      - faz17_d1/               ← Python backend
      - faz17_d2/               ← PySide6 desktop app
      - faz18_bringup/real_esp32_link.py
      - DESKTOP_SIMULATION_SETUP.md
      - WINDOWS_SETUP.md
      - TROUBLESHOOTING.md
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Root    = $PSScriptRoot
$Date    = Get-Date -Format 'yyyyMMdd'
$ZipName = "FilamentWindingCAM_portable_$Date.zip"
$Release = Join-Path $Root 'release'
$ZipPath = Join-Path $Release $ZipName
$Tmp     = Join-Path $Root "_release_tmp_$Date"

Write-Host "Creating portable release: $ZipName" -ForegroundColor Cyan
Write-Host ''

# ── Clean temp dir ────────────────────────────────────────────────────────────
if (Test-Path $Tmp) { Remove-Item $Tmp -Recurse -Force }
New-Item -ItemType Directory -Path $Tmp | Out-Null

# ── Copy files ────────────────────────────────────────────────────────────────
$files = @(
    'app_launcher.py',
    'sim_launcher.py',
    'START_FILAMENT_CAM.bat',
    'START_FILAMENT_CAM.ps1',
    'install_deps_windows.bat',
    'install_deps_windows.ps1',
    'DESKTOP_SIMULATION_SETUP.md',
    'WINDOWS_SETUP.md',
    'TROUBLESHOOTING.md',
    'CLAUDE.md'
)

foreach ($f in $files) {
    $src = Join-Path $Root $f
    if (Test-Path $src) {
        Copy-Item $src (Join-Path $Tmp $f)
        Write-Host "  + $f"
    } else {
        Write-Warning "  ! $f not found, skipping"
    }
}

# ── Copy directories ──────────────────────────────────────────────────────────
$dirs = @('faz17_d1', 'faz17_d2', 'faz19_firmware')
foreach ($d in $dirs) {
    $src = Join-Path $Root $d
    if (Test-Path $src) {
        Copy-Item $src (Join-Path $Tmp $d) -Recurse -Exclude '__pycache__', '*.pyc', 'build'
        Write-Host "  + $d/"
    }
}

# real_esp32_link.py alone from faz18_bringup
$bringupDst = Join-Path $Tmp 'faz18_bringup'
New-Item -ItemType Directory -Path $bringupDst -Force | Out-Null
$rlSrc = Join-Path $Root 'faz18_bringup' 'real_esp32_link.py'
if (Test-Path $rlSrc) {
    Copy-Item $rlSrc $bringupDst
    Write-Host '  + faz18_bringup/real_esp32_link.py'
}

# ── Remove __pycache__ recursively ───────────────────────────────────────────
Get-ChildItem $Tmp -Recurse -Directory -Filter '__pycache__' |
    Remove-Item -Recurse -Force
Get-ChildItem $Tmp -Recurse -Include '*.pyc','*.pyo' |
    Remove-Item -Force

# ── Create zip ────────────────────────────────────────────────────────────────
if (-not (Test-Path $Release)) { New-Item -ItemType Directory -Path $Release | Out-Null }
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }

Compress-Archive -Path (Join-Path $Tmp '*') -DestinationPath $ZipPath
$sizeMB = (Get-Item $ZipPath).Length / 1MB

# ── Cleanup ───────────────────────────────────────────────────────────────────
Remove-Item $Tmp -Recurse -Force

Write-Host ''
Write-Host ('=' * 60) -ForegroundColor Green
Write-Host "  PORTABLE RELEASE CREATED" -ForegroundColor Green
Write-Host "  Path: $ZipPath" -ForegroundColor Green
Write-Host ("  Size: {0:F1} MB" -f $sizeMB) -ForegroundColor Green
Write-Host ('=' * 60) -ForegroundColor Green
Write-Host ''
Write-Host 'Distribution: extract the zip, then double-click START_FILAMENT_CAM.bat'
