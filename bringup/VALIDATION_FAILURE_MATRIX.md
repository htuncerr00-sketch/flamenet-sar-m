# VALIDATION FAILURE INJECTION MATRIX
## Commissioning Toolchain Dry-Run — Test Results
**Date:** 2026-05-28  
**Harness:** `bringup/test_toolchain.py`  
**Mock source:** `bringup/mock_frame_generator.py` + `bringup/mock_esp32_serial.py`  
**Final result: 14/14 PASS**

Evidence labels: (host-sim) = executed on PTY, (analytical) = code inspection, (inferred) = logical deduction

---

## Matrix

| Test | Injected Failure | Expected Detector | Observed Detector | Result | False-Positive Risk | False-Negative Risk |
|---|---|---|---|---|---|---|
| **T01** | None (happy path) | All phases PASS | All phases PASS, overall_pass=True | **PASS** | Low — freeze=2 on vib_y/vib_z (PTY artifact, L2) | None observed |
| **T02** | 10% CRC corruption | P4 FAIL, n_crc_errors > 0 | P4 FAIL, n_crc_errors=2992/~30k | **PASS** | None | None — CRC error immediately detectable at 10% rate |
| **T03** | 10% desync (garbage prepend) | n_sync_drops > 0, delivery may stay >99% | n_sync_drops=13648 bytes, P4 PASS (99.65%) | **PASS** | None | LOW — at low desync rates (<1%) sync_drops may not exceed threshold in short window |
| **T04** | F_WATCHDOG_RST flag in all frames | WARN in P1 output | WARN logged, P1 PASS (watchdog is warning, not FAIL) | **PASS** | None | MEDIUM — toolchain does not fail P1 on watchdog; operator must read WARN manually |
| **T05** | F_BROWNOUT flag in all frames | WARN in P1 output | WARN logged, P1 PASS (brownout is warning, not FAIL) | **PASS** | None | MEDIUM — same as T04; requires operator to act on WARN |
| **T06** | F_SAFE_HALT flag in all frames | P3 FAIL (safe_halt_count > 0) | P3 FAIL, safe_halt_count=30008, STOP verdict | **PASS** | None | None — any F_SAFE_HALT causes immediate P3 FAIL |
| **T07** | Thermal freeze (temp_K identical for 200+ frames, flag stays SET) | freeze_events > 0 | freeze=3 (temp_K + vib_y + vib_z), WARN printed | **PASS** | HIGH — vib_y, vib_z fire freeze even without injection (see L2) | None — temp_K freeze reliably detected |
| **T08** | 60% delivery fraction | P4 FAIL (delivery_pct < 99%) | P4 FAIL, delivery=59.75%, STOP verdict | **PASS** | None | LOW — at rates just below 99% (e.g., 98.5%) P4 might or might not FAIL depending on timing window |
| **T09** | No frames emitted at all | P1 timeout (FAIL after 10 s), overall_pass=False | P1 FAIL, STOP verdict, overall_pass=False | **PASS** (was FAIL before Bug B5 fix) | None | None — 10 s P1 window is deterministic |
| **T10** | INA226 flag clear (F_INA226_OK=0) for entire 30s window | P2 FAIL (INA226_OK < 98%) | P2 FAIL, INA226_OK=0.0%, STOP verdict | **PASS** | None | LOW — a transient INA226 drop that resolves within the window would not fail P2 (98% threshold) |
| **T11** | Clean boot log (UART0) | boot_banner, telem_task, sensor_task all seen | All three flags set, verdict OK | **PASS** | None | LOW — if boot log arrives faster than serial_capture's 50ms poll the events might be batched; partial-line handling prevents loss |
| **T12** | Guru Meditation in UART0 at t=2s | guru_meditation_detected=True, STOP verdict | Guru Meditation detected, verdict STOP | **PASS** | None | LOW — pattern requires exact substring "Guru Meditation Error"; truncated line would miss it |
| **T13** | Happy path — JSON report validity | All required keys present, JSON parseable | `json.loads()` succeeds, all keys present | **PASS** | None | None — missing key causes KeyError in check |
| **T14** | Happy path — markdown report validity | Required section headers present | All section headings found in output | **PASS** | None | None — section header check is substring match |

---

## Detector Mapping

| Signal | Detector Layer | Phase | Threshold | Evidence |
|---|---|---|---|---|
| CRC corruption | `FrameParser.n_crc_errors` | P4 wire integrity | >0 causes FAIL | (host-sim) T02 |
| Frame desync | `FrameParser.n_sync_drops` | P4 (soft warn only) | No hard threshold — reported in output | (host-sim) T03 |
| P1 timeout (no frames) | `time.monotonic()` in verify loop | P1 first frame | >10 s → FAIL | (host-sim) T09 |
| F_SAFE_HALT | Flags bitmask check | P3 plausibility | Any SAFE_HALT frame → FAIL | (host-sim) T06 |
| F_WATCHDOG_RST | Flags bitmask check | P1 warn | Warning only, not FAIL | (host-sim) T04 |
| F_BROWNOUT | Flags bitmask check | P1 warn | Warning only, not FAIL | (host-sim) T05 |
| Sensor flag % | Per-frame INA/IMU/THERMAL flag counters | P2 sensor flags | <98% uptime → FAIL | (host-sim) T10 |
| Delivery rate | `n_frames / expected` | P4 wire integrity | <99% → FAIL | (host-sim) T08 |
| Sensor freeze | `SensorFreezeDetector` deque(200) | WARN (soft) | 200 identical values → event logged | (host-sim) T07 |
| Guru Meditation | `serial_capture` regex `r"Guru Meditation"` | SessionStats flag | Any match → `guru_meditation=True`, verdict STOP | (host-sim) T12 |
| Brownout log | `serial_capture` regex `r"brownout|BROWNOUT"` | SessionStats counter | Increments `brownout_count` | (analytical) |
| Watchdog log | `serial_capture` regex `r"WDT|watchdog|WATCHDOG"` | SessionStats counter | Increments `watchdog_count` | (analytical) |

---

## Unvalidated Failure Modes (require real hardware)

| Failure Mode | Why Unvalidatable in PTY Sim | Risk Level |
|---|---|---|
| Real CRC mismatch from bit-flip on UART line | PTY is lossless; real UART has EMI | MEDIUM |
| Partial packet spanning multiple OS `read()` calls | Partial packet handling tested in RealESP32Link; PTY never splits a 66-byte frame | MEDIUM |
| Baud rate mismatch (921600 vs actual oscillator) | PTY is exact; real crystal tolerance ±100 ppm | LOW |
| USB-UART chip buffer overflow at sustained 1 kHz | PTY has no USB layer | LOW |
| I²C stuck bus (SCL held low) | Not in wire frames; detected only by flag absence | MEDIUM |
| UART0 garbled boot log from ESP32 boot ROM noise | MockBootLog emits clean lines; real boot has noise before log init | LOW |
| Power-cycle during flash | Not simulatable | HIGH — brick risk |
| Sensor reading drift from temperature | Mock uses fixed Gaussian noise; real drift is systematic | LOW |

---

## Known False-Positive Risks

| Detector | False-Positive Condition | Observed in Dry-Run | Mitigation |
|---|---|---|---|
| `SensorFreezeDetector` on vib_y, vib_z | Board at rest; axes near-constant for 200+ frames | YES (freeze=2 in T01, T02, T04–T10) — PTY artifact | Document: freeze with flag SET = operational note, not fault; freeze with flag CLEAR = investigate |
| Jitter WARN | Python/PTY jitter σ typically 50–200 µs; threshold is σ < 100 µs | YES (T03, T04, T07, T08) | Jitter WARN is soft; only fails commissioning if combined with P4 FAIL |
| Delivery < 99% | Single OS scheduler stall during 30s window could drop frames | Not observed in clean runs, but possible under load | Rerun verify_bringup if marginal (98–99%) |

---

## Known False-Negative Risks

| Detector | False-Negative Condition | Risk Level | Mitigation |
|---|---|---|---|
| P2 sensor flag (98% threshold) | Sensor drops for <2% of 30s window passes P2 | LOW | 30-min soak with per-minute trending catches intermittent faults |
| F_WATCHDOG_RST / F_BROWNOUT not causing FAIL | These set a WARN only in P1; operator could miss it | MEDIUM | Check output for "WARN WATCHDOG_RESET" / "WARN BROWNOUT" lines; treat as STOP in real commissioning |
| Guru Meditation pattern miss | Pattern requires full "Guru Meditation Error" substring; truncation at 10 MB log rotation boundary | LOW | Log rotation preserves complete lines; truncation at boundary is between lines |
| serial_capture reconnect | PTY reconnect not tested; auto-reconnect logic relies on serial.SerialException on port drop | LOW | Test with physical cable unplug during commissioning |
