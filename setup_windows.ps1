#Requires -Version 5.1
<#
.SYNOPSIS
    Filament Winding CAM — One-time Windows setup script.

.DESCRIPTION
    Creates a Python virtual environment, installs all dependencies,
    and verifies the installation. After this runs successfully,
    launch the app by double-clicking run_app.bat.

.PARAMETER NoVenv
    Skip venv creation and install into the system/user Python instead.

.PARAMETER PythonCmd
    Explicit Python executable to use (e.g. "C:\Python311\python.exe").

.EXAMPLE
    .\setup_windows.ps1
    .\setup_windows.ps1 -NoVenv
    .\setup_windows.ps1 -PythonCmd "C:\Python311\python.exe"
#>
param(
    [switch]$NoVenv,
    [string]$PythonCmd = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# ── Helpers ──────────────────────────────────────────────────────────────────
function Write-Step { param($msg) Write-Host "`n  >>> $msg" -ForegroundColor Cyan }
function Write-Ok   { param($msg) Write-Host "      OK  $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "      --  $msg" -ForegroundColor Yellow }
function Write-Fail { param($msg) Write-Host "     ERR  $msg" -ForegroundColor Red }

function Invoke-Checked {
    param([string]$cmd, [string[]]$args, [string]$label)
    & $cmd @args
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "$label failed (exit $LASTEXITCODE)"
        exit 1
    }
}

Write-Host ""
Write-Host "  =============================================================" -ForegroundColor Cyan
Write-Host "   Filament Winding CAM — Windows Setup" -ForegroundColor Cyan
Write-Host "  =============================================================" -ForegroundColor Cyan

# ── 1. Find Python ───────────────────────────────────────────────────────────
Write-Step "Finding Python..."

$py = $PythonCmd
if (-not $py) {
    foreach ($candidate in @("py", "python3", "python")) {
        try {
            $null = & $candidate --version 2>&1
            if ($LASTEXITCODE -eq 0) { $py = $candidate; break }
        } catch { }
    }
}

if (-not $py) {
    Write-Fail "Python not found on PATH."
    Write-Host "  Download Python 3.11+ from https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "  During install, enable 'Add Python to PATH'." -ForegroundColor Yellow
    exit 1
}

$pyVer = (& $py --version 2>&1).ToString().Trim()
Write-Ok "$pyVer  ($py)"

# Check version >= 3.11
$verMatch = $pyVer -match 'Python (\d+)\.(\d+)'
if ($verMatch) {
    $major = [int]$Matches[1]; $minor = [int]$Matches[2]
    if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 11)) {
        Write-Fail "Python 3.11 or newer is required (found $pyVer)."
        exit 1
    }
}

# ── 2. Create virtual environment ────────────────────────────────────────────
$venvPy = $py
$venvPip = $py

if (-not $NoVenv) {
    Write-Step "Setting up virtual environment (.venv\)..."

    if (Test-Path ".venv\Scripts\python.exe") {
        Write-Ok "Virtual environment already exists — updating packages."
    } else {
        Invoke-Checked $py @("-m", "venv", ".venv") "venv creation"
        Write-Ok "Virtual environment created."
    }

    $venvPy  = ".venv\Scripts\python.exe"
    $venvPip = ".venv\Scripts\pip.exe"
} else {
    Write-Warn "Skipping venv — installing into system Python."
}

# ── 3. Upgrade pip ───────────────────────────────────────────────────────────
Write-Step "Upgrading pip..."
Invoke-Checked $venvPy @("-m", "pip", "install", "--upgrade", "pip", "--quiet") "pip upgrade"
Write-Ok "pip up to date."

# ── 4. Install requirements ───────────────────────────────────────────────────
Write-Step "Installing from requirements.txt..."
Invoke-Checked $venvPy @("-m", "pip", "install", "-r", "requirements.txt") "requirements install"
Write-Ok "All packages installed."

# ── 5. Verify installation ────────────────────────────────────────────────────
Write-Step "Verifying packages..."

$checks = [ordered]@{
    "PySide6"       = "import PySide6; print(PySide6.__version__)"
    "pyqtgraph"     = "import pyqtgraph; print(pyqtgraph.__version__)"
    "numpy"         = "import numpy; print(numpy.__version__)"
    "PyOpenGL"      = "import OpenGL; print('OK')"
    "pyserial"      = "import serial; print(serial.__version__)"
    "backend pkg"   = "import sys; sys.path.insert(0,'.'); import backend; print('OK')"
}

$allOk = $true
foreach ($pkg in $checks.Keys) {
    try {
        $out = (& $venvPy -c $checks[$pkg] 2>&1).ToString().Trim()
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "$pkg  ($out)"
        } else {
            Write-Warn "$pkg not available  ($out)"
            if ($pkg -in @("PySide6","pyqtgraph","numpy")) { $allOk = $false }
        }
    } catch {
        Write-Warn "$pkg check raised exception"
        if ($pkg -in @("PySide6","pyqtgraph","numpy")) { $allOk = $false }
    }
}

# ── 6. Summary ────────────────────────────────────────────────────────────────
Write-Host ""
if ($allOk) {
    Write-Host "  =============================================================" -ForegroundColor Green
    Write-Host "   Setup complete!  Launch the app: double-click run_app.bat" -ForegroundColor Green
    Write-Host "  =============================================================" -ForegroundColor Green
} else {
    Write-Host "  =============================================================" -ForegroundColor Yellow
    Write-Host "   Setup finished with warnings.  Check messages above." -ForegroundColor Yellow
    Write-Host "   Some optional features may not be available." -ForegroundColor Yellow
    Write-Host "  =============================================================" -ForegroundColor Yellow
}
Write-Host ""
