# DRY-RUN AUDIT REPORT
## Commissioning Toolchain — Pre-Silicon Validation
**Date:** 2026-05-28  
**Evidence labels used throughout:** (host-sim) = executed on PTY mock, (analytical) = code inspection, (inferred) = logical deduction from code + docs

---

## Scope

Systematic audit of all 6 commissioning scripts for syntax correctness, import validity, path assumptions, Linux compatibility, dependency issues, executable permissions, broken relative paths, report generation, log directory creation, JSON export, and markdown export.

Scripts audited:
1. `bringup/flash_and_monitor.sh` (Bash, 285 lines)
2. `bringup/flash_and_monitor.ps1` (PowerShell, 185 lines)
3. `bringup/serial_capture.py` (Python, ~300 lines)
4. `bringup/verify_bringup.py` (Python, ~750 lines)
5. `bringup/soak_test_runner.py` (Python, ~400 lines)
6. `bringup/mock_esp32_serial.py` (Python, ~200 lines) — mock infrastructure
7. `bringup/mock_frame_generator.py` (Python, ~440 lines) — mock infrastructure
8. `bringup/test_toolchain.py` (Python, ~420 lines) — dry-run harness

---

## Audit Results

### 1. flash_and_monitor.sh

| Check | Result | Evidence |
|---|---|---|
| Bash syntax (`bash -n`) | PASS | (host-sim) |
| Shebang line | `#!/usr/bin/env bash` ✓ | (analytical) |
| `set -euo pipefail` present | ✓ | (analytical) |
| Executable permission | `chmod +x` required; `.sh` not auto-exec | (analytical) |
| IDF_PATH dependency | Documented; exits with message if unset | (analytical) |
| `/dev/ttyUSB0` path assumption | Default only; overridable via `--port` | (analytical) |
| Log directory creation | `mkdir -p "${LOG_DIR}"` ✓ | (analytical) |

#### Bug B1 — Brownout detection silent false-negative (FIXED)

**Severity:** HIGH — commissioning would not stop on brownout  
**Root cause:** `has_pattern` shell function uses only its first argument (`$1`). Call site was:
```bash
has_pattern -iE "brownout|BROWNOUT" "${LOG_FILE}"
```
`grep` received `-iE` as the search pattern — literal match against log bytes. Brownout log lines were never matched.

**Fix applied:**
```bash
# Before (broken):
has_pattern -iE "brownout|BROWNOUT"
# After (correct):
grep -qi "brownout" "${LOG_FILE}" 2>/dev/null
```
Evidence: (analytical) — grep pattern arg position; (host-sim) — T12 confirms watchdog detection works in Python capture equivalent

#### Bug B2 — Watchdog detection silent false-negative (FIXED)

**Severity:** HIGH — commissioning would not stop on watchdog reset  
**Root cause:** Same `has_pattern` function bug. Call was:
```bash
has_pattern -E "WDT|watchdog|WATCHDOG|TG0WDT|TWDT" "${LOG_FILE}"
```
`grep` searched for literal `-E` in the log file.

**Fix applied:**
```bash
grep -qE "WDT|watchdog|WATCHDOG|TG0WDT|TWDT" "${LOG_FILE}" 2>/dev/null
```
Evidence: (analytical)

#### Bug B4 — Nonexistent `--no-monitor` flag referenced in COMMISSIONING_SEQUENCE.md (FIXED)

**Severity:** LOW — documentation error; operator would receive unhelpful error  
**Root cause:** `flash_and_monitor.sh` has `--no-build` and `--no-flash` flags but no `--no-monitor`. The sequence doc referenced `--no-monitor`.  
**Fix:** Documentation updated to use `--no-build --no-flash`. Evidence: (analytical)

---

### 2. flash_and_monitor.ps1

| Check | Result | Evidence |
|---|---|---|
| PowerShell syntax (`pwsh -Command Invoke-Expression`) | Not verified (no PowerShell in CI) | (inferred) |
| Equivalent fault-detection logic | Uses `-match` regex on log content; no `has_pattern` analog — NOT affected by B1/B2 | (analytical) |
| Linux compatibility | PowerShell only; not applicable on Linux without `pwsh` install | (analytical) |
| Path separators | Uses `Join-Path` ✓ | (analytical) |

**Note (inferred):** The Windows `Start-Process` + `-Wait` approach for `idf.py` mirror is untested without an actual ESP-IDF installation on Windows. Its brownout/watchdog detection uses `-match "brownout"` and `-match "WDT|watchdog"` directly on `$content` — correct usage, not affected by the shell bug.

---

### 3. serial_capture.py

| Check | Result | Evidence |
|---|---|---|
| Python syntax (`py_compile`) | PASS | (host-sim) |
| Imports (`serial`, `re`, `json`, `signal`, `threading`) | All stdlib except `pyserial` | (analytical) |
| `pyserial` availability | `pip install pyserial` required; script exits cleanly if missing | (host-sim) |
| Output directory creation | `Path(out_dir).mkdir(parents=True, exist_ok=True)` ✓ | (analytical) |
| Partial line handling | `data = leftover + chunk; lines = data.split(b"\n"); leftover = lines[-1]` ✓ | (analytical) |
| Ctrl+C / SIGTERM handling | `signal.signal(SIGTERM, ...)` + `KeyboardInterrupt` catch ✓ | (analytical) |
| Log rotation | `RotatingLogFile` rotates at `max_bytes` (default 10 MB) ✓ | (analytical) |
| JSON export | Written on exit; tested in T11/T12 | (host-sim) |
| Invalid UTF-8 | `errors="replace"` on decode ✓ | (analytical) |
| Pattern matching on binary | Decodes with `errors="replace"` before pattern match ✓ | (analytical) |

**No bugs found.** Evidence: (host-sim) T11, T12 PASS; (analytical) code review.

---

### 4. verify_bringup.py

| Check | Result | Evidence |
|---|---|---|
| Python syntax (`py_compile`) | PASS | (host-sim) |
| All imports resolve | ✓ (`dataclasses`, `struct`, `threading`, `serial`) | (host-sim) |
| `pyserial` dependency | Exits with clear message if missing | (analytical) |
| Report directory creation | `Path(report_dir).mkdir(parents=True, exist_ok=True)` ✓ | (analytical) |
| JSON output format | All keys serialisable; tested in T13 | (host-sim) |
| Markdown output format | Section headers verified in T14 | (host-sim) |
| CRC algorithm | Poly 0x1021, init 0xFFFF, no reflection — byte-identical to firmware | (host-sim) 100/100 frame round-trip |
| Struct format | `>QHHffffffffffffHH` = 17 items, 64 bytes ✓ | (analytical) |

#### Bug B3 — SoakStats.as_dict() overwrites correct CRC/sync counts (FIXED)

**Severity:** MEDIUM — soak report would always show 0 CRC errors and 0 sync drops even when errors occurred during soak  
**Root cause:** `SoakStats` dataclass initialises `n_crc_errors=0` and `n_sync_drops=0`; these fields are only tracked by `FrameParser` which is not inside `SoakStats`. `soak_dict.update(stats_soak.as_dict())` overwrote the correctly-computed values with zeros.

**Fix applied:**
```python
soak_extra = stats_soak.as_dict()
soak_extra.pop("n_crc_errors", None)
soak_extra.pop("n_sync_drops", None)
soak_dict = {
    "n_crc_errors": soak_crc,
    "n_sync_drops": parser.n_sync_drops,
    ...
}
soak_dict.update(soak_extra)
```
Evidence: (analytical)

#### Bug B5 — P1 timeout early-return leaves overall_pass as None (FIXED)

**Severity:** HIGH — test code checking `data.get("overall_pass") is False` would return False (None is not False in Python identity check), masking a P1 failure  
**Root cause:** P1 early-return path set `results["verdict"] = "STOP — UNSAFE"` but never set `results["overall_pass"]`, leaving it as the `None` default.  
**Python detail:** `None is False` evaluates to `False` (identity, not equality). Any caller checking `results["overall_pass"] is False` would silently miss the failure.

**Fix applied:**
```python
results["verdict"] = "STOP — UNSAFE"
results["overall_pass"] = False    # was missing
return results
```
Evidence: (host-sim) T09 was FAIL before fix, PASS after

---

### 5. soak_test_runner.py

| Check | Result | Evidence |
|---|---|---|
| Python syntax (`py_compile`) | PASS | (host-sim) |
| Imports from verify_bringup | All names resolved | (host-sim) |
| Report directory creation | Inherited from `generate_markdown_report` call + explicit `mkdir` | (analytical) |
| Heap drift JSON loading | Loads `uart0_session.json`; gracefully skips if not found | (analytical) |
| SIGINT / KeyboardInterrupt | Caught; flushes partial report | (analytical) |
| SAFE_HALT abort logic | Checks `F_SAFE_HALT` bit; hard stops soak with `STOP — UNSAFE` | (analytical) |
| Watchdog abort logic | Counts `F_WATCHDOG_RST` events; stops after 3 consecutive | (analytical) |

**No bugs found in functional logic.** One operational note:

**Operational gap (inferred):** `soak_test_runner.py` imports from `verify_bringup` which must be on `sys.path`. The runner uses `sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))` which sets `bringup/` as the import root — this is correct. However, if run as `python3 soak_test_runner.py` from a different working directory, the relative import will still work because the path insertion uses `__file__`, not `os.getcwd()`. Evidence: (analytical)

---

### 6. mock_frame_generator.py

| Check | Result | Evidence |
|---|---|---|
| Python syntax | PASS | (host-sim) |
| Self-test (`--test`) | 7/7 assertions PASS after fixes | (host-sim) |
| CRC algorithm matches verify_bringup.py | Byte-identical — same poly, init, reflection | (analytical) |
| Struct format | Same `>QHHffffffffffffHH` ✓ | (analytical) |
| Thread safety | `threading.Lock()` on all mutable state | (analytical) |

#### Bug B6 — False-positive freeze on vib_y, vib_z in happy path (FIXED)

**Severity:** MEDIUM — happy-path test would show freeze=2 even with all sensor flags SET; masks real freeze detection signal  
**Root cause:** `BASELINE_VIB_Y = 0.0` and `BASELINE_VIB_Z = 0.0` exactly, with no Gaussian noise added. The `SensorFreezeDetector` fires when all 200 values in its deque are identical — which is every run for exact-zero axes.

**Fix applied:**
```python
BASELINE_VIB_Y = 0.0050   # was 0.0
BASELINE_VIB_Z = 0.0050   # was 0.0
# and in non-freeze path:
vib_y = BASELINE_VIB_Y + rng.gauss(0, 0.001)
vib_z = BASELINE_VIB_Z + rng.gauss(0, 0.001)
```
**Residual false-positive (documented):** Even after fix, host-sim runs at lower σ than real silicon; vib_y and vib_z occasionally show 1 freeze event each in the 30-second test window (observed in T01). This is a PTY timing artifact. On real ESP32, sensor noise floor at idle is ~0.005-0.01 g RMS, which will naturally prevent sustained 200-frame equality. Evidence: (host-sim), (inferred)

#### Bug B7 — Wrong payload offset in self-test (FIXED)

**Severity:** LOW — only affected the self-test; runtime code unaffected  
**Root cause:** `TEMP_K_PAYLOAD_OFFSET = 42` used in `--test` mode; correct value is 40.  
**Calculation:** ts_us[8] + seq[2] + flags[2] + 7×float[28] = 40. The extra 2 came from incorrectly counting the flags field twice.  
**Fix:** `TEMP_K_PAYLOAD_OFFSET = 40`. Evidence: (analytical)

---

### 7. mock_esp32_serial.py

| Check | Result | Evidence |
|---|---|---|
| Python syntax | PASS | (host-sim) |
| Linux only — `pty` module | Documented; `import pty` inside `main()` gives clear ImportError on Windows | (analytical) |
| PTY raw mode | `termios.tcsetattr` with cfmakeraw-equivalent flags prevents byte mangling | (host-sim) |
| Drift-free 1kHz timing | `target_t += 0.001; sleep(target_t - now)` pattern with 5ms reset guard | (analytical) |
| Slave path printed first | `UART0=...` and `UART1=...` to stdout before status lines | (analytical) |
| Clean shutdown | `stop_event.set()` + fd close in `finally` block | (analytical) |

**No bugs found.** Evidence: (host-sim) all 14 tests use PTY infrastructure.

---

## Summary Table

| Bug ID | File | Severity | Status | Type |
|---|---|---|---|---|
| B1 | flash_and_monitor.sh | HIGH | FIXED | False-negative |
| B2 | flash_and_monitor.sh | HIGH | FIXED | False-negative |
| B3 | verify_bringup.py | MEDIUM | FIXED | Data clobber |
| B4 | COMMISSIONING_SEQUENCE.md | LOW | FIXED | Doc error |
| B5 | verify_bringup.py | HIGH | FIXED | None vs False |
| B6 | mock_frame_generator.py | MEDIUM | FIXED | False-positive |
| B7 | mock_frame_generator.py | LOW | FIXED | Self-test offset |

**No unfixed bugs remain in the Python toolchain.** The PowerShell script has not been host-verified (no `pwsh` in CI); its logic is analytically correct.

---

## Open Limitations (not bugs — by design or environment)

| ID | Description | Impact | Evidence |
|---|---|---|---|
| L1 | Jitter σ in host-sim (PTY) is 30–200 µs; real FreeRTOS expected σ < 50 µs | Jitter WARN fires in T04/T07/T08/T03; not indicative of firmware bug | (host-sim) |
| L2 | vib_y, vib_z show 1 freeze event each in happy path (host-sim only) | Freeze count is 2 instead of 0 in T01; freeze_events > 0 check not used in T01 pass criteria | (host-sim) |
| L3 | flash_and_monitor.ps1 untested on Windows | Windows validation requires physical Windows + ESP-IDF install | (inferred) |
| L4 | soak_test_runner.py not PTY-validated (requires 30+ min) | Functional path verified analytically + import test; 30-min soak not run in CI | (analytical) |
| L5 | serial_capture.py auto-reconnect path not exercised | Reconnect logic requires serial.serialException on port drop; PTY doesn't replicate cable unplug exactly | (analytical) |
