# bringup/flash_and_monitor.ps1 — Windows Flash + Monitor Script
# ================================================================
# PowerShell equivalent of flash_and_monitor.sh for Windows hosts.
# Requires: ESP-IDF installed via Windows installer (adds idf.py to PATH
# after sourcing "ESP-IDF PowerShell" shortcut or running export.ps1).
#
# Usage (in ESP-IDF PowerShell or after sourcing export.ps1):
#   .\flash_and_monitor.ps1
#   .\flash_and_monitor.ps1 -Port COM3
#   .\flash_and_monitor.ps1 -Port COM3 -NoBuild -LogDir C:\logs
#
# After flash: UART1 telemetry (921600, GPIO 17/16) on SEPARATE port.
# Run in a second PowerShell window:
#   python bringup\verify_bringup.py --port COM4 --soak-minutes 10

[CmdletBinding()]
param(
    [string] $Port         = "",
    [string] $LogDir       = "",
    [int]    $MonitorBaud  = 115200,
    [switch] $NoBuild,
    [switch] $NoFlash,
    [switch] $Help
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── constants ─────────────────────────────────────────────────────────
$SCRIPT_DIR    = Split-Path -Parent $MyInvocation.MyCommand.Path
$PROJECT_ROOT  = Split-Path -Parent $SCRIPT_DIR
$FIRMWARE_DIR  = Join-Path $PROJECT_ROOT "faz19b_sensors\faz19b_sensors\faz19b_sensors"
$BINARY        = Join-Path $FIRMWARE_DIR "build\filament_winding_telem.bin"
$SDKCONFIG     = Join-Path $FIRMWARE_DIR "sdkconfig"
$REQ_MAJOR     = 5
$REQ_MINOR     = 3
$REQ_TARGET    = "esp32"

# ── colour helpers ────────────────────────────────────────────────────
function Write-Ok   { param($m) Write-Host "  [PASS] $m" -ForegroundColor Green }
function Write-Warn { param($m) Write-Host "  [WARN] $m" -ForegroundColor Yellow }
function Write-Err  { param($m) Write-Host "  [FAIL] $m" -ForegroundColor Red }
function Write-Info { param($m) Write-Host "  [INFO] $m" -ForegroundColor Cyan }

if ($Help) {
    Get-Content $MyInvocation.MyCommand.Path | Select-String "^#" | ForEach-Object { $_.Line -replace '^# ?','' } | Select-Object -First 20
    exit 0
}

# ── header ────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "==========================================================" -ForegroundColor White
Write-Host " Filament Winding Telemetry -- Flash + Monitor (Windows)  " -ForegroundColor White
Write-Host " $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')                " -ForegroundColor White
Write-Host "==========================================================" -ForegroundColor White
Write-Host ""

# ── [1/6] ESP-IDF Environment ─────────────────────────────────────────
Write-Host "[1/6] ESP-IDF Environment" -ForegroundColor White
$idfpy = Get-Command idf.py -ErrorAction SilentlyContinue
if (-not $idfpy) {
    Write-Err "idf.py not found in PATH."
    Write-Host "       Open 'ESP-IDF PowerShell' shortcut, or run:"
    Write-Host "       . `"$env:USERPROFILE\esp\esp-idf\export.ps1`""
    exit 1
}

$idfVerRaw = (idf.py --version 2>&1) | Select-String -Pattern 'v(\d+)\.(\d+)' | ForEach-Object { $_.Matches[0].Value }
if ($idfVerRaw -match 'v(\d+)\.(\d+)') {
    $major = [int]$Matches[1]
    $minor = [int]$Matches[2]
    if ($major -gt $REQ_MAJOR -or ($major -eq $REQ_MAJOR -and $minor -ge $REQ_MINOR)) {
        Write-Ok "ESP-IDF $idfVerRaw (required >= v$REQ_MAJOR.$REQ_MINOR)"
    } else {
        Write-Err "ESP-IDF $idfVerRaw is older than required v$REQ_MAJOR.$REQ_MINOR"
        exit 1
    }
} else {
    Write-Warn "Cannot parse ESP-IDF version: $idfVerRaw"
}

# Check sdkconfig target
if (Test-Path $SDKCONFIG) {
    $targetLine = Get-Content $SDKCONFIG | Where-Object { $_ -match '^CONFIG_IDF_TARGET=' }
    if ($targetLine -match '=(.+)') {
        $builtTarget = $Matches[1].Trim('"')
        if ($builtTarget -eq $REQ_TARGET) {
            Write-Ok "Build target: $builtTarget"
        } else {
            Write-Err "Build target '$builtTarget' != required '$REQ_TARGET'"
            Write-Host "       Fix: idf.py set-target $REQ_TARGET"
            exit 1
        }
    }
} else {
    Write-Warn "No sdkconfig found -- run 'idf.py set-target $REQ_TARGET' first"
}

# ── [2/6] Build ───────────────────────────────────────────────────────
Write-Host ""
Write-Host "[2/6] Build" -ForegroundColor White
if (-not $NoBuild) {
    Write-Info "Running idf.py build in $FIRMWARE_DIR"
    Push-Location $FIRMWARE_DIR
    try {
        idf.py build
        if ($LASTEXITCODE -ne 0) { Write-Err "Build failed"; exit 1 }
        Write-Ok "Build succeeded"
    } finally {
        Pop-Location
    }
} else {
    Write-Info "Build skipped (-NoBuild)"
}

# ── [3/6] Binary Verification ─────────────────────────────────────────
Write-Host ""
Write-Host "[3/6] Binary Verification" -ForegroundColor White

if (-not (Test-Path $BINARY)) {
    Write-Err "Binary not found: $BINARY"
    exit 1
}

$binaryItem = Get-Item $BINARY
$binaryKB   = [math]::Round($binaryItem.Length / 1024)
Write-Ok "Binary: $BINARY ($binaryKB KB)"

$ageH = [math]::Floor(((Get-Date) - $binaryItem.LastWriteTime).TotalHours)
if ($ageH -gt 24) {
    Write-Warn "Binary is $ageH hours old -- consider rebuilding"
} else {
    Write-Ok "Binary age: $ageH hours"
}

$ptable = Join-Path $FIRMWARE_DIR "build\partition_table\partition-table.bin"
if (Test-Path $ptable) {
    $ptKB = [math]::Round((Get-Item $ptable).Length / 1024)
    Write-Ok "Partition table found ($ptKB KB)"
} else {
    Write-Warn "Partition table binary not found"
}

if ($binaryItem.Length -gt 1MB) {
    Write-Err "Binary $binaryKB KB exceeds 1024 KB app partition limit"
    exit 1
}

# ── [4/6] Port Detection ──────────────────────────────────────────────
Write-Host ""
Write-Host "[4/6] Port Detection" -ForegroundColor White

if (-not $Port) {
    # Enumerate COM ports and find likely ESP32 (Silicon Labs or CH340)
    $comPorts = Get-WmiObject Win32_PnPEntity -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match 'COM\d' -and
                       ($_.Name -match 'Silicon Labs|CH340|CP210|USB-SERIAL|UART') } |
        ForEach-Object { ($_.Name | Select-String -Pattern 'COM\d+').Matches[0].Value } |
        Select-Object -First 1

    if ($comPorts) {
        $Port = $comPorts
        Write-Ok "Autodetected port: $Port"
    } else {
        # Fall back to listing all COM ports
        $allCom = Get-WmiObject Win32_SerialPort |
            Select-Object -ExpandProperty DeviceID | Select-Object -First 1
        if ($allCom) {
            $Port = $allCom
            Write-Warn "No recognized ESP32 adapter found; using first COM port: $Port"
        } else {
            Write-Err "No COM ports found. Connect USB-serial adapter."
            Write-Host "       Device Manager > Ports (COM & LPT) to identify port"
            exit 1
        }
    }
}

Write-Ok "UART0 monitor port: $Port @ $MonitorBaud baud"
Write-Info "UART1 telemetry (921600, GPIO 17/16) must be on a SEPARATE port"

# ── [5/6] Log Directory ───────────────────────────────────────────────
Write-Host ""
Write-Host "[5/6] Log Directory" -ForegroundColor White

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
if (-not $LogDir) {
    $LogDir = Join-Path $SCRIPT_DIR "logs\$ts"
}
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile      = Join-Path $LogDir "monitor_$ts.log"
$AnalysisFile = Join-Path $LogDir "boot_analysis_$ts.txt"
Write-Ok "Log directory: $LogDir"
Write-Ok "Monitor log:   $LogFile"

# ── [6/6] Flash + Monitor ─────────────────────────────────────────────
Write-Host ""
Write-Host "[6/6] Flash + Monitor" -ForegroundColor White
Write-Host ""

if (-not $NoFlash) {
    Write-Host "  About to flash: $BINARY" -ForegroundColor Green
    Write-Host "  to port:        $Port" -ForegroundColor Green
    Write-Host ""
    $confirm = Read-Host "  Proceed? [y/N]"
    if ($confirm -notmatch '^[Yy]') { Write-Host "  Aborted."; exit 0 }
    Write-Host ""
    Write-Info "Flashing..."
    Push-Location $FIRMWARE_DIR
    try {
        idf.py -p $Port flash
        if ($LASTEXITCODE -ne 0) { Write-Err "Flash failed"; exit 1 }
    } finally {
        Pop-Location
    }
    Write-Ok "Flash complete"
    Write-Host ""
}

Write-Info "Starting monitor -- log: $LogFile"
Write-Info "Press Ctrl+] to exit monitor"
Write-Host ""
Write-Host "--- UART0 BOOT LOG ---" -ForegroundColor Yellow

# Run monitor and tee to log file
Push-Location $FIRMWARE_DIR
try {
    idf.py -p $Port --baud $MonitorBaud monitor | Tee-Object -FilePath $LogFile
} catch {
    # Monitor exits via Ctrl+] — that's expected
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "--- END OF MONITOR SESSION ---" -ForegroundColor Yellow
Write-Host ""

# ── Post-session analysis ─────────────────────────────────────────────
Write-Host "Post-session analysis..." -ForegroundColor White

if (-not (Test-Path $LogFile)) {
    Write-Warn "Log file not found -- monitor may not have written output"
    exit 0
}

$logContent = Get-Content $LogFile -Raw -ErrorAction SilentlyContinue
if (-not $logContent) { $logContent = "" }

$analysis = @("Boot log analysis: $LogFile", "Generated: $(Get-Date)", "=" * 50, "")
$fatal = 0; $warnings = 0

# Boot loop detection
$rstCount = ([regex]::Matches($logContent, 'rst:0x')).Count
if ($rstCount -gt 2) {
    $fatal++
    Write-Err "BOOT LOOP DETECTED: $rstCount reset events"
    $analysis += "BOOT LOOP: $rstCount reset events"
}

# Guru Meditation
if ($logContent -match 'Guru Meditation Error') {
    $fatal++
    Write-Err "GURU MEDITATION detected"
    $analysis += "FATAL: Guru Meditation Error"
}

# Brownout
if ($logContent -imatch 'brownout') {
    $warnings++
    Write-Warn "BROWNOUT detected in boot log"
    $analysis += "WARN: Brownout detected"
}

# Watchdog
if ($logContent -imatch 'WDT|watchdog|TG0WDT|TWDT') {
    $warnings++
    Write-Warn "WATCHDOG reset detected"
    $analysis += "WARN: Watchdog reset"
}

# Positive checks
if ($logContent -match 'Filament Winding Telemetry') {
    Write-Ok "Boot banner found"
    $analysis += "PASS: Boot banner"
} else {
    Write-Warn "Boot banner NOT found"
    $analysis += "FAIL: Boot banner missing"
}

if ($logContent -match 'Reset reason: (\w+)') {
    Write-Ok "Reset reason: $($Matches[1])"
    $analysis += "PASS: Reset reason: $($Matches[1])"
}

if ($logContent -match 'telemetry task started') {
    Write-Ok "Telemetry task started"
    $analysis += "PASS: telemetry task started"
} else {
    Write-Warn "Telemetry task start not confirmed"
    $analysis += "WARN: telemetry task not found"
}

# Save analysis
$analysis | Out-File -FilePath $AnalysisFile -Encoding utf8

# ── Verdict ───────────────────────────────────────────────────────────
Write-Host ""
Write-Host "==========================================================" -ForegroundColor White
Write-Host " POST-SESSION VERDICT" -ForegroundColor White
Write-Host "==========================================================" -ForegroundColor White

if ($fatal -gt 0) {
    Write-Host "  STOP -- UNSAFE: fatal events detected" -ForegroundColor Red
    Write-Host "  See: $AnalysisFile" -ForegroundColor Red
} elseif ($warnings -gt 0) {
    Write-Host "  CAUTION: warning events -- review before soak" -ForegroundColor Yellow
    Write-Host "  See: $AnalysisFile" -ForegroundColor Yellow
} else {
    Write-Host "  PASS: boot log clean -- proceed to verify_bringup.py" -ForegroundColor Green
}

Write-Host ""
Write-Host "  Log:      $LogFile" -ForegroundColor Cyan
Write-Host "  Analysis: $AnalysisFile" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Next step (UART1 telemetry, second window):" -ForegroundColor Cyan
Write-Host "  python bringup\verify_bringup.py --port COM4 --soak-minutes 10" -ForegroundColor Cyan
Write-Host ""
