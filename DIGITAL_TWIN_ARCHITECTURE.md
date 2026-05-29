# DIGITAL TWIN ARCHITECTURE
## Filament Winding CAM Platform — Physics Simulation Layer

**Status:** Architecture document — Sprint 4 Phase A  
**Date:** 2026-05-29  
**Branch:** claude/amazing-feynman-XUXBf  

---

## 1. Executive Summary

The digital twin is an **offline, pre-run physics simulation** of the winding machine. Given a `WindingPath`, it computes the full machine state history, fiber deposition map, and process quality metrics — producing a `TwinSimulationResult` that can be played back, inspected, and exported as a `ProductionReport`.

It is NOT a real-time control system. It is NOT a PID tuning environment. It is a deterministic trajectory integrator that answers: *"If this path is executed on this machine, what happens?"*

---

## 2. Known Bugs in Current Implementation

### Bug 1: RPM Astronomical Values (45,819 RPM)

**Location:** `winding_twin.py`, `TwinState.spindle_rpm`  
**Root cause:** `spindle_rpm` is computed from the raw rate of change of **cumulative** `a_deg`. Between adjacent timeline states the delta-degrees can be very large because `a_deg` is an unbounded accumulator (e.g. 720,000° after many circuits). If the dt used for differentiation is ever the simulation step (0.1 s) and a_deg jumps by hundreds of degrees between those states, RPM is hugely wrong.

**Correct formula:**
```
ω_deg_s = (a_deg[i] - a_deg[i-1]) / (t[i] - t[i-1])  [deg/s]
RPM     = |ω_deg_s| / 360.0 × 60.0                    [rev/min]
```
Expected range: `spindle_rpm` ∈ [0, `MachineEnvelope.max_spindle_rpm`] which is typically 10–300 RPM.

### Bug 2: Lag Error 17,736 mm (carriage only travels 300 mm)

**Location:** `payout_dynamics.py`, `simulate_eye_response()`  
**Root cause:** The PD controller is configured with `natural_freq_hz = 8.0 Hz`, giving:
```
kp = (2π × 8)² ≈ 2527 [1/s²]
kd = 2 × 2π × 8  ≈  100 [1/s]
```
This is a **servo control simulation** running at 10 Hz sampling (dt = 0.1 s) over a winding process that spans 3000 seconds. The PD controller is numerically unstable at these parameters with this step size: `kp × dt² = 2527 × 0.01 = 25.3 >> 1`, which violates the Nyquist stability criterion for discrete-time PD controllers.

**Correct model:** The digital twin does not need to simulate servo electronics. Carriage tracking lag in a properly tuned industrial servo is modeled as a first-order lag:

```
lag_mm(t) = v_carriage(t) × τ_lag
```

where `τ_lag` is the effective response time constant of the drive system (typically 0.01–0.05 s for a servo, giving lag of 0.8–4 mm at 80 mm/s). This is a **feedforward lag model**, not a closed-loop simulation.

`simulate_eye_response()` should be REMOVED from the twin simulation path. It belongs in a separate servo analysis tool, not in the winding timeline.

### Bug 3: Total Winding Time Validity Check

**Smoke test result:** 3259 s for 3-layer cylinder.  
**Back-of-envelope check:**  
- Fiber deposited: 87.9 m = 87,900 mm  
- Feed rate: 80 mm/s  
- Pure fiber time: 87,900 / 80 ≈ 1099 s  
- 3259 / 1099 ≈ 2.97× overhead from reversals + acceleration phases  

**Verdict:** Total time is plausible for a trajectory with many reversal dwell points. However, the dt = 0.1 s producing 32,592 states is too fine for offline pre-computation. The recommended approach is dt = 1.0 s (3259 states), which is sufficient for UI playback at any reasonable speed multiplier.

---

## 3. Architecture Overview

```
WindingPath (from path_generator)
    │
    ▼
┌─────────────────────────────────┐
│   TRAJECTORY LAYER              │  Pure kinematics — no physics yet
│   trajectory_builder.py        │
│   Inputs:  WindingPath          │
│   Outputs: TwinTimeline         │
│            - t_s[]              │  uniform time axis (dt = 1.0 s)
│            - x_mm[]             │  carriage reference position
│            - a_deg[]            │  cumulative spindle angle
│            - feed[]             │  instantaneous feed [mm/s]
│            - layer[], circuit[] │
│            - fiber_mm[]         │  cumulative fiber length
└─────────────────┬───────────────┘
                  │
                  ▼
┌─────────────────────────────────┐
│   DYNAMICS LAYER                │  First-order machine response
│   (inline in winding_twin.py)   │
│   Inputs:  TwinTimeline         │
│   Outputs: per-state fields     │
│                                 │
│   carriage_x_actual = x_ref     │  (servo assumed ideal for pre-run)
│       + v_carriage × τ_lag      │  first-order lag only
│   spindle_rpm = |dA/dt|/360×60  │  correct differential
│   eye_x = x_actual + lead       │  lead = standoff·tan(α_local)
│   eye_r = r_surface + standoff  │
│   lag_error = v × τ_lag         │  bounded, realistic
└─────────────────┬───────────────┘
                  │
                  ▼
┌─────────────────────────────────┐
│   DEPOSITION LAYER              │  Spatial fiber coverage
│   fiber_deposition.py           │
│   (existing, unchanged)         │
│   Inputs:  LayeredPath list     │
│   Outputs: DepositionMap        │
│            - thickness grid     │
│            - orientation grid   │
│            - coverage_pct       │
│            - uniformity index   │
└─────────────────┬───────────────┘
                  │
                  ▼
┌─────────────────────────────────┐
│   LAYER BUILDUP LAYER           │  Radial profile growth
│   layer_buildup.py              │
│   (existing, unchanged)         │
│   Inputs:  base MandrelProfile  │
│            FiberBand, n_layers  │
│   Outputs: LayeredPath[]        │
│            final profile        │
└─────────────────┬───────────────┘
                  │
                  ▼
┌─────────────────────────────────┐
│   RESULT LAYER                  │
│   winding_twin.py               │
│   TwinSimulationResult          │
│   - states: List[TwinState]     │  indexed by time step
│   - layered_paths               │
│   - final_deposition            │
│   - layer_time_ranges_s         │
│   - total_time_s                │
│   - max_lag_error_mm            │
│   - max_spindle_rpm             │
└─────────────────┬───────────────┘
                  │
                  ▼
┌─────────────────────────────────┐
│   PLAYBACK LAYER                │
│   simulation_playback.py        │
│   (existing, correct)           │
│   SimulationPlayback            │
│   - play/pause/seek/speed       │
│   - telemetry_snapshot()        │
└─────────────────────────────────┘
```

---

## 4. Module Inventory

### 4.1 Existing Modules — Status

| Module | Status | Action Required |
|---|---|---|
| `geometry_engine.py` | ✓ CORRECT | None |
| `path_generator.py` | ✓ CORRECT | None |
| `fiber_band.py` | ✓ CORRECT | None |
| `coverage_solver.py` | ✓ CORRECT | None |
| `geodesic_validator.py` | ✓ CORRECT | None |
| `payout_kinematics.py` | ✓ CORRECT (bug fixed Sprint 3) | None |
| `industrial_motion.py` | ✓ CORRECT | None |
| `manufacturability.py` | ✓ CORRECT | None |
| `machine_envelope.py` | ✓ CORRECT | None |
| `process_parameters.py` | ✓ CORRECT | None |
| `fiber_tension.py` | ✓ CORRECT | None |
| `layer_buildup.py` | ✓ CORRECT | None |
| `fiber_deposition.py` | ✓ CORRECT | None |
| `payout_dynamics.py` | ⚠ BUG — PD controller unsuitable | Remove `simulate_eye_response` from twin path; keep lag helper only |
| `winding_twin.py` | ✗ BUGS — RPM, lag, dt | Rewrite `simulate_winding()` |
| `production_report.py` | ✓ Mostly correct | Minor: wire in LayeredPath support |
| `simulation_playback.py` | ✓ CORRECT | None |

### 4.2 New Module Required

**`trajectory_builder.py`** — Converts `WindingPath` (list of `WindingPoint`) into a uniform time grid (`TwinTimeline`).

This module is missing and is the root cause of many downstream bugs. Without a clean time-domain trajectory, all kinematics computations are fragile.

---

## 5. Trajectory Builder — Specification

```python
# core/trajectory_builder.py

@dataclass
class TwinTimeline:
    """Uniform time-domain representation of a winding path."""
    t_s: np.ndarray           # (N,) uniform time axis, dt = 1.0 s
    x_ref_mm: np.ndarray      # (N,) carriage reference position [mm]
    a_cum_deg: np.ndarray     # (N,) cumulative spindle angle [deg]
    feed_mm_s: np.ndarray     # (N,) instantaneous combined feed [mm/s]
    layer: np.ndarray         # (N,) int — current layer index
    circuit: np.ndarray       # (N,) int — current circuit index
    fiber_cum_mm: np.ndarray  # (N,) cumulative fiber deposited [mm]
    alpha_deg: np.ndarray     # (N,) local winding angle [deg]
    dt_s: float               # time step (1.0 s)
    total_time_s: float
    n_layers: int
    n_circuits: int
```

**Algorithm:**
1. For each consecutive pair of `WindingPoint` objects `p[i], p[i+1]`:
   - `dx = p[i+1].x_mm - p[i].x_mm`
   - `da = p[i+1].a_deg - p[i].a_deg` (cumulative degrees, already monotone)
   - `da_arc = |da| × π/180 × r_avg` [mm, arc length on surface]
   - `ds = sqrt(dx² + da_arc²)` [mm, combined path length]
   - `dt_seg = ds / feed_mm_s` [s, time for this segment]
   - Accumulate: `t_end += dt_seg`, `fiber_cum += ds`

2. Build segment list: `[(t_start, t_end, WindingPoint, WindingPoint), ...]`

3. Generate uniform time grid: `t = np.arange(0, total_time + dt, dt)` with `dt = 1.0 s`

4. Interpolate all channels at each `t` using linear interpolation over the segment list

5. Compute `alpha_deg` at each t using local path tangent:
   ```
   alpha = arctan2(|da_arc_local|, |dx_local|)
   ```

**Key invariants:**
- `a_cum_deg` is strictly monotonically increasing (spindle never reverses)
- `x_ref_mm` oscillates between carriage limits (forward/reverse strokes)
- `feed_mm_s` drops to near zero at reversal points (deceleration)
- `fiber_cum_mm` is strictly non-decreasing

---

## 6. Winding Twin — Corrected Simulation Logic

`simulate_winding()` in `winding_twin.py` must be rewritten to:

```python
def simulate_winding(
    path: WindingPath,
    profile: MandrelProfile,
    band: FiberBand,
    dynamics_config: PayoutDynamicsConfig,
    dt_s: float = 1.0,
) -> TwinSimulationResult:

    # Step 1: Build uniform time-domain trajectory
    timeline = build_twin_timeline(path, profile, dt_s)

    # Step 2: Compute machine kinematics at each time step
    states = []
    for i, t in enumerate(timeline.t_s):
        x_ref = timeline.x_ref_mm[i]
        a_deg = timeline.a_cum_deg[i]
        feed  = timeline.feed_mm_s[i]
        alpha = timeline.alpha_deg[i]

        # Carriage velocity (central difference)
        if i == 0:
            v_x = 0.0
        elif i == len(timeline.t_s) - 1:
            v_x = (timeline.x_ref_mm[i] - timeline.x_ref_mm[i-1]) / dt_s
        else:
            v_x = (timeline.x_ref_mm[i+1] - timeline.x_ref_mm[i-1]) / (2 * dt_s)

        # Spindle RPM — correct formula
        if i == 0:
            omega_deg_s = 0.0
        else:
            omega_deg_s = (timeline.a_cum_deg[i] - timeline.a_cum_deg[i-1]) / dt_s
        spindle_rpm = abs(omega_deg_s) / 360.0 * 60.0  # expected: 10–300 RPM

        # Carriage lag — first-order model
        tau = dynamics_config.eye_lag_time_const_s
        lag_mm = v_x * tau  # positive = carriage behind reference

        x_actual = x_ref + lag_mm

        # Eye position
        r_surface = profile.radius_at(x_ref)
        lead_mm = dynamics_config.standoff_mm * math.tan(math.radians(
            max(1.0, min(89.0, alpha))
        ))
        eye_x = x_actual + lead_mm
        eye_r = r_surface + dynamics_config.standoff_mm

        # Contact point (under the eye, on the surface)
        contact_z = x_ref
        contact_r = r_surface

        state = TwinState(
            t_s=t,
            spindle_angle_deg=a_deg % 360.0,
            spindle_rpm=spindle_rpm,
            carriage_x_mm=x_ref,
            carriage_x_actual_mm=x_actual,
            carriage_v_mm_s=v_x,
            eye_x_mm=eye_x,
            eye_r_mm=eye_r,
            contact_z_mm=contact_z,
            contact_r_mm=contact_r,
            current_layer=int(timeline.layer[i]),
            current_circuit=int(timeline.circuit[i]),
            fiber_deposited_mm=timeline.fiber_cum_mm[i],
            current_radius_mm=r_surface,
            lag_error_mm=lag_mm,
            progress_pct=timeline.fiber_cum_mm[i] / path.total_fiber_length_mm * 100.0,
        )
        states.append(state)

    # Step 3: Compute deposition (once, not per-state)
    layered = _build_layered_paths(path, profile, band)
    deposition = simulate_deposition(
        [lp.path for lp in layered], band, profile
    )

    return TwinSimulationResult(
        states=states,
        layered_paths=layered,
        final_deposition=deposition,
        ...
    )
```

---

## 7. Physics Layers and Responsibilities

### Layer 1: Trajectory (Kinematic)
**Owner:** `trajectory_builder.py`  
**Physics:** Pure kinematics — position, velocity from path geometry and feed rates  
**No dynamics** — assumes ideal servo following  
**Key outputs:** `x_ref(t)`, `a_cum(t)`, `feed(t)`, `alpha(t)`, `fiber_cum(t)`

### Layer 2: Drive Dynamics (First-Order)
**Owner:** `winding_twin.py` (inline, not a separate module)  
**Physics:** First-order lag model `lag = v × τ`  
**Valid for:** Pre-run quality assessment (is lag within tolerance?)  
**Not valid for:** Servo tuning, control loop design  
**Key outputs:** `x_actual(t)`, `lag_error(t)`, `eye_x(t)`

### Layer 3: Eye Geometry
**Owner:** `winding_twin.py` (calls `payout_kinematics`)  
**Physics:** Static standoff + lead geometry  
**Key outputs:** `eye_r(t)`, `contact_z(t)`, `contact_r(t)`

### Layer 4: Fiber Deposition (Spatial)
**Owner:** `fiber_deposition.py`  
**Physics:** Band painting on 2D (z × θ) grid  
**Computed ONCE** after trajectory is built — NOT per time step  
**Key outputs:** `DepositionMap` (thickness, orientation, coverage)

### Layer 5: Layer Buildup (Geometric)
**Owner:** `layer_buildup.py`  
**Physics:** Radial profile growth per layer  
**Key outputs:** `LayeredPath[]`, `final_profile`

### Layer 6: Tension / Compaction (Optional, offline)
**Owner:** `fiber_tension.py`  
**Physics:** Contact pressure, slip risk, compaction factor  
**Used in:** `ManufacturabilityReport`, not in `TwinState`  
**Key outputs:** Slip risk flag, minimum tension requirement

---

## 8. TwinState Data Model — Corrected Fields

```python
@dataclass
class TwinState:
    # Time
    t_s: float                   # simulation time [s]
    progress_pct: float          # fiber_deposited / total_fiber × 100

    # Spindle
    spindle_angle_deg: float     # 0–360 (a_cum_deg % 360)
    spindle_rpm: float           # |dA/dt| / 360 × 60; expected: 10–300 RPM

    # Carriage
    carriage_x_mm: float         # reference (commanded) position [mm]
    carriage_x_actual_mm: float  # with first-order lag [mm]
    carriage_v_mm_s: float       # reference velocity (central diff) [mm/s]

    # Eye (payout guide)
    eye_x_mm: float              # = carriage_actual + lead_mm [mm]
    eye_r_mm: float              # = surface_radius + standoff [mm]

    # Contact point
    contact_z_mm: float          # = carriage reference x [mm]
    contact_r_mm: float          # = surface radius at contact [mm]

    # Process state
    current_layer: int           # 0-indexed layer
    current_circuit: int         # 0-indexed circuit within layer
    fiber_deposited_mm: float    # cumulative fiber [mm]
    current_radius_mm: float     # mandrel surface radius at contact [mm]
    lag_error_mm: float          # carriage lag (positive = behind) [mm]
```

**Sanity bounds for validation:**
- `spindle_rpm` ∈ [0, 300]
- `carriage_x_mm` ∈ [-5, 395]
- `lag_error_mm` ∈ [-10, 10] (first-order model)
- `eye_r_mm` > `contact_r_mm` (eye always outside surface)
- `fiber_deposited_mm` monotonically non-decreasing

---

## 9. TwinSimulationResult — Data Structure

```python
@dataclass
class TwinSimulationResult:
    states: List[TwinState]          # uniform time grid, dt = 1.0 s
    dt_s: float                      # time step (1.0 s)
    total_time_s: float              # = len(states) × dt_s
    n_layers: int
    n_circuits: int
    layered_paths: List[LayeredPathEntry]  # from layer_buildup
    final_deposition: DepositionMap
    layer_time_ranges_s: List[Tuple[float, float]]  # [(t0, t1) per layer]
    max_lag_error_mm: float          # diagnostic
    max_spindle_rpm: float           # diagnostic — should be ≤ MachineEnvelope limit
    peak_carriage_speed_mm_s: float  # diagnostic

    def state_at(self, t_s: float) -> TwinState:
        """Binary search or floor-index into states list."""
        idx = min(int(t_s / self.dt_s), len(self.states) - 1)
        return self.states[max(0, idx)]
```

---

## 10. `payout_dynamics.py` — Retained vs Removed

### RETAINED (useful):
- `PayoutDynamicsConfig` dataclass — config for twin simulation
- `compute_carriage_lead_safe(alpha_deg, standoff_mm)` — static geometry
- `compute_lag_compensation_mm(velocity_mm_s, config)` — first-order lag model
- `ContactPointMotion` dataclass + `compute_contact_point_motion()` — post-processing

### REMOVED FROM TWIN PATH:
- `simulate_eye_response()` — the full PD + accel saturation simulation
- `EyeResponseResult` — removed from winding twin; kept only for servo analysis tooling

The `simulate_eye_response()` function is valid for analyzing servo responsiveness as a standalone tool, but it must NOT be called inside `simulate_winding()`. Its instability (numerical blow-up due to high `kp` × large `dt`) corrupts the twin output.

---

## 11. Timing Model

### Simulation Time Grid
- **dt = 1.0 s** (not 0.1 s)
- Rationale: UI playback at 30 FPS with 100× speed = need 1 state per 0.033 s of simulated time → dt = 1.0 s covers 30× playback easily
- For a 3259 s winding: 3260 states (vs 32,592 states at dt=0.1 s) — 10× memory reduction

### Segment-to-Time Conversion
For each WindingPoint pair `(p[i], p[i+1])`:
```
dx     = p[i+1].x_mm - p[i].x_mm
da_arc = (p[i+1].a_deg - p[i].a_deg) × π/180 × r_at_midpoint
ds     = sqrt(dx² + da_arc²)            [mm]
dt_seg = ds / max(feed_mm_s, 1.0)       [s]
```
Where `feed_mm_s` = `p[i].feed` (the feed rate stored in the WindingPoint, already in mm/s as set by `WindingPathParams.feed_mm_s`).

### Layer Time Range Detection
A layer transition is detected when `timeline.layer[i] != timeline.layer[i-1]`. The time ranges are recorded as `(t_first_state_in_layer, t_last_state_in_layer)`.

---

## 12. Real-Time vs Offline Boundaries

| Computation | Mode | Module | Rationale |
|---|---|---|---|
| Trajectory building | **Offline** | `trajectory_builder.py` | O(N_points), run once |
| Kinematic state history | **Offline** | `winding_twin.simulate_winding()` | O(N_states), run once |
| Fiber deposition map | **Offline** | `fiber_deposition.py` | O(Nz × Nθ × N_circuits) |
| Layer buildup geometry | **Offline** | `layer_buildup.py` | O(N_layers) |
| ManufacturabilityReport | **Offline** | `manufacturability.py` | O(N_points × N_checks) |
| ProductionReport | **Offline** | `production_report.py` | Aggregates all above |
| Playback state access | **Real-time** | `simulation_playback.py` | O(1) index lookup |
| Telemetry snapshot | **Real-time** | `simulation_playback.py` | O(1) dict construction |
| UI chart update | **Real-time** | `winding_3d.py`, panels | O(1) per frame |

**Critical rule:** Nothing in the real-time playback path should call physics modules. `state_at(t)` must be O(1) — a simple array index.

---

## 13. Validation Strategy

### 13.1 Unit Tests Per Module

**`trajectory_builder.py`:**
- Given `WindingPath` for a straight 300 mm cylinder, total fiber length must equal `sqrt(dx² + da_arc²)` summed over all points ± 0.1%
- `a_cum_deg` must be monotonically non-decreasing
- `x_ref_mm` must stay within `[carriage_min_mm, carriage_max_mm]`
- `feed_mm_s` must stay within `[0, WindingPathParams.feed_mm_s]`
- Time array must be uniform: `np.diff(timeline.t_s)` ≈ `dt_s` throughout

**`winding_twin.py` (corrected):**
- `max_spindle_rpm` < `MachineEnvelope.max_spindle_rpm` (300 RPM)
- `max_lag_error_mm` < 10 mm (for v ≤ 80 mm/s, τ ≤ 0.1 s)
- `fiber_deposited_mm[-1]` ≈ `path.total_fiber_length_mm` ± 1%
- `total_time_s` ≈ `path.estimated_time_s` ± 5%
- All `carriage_x_mm` within `[-5, 400]`
- `state_at(0.0)` returns first state; `state_at(total_time_s)` returns last state

**Regression guard:**
- Smoke test must produce: `max_lag_error_mm < 10`, `max_spindle_rpm < 300`
- These two bounds catch Bug 1 and Bug 2 regressions

### 13.2 Integration Test (Full Pipeline)

```python
# test_digital_twin.py — integration scenario

def test_full_twin_pipeline():
    profile = MandrelProfile.cylinder(300.0, 50.0)
    band    = FiberBand(tow_width_mm=6.35, thickness_mm=0.2)
    params  = WindingPathParams(profile, alpha_deg=55.0, n_layers=3)
    path    = generate_path(params)
    config  = PayoutDynamicsConfig(standoff_mm=150.0)

    result = simulate_winding(path, profile, band, config)

    assert result.max_spindle_rpm < 300.0          # Bug 1 guard
    assert result.max_lag_error_mm < 10.0           # Bug 2 guard
    assert result.total_time_s > 100.0              # reasonable duration
    assert result.final_deposition.coverage_pct > 60.0
    assert len(result.states) == int(result.total_time_s) + 1

    pb = SimulationPlayback(result)
    pb.play()
    pb.advance(10.0)
    snap = pb.telemetry_snapshot()
    assert 0 < snap["is_mili_rpm"] < 300
    assert abs(snap["gecikme_hata_mm"]) < 10.0
```

### 13.3 Numerical Sanity Report

After any `simulate_winding()` call, print a summary:
```
WindingTwin: 3 layers | 3260 states @ dt=1s | süre=3259s
  r: 50.0→50.6 mm | fiber=87.9m
  max_spindle_rpm=45.2 | max_lag_error=3.2 mm   ← SANE values
  deposition: coverage=71.3% uniformity=0.43
```

---

## 14. Implementation Plan (Incremental Sprints)

### Sprint 4A — THIS DOCUMENT (done)

### Sprint 4B — Fix `winding_twin.py` (Phase B)
1. Add `trajectory_builder.py` — `build_twin_timeline(path, profile, dt_s=1.0)`
2. Rewrite `simulate_winding()` — use trajectory builder, correct RPM, first-order lag
3. Fix `TwinSimulationResult.state_at()` — correct O(1) index

### Sprint 4C — Fix Tests (Phase C)
1. Rewrite `test_digital_twin.py` with regression bounds for Bug 1/Bug 2
2. Run full test suite — expect all pass

### Sprint 4D — Integration + Playback (Phase D)
1. Wire corrected twin into `cam_panel.py` simulation tab
2. Wire `SimulationPlayback` into UI timer
3. Show telemetry_snapshot() values in live HUD overlay

### Sprint 4E — Production Report (Phase E)
1. Wire `ProductionReport` into CAM panel output tab
2. Show verdict + blocking issues in Turkish UI

---

## 15. File Locations

```
faz17_d1/faz17_d1_backend/faz17_d1/core/
├── trajectory_builder.py    ← NEW (Sprint 4B)
├── winding_twin.py          ← REWRITE simulate_winding() (Sprint 4B)
├── payout_dynamics.py       ← PARTIAL use only (retain lag helpers)
├── simulation_playback.py   ← CORRECT, no changes
├── production_report.py     ← Mostly correct, minor wiring
├── fiber_deposition.py      ← CORRECT
├── layer_buildup.py         ← CORRECT
├── process_parameters.py    ← CORRECT
├── machine_envelope.py      ← CORRECT
└── fiber_tension.py         ← CORRECT

faz17_d1/tests/
└── test_digital_twin.py     ← REWRITE with regression bounds (Sprint 4C)
```

---

## 16. Summary of Root Causes

| Bug | Root Cause | Fix |
|---|---|---|
| RPM = 45,819 | `da_deg/dt` computed across simulation step on cumulative angle | Differential per adjacent state pair |
| Lag = 17,736 mm | PD controller numerically unstable at dt=0.1s with kp=2527 | Replace with `lag = v × τ` |
| 32,592 states | dt = 0.1 s (too fine for 3259 s process) | Use dt = 1.0 s |
| Simulation slow | O(N_states) full PD integration loop | Vectorized numpy after trajectory build |

All bugs share one root: `simulate_winding()` attempted to simulate servo electronics (closed-loop PD control) inside an offline trajectory planner. These are different engineering domains. The fix is architectural: use first-order lag for pre-run quality assessment; use `simulate_eye_response()` only if a standalone servo analysis tool is needed.

---

*End of DIGITAL_TWIN_ARCHITECTURE.md*
