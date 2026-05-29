# DIGITAL TWIN — SPRINT 4B REPORT
## Timing Correctness · Numerical Stability · Deterministic Playback

**Branch:** claude/amazing-feynman-XUXBf
**Date:** 2026-05-29
**Scope:** Timing/state-generation architecture only (per DIGITAL_TWIN_ARCHITECTURE.md).
No UI, no rendering, no new simulation domains.

**Verdict:** ★★★ READY ★★★ — all in-scope tasks complete, 41/41 new regression
assertions pass, two pre-existing twin bugs eliminated with measured numbers below.

---

## 1. Summary of Numbers (Before → After)

3-layer cylinder (Ø100 mm × 300 mm, α=55°, tow 6 mm), measured at the previously
failing configuration:

| Metric | Before (Sprint 4 / dt=0.1) | After (Sprint 4B / dt=1.0) |
|---|---|---|
| **max spindle RPM** | **45,819** (astronomical) | **4.2** (physical) |
| **max carriage lag** | **17,736 mm** (nonsensical) | **0.46 mm** (= v·τ bounded) |
| **state count** | **32,592** | **3,260** (10.0× fewer) |
| **spindle angle monotone** | NO (resets at layer boundary) | YES (global cumulative) |
| **NaN / Inf in states** | possible | none |
| **deterministic re-run** | not verified | bit-identical (verified) |

Test suite: **153 → 197 assertions**, **150 → 196 PASS**, **3 → 1 FAIL**.
The single remaining failure (`2 katman ≥ 1 katman kalınlık`) is a **pre-existing
deposition-physics issue** in `test_fiber_deposition`, confirmed failing on the
pre-Sprint-4B commit (`git stash` check), and is **out of scope** for this sprint
(timing/stability/determinism only).

---

## 2. Task-by-Task Results

### Task 1 — Fix RPM computation ✓

**Root cause:** Each layer's `WindingPath` restarts `a_deg` from 0. The old
`_build_timeline` concatenated layers without an angle offset, so the cumulative
spindle angle jumped *backward* at every layer boundary (e.g. 720,000° → 0°). The
RPM differential `|Δa/Δt|` then saw a huge negative `Δa`, and `abs()` turned it into
45,819 RPM.

**Fix (two parts):**
1. `_build_trajectory_segments()` (`winding_twin.py`) now carries a running
   `a_offset`, adding each layer's final cumulative angle to the next layer's
   segments. The global angle is strictly monotone — no boundary jump.
2. RPM is computed in `trajectory_builder.build_timeline()` exactly as specified:
   ```python
   spindle_v_deg_s = diff(a_deg) / dt
   rpm = abs(spindle_v_deg_s) / 360.0 * 60.0
   ```

**Verification:** `max_spindle_rpm` now ≤ machine limit (300 RPM); per-state RPM
re-derived from `(Δa/Δt)/360×60` matches `state.spindle_rpm` to 1e-6 across all
samples.

### Task 2 — Remove unstable PD eye-response ✓

**Root cause:** `simulate_eye_response()` ran a critically-damped PD tracker with
`kp=(2π·8)²≈2527` at dt=0.1 s. The discrete stability product `kp·dt²=25.3 ≫ 1`
caused numerical blow-up → 17,736 mm lag on a carriage that only travels 300 mm.

**Fix:** The PD simulation is **removed from the twin runtime path** (no longer
imported or called in `winding_twin.py`). Replaced with the first-order lag model:
```python
tau      = payout.eye_lag_time_const_s          # 0.03 s
lag_mm   = timeline.carriage_v_mm_s * tau        # signed, |lag| ≤ v_max·τ
x_actual = timeline.x_mm - lag_mm                # trails reference
```
Properties: **bounded** (|lag| ≤ peak_v·τ ≈ 0.46 mm), **numerically stable** (no
feedback loop), **deterministic** (pure arithmetic), **no oscillation** (first-order,
monotone in v).

`simulate_eye_response()` itself is **retained** in `payout_dynamics.py` for
standalone servo analysis (and its existing unit tests still pass), per
DIGITAL_TWIN_ARCHITECTURE.md §10 — it is simply never called inside the twin.

### Task 3 — Implement `trajectory_builder.py` ✓

New module `faz17_d1/.../core/trajectory_builder.py` (+ `backend/core/` re-export).

- `TrajectorySegment` — one timed linear segment (global cumulative angle).
- `TwinTimeline` — uniform-grid arrays + `index_at()` + diagnostics.
- `build_timeline(segments, dt_s=1.0, total_time_s=None)` — vectorized numpy
  sampler computing all required channels:
  - `x_mm(t)`, `a_deg(t)` (global cumulative), `rpm(t)`,
  - `carriage_v_mm_s(t)`, `spindle_v_deg_s(t)`, `fiber_mm(t)`.
- Fixed-dt support; deterministic `searchsorted`-based segment lookup; no RNG.

### Task 4 — Reduce state count (dt 0.1 → 1.0) ✓

`simulate_winding(..., dt_s=1.0)` is the new default (was 0.05). `dt_s` is a fully
configurable parameter on both `simulate_winding()` and `build_timeline()`.
Measured: dt=1.0 yields 3,260 states vs 32,592 at dt=0.1 — a 10.0× reduction.
`dt_s ≤ 0` raises `ValueError`.

### Task 5 — `state_at(t)` is O(1), no runtime physics ✓

`TwinSimulationResult.state_at(t)` and `TwinTimeline.index_at(t)` are pure
arithmetic: `idx = round(t/dt)` clamped to `[0, n-1]` — O(1), no search, no physics
recomputation. All physics (kinematics, lag, deposition) is computed once, offline,
during `simulate_winding()`. Playback (`simulation_playback.py`, unchanged) only
indexes the precomputed `states` list.

### Task 6 — Regression tests ✓

Added two test functions to `faz17_d1/tests/test_digital_twin.py` (41 assertions):

- `test_trajectory_builder()` — RPM formula on known segments, uniform dt, monotone
  global angle, signed velocities, `index_at` rounding/clamping, NaN-free arrays,
  configurable dt, empty/invalid input handling.
- `test_sprint4b_twin_timing()` — RPM correctness & bound, lag = v·τ & < 10 mm,
  no oscillatory divergence (deviation ≤ max_lag, position bounded), no NaN/Inf,
  bit-identical deterministic re-run, state-count scaling (dt½ → 2×, dt=1 → 10×
  fewer than dt=0.1, `floor(T/dt)+1`), O(1) `state_at`.

### Task 7 — This report ✓

---

## 3. Files Changed

| File | Change |
|---|---|
| `faz17_d1/.../core/trajectory_builder.py` | **NEW** — uniform time-grid builder |
| `backend/core/trajectory_builder.py` | **NEW** — re-export wrapper |
| `faz17_d1/.../core/winding_twin.py` | Rewrote timeline build (global angle offset), first-order lag, dt=1.0 default, removed PD call |
| `faz17_d1/tests/test_digital_twin.py` | +2 regression test functions (41 assertions) |

`payout_dynamics.py`, `simulation_playback.py`, and all other modules: **unchanged**.

---

## 4. Out-of-Scope Note

The remaining test failure `2 katman ≥ 1 katman kalınlık` lives in
`test_fiber_deposition` and concerns multi-layer thickness accumulation in
`fiber_deposition.simulate_deposition()` — a deposition-physics domain, not timing.
It was already failing before Sprint 4B (verified). Per the sprint boundary ("ONLY
timing correctness, numerical stability, deterministic playback; do NOT add new
simulation domains"), it is intentionally left for a future deposition-focused sprint.

---

*End of DIGITAL_TWIN_SPRINT4B_REPORT.md*
