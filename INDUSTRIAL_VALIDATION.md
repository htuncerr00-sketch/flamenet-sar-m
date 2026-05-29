# INDUSTRIAL MANUFACTURING CORRECTNESS — Subsystem Validation Record

**Phase:** Industrial Winding Validation + Machine Constraint Modeling
**Branch:** claude/amazing-feynman-XUXBf
**Date:** 2026-05-29

Every subsystem below preserves: deterministic simulation, manufacturing
validity, machine safety, physically realizable trajectories, stable motion
planning. Each was **built on** the existing validated foundations rather than
duplicating them. Per the validation standard, each subsystem documents:
analytical basis · engineering assumptions · deterministic tests ·
manufacturability validation · failure-mode analysis.

**Regression status after this phase:**
- `test_industrial_validity.py` — **47/47 PASS** (new)
- `test_winding_physics.py` — **13363/13363 PASS** (unchanged, no regression)
- `test_digital_twin.py` — **196/197 PASS** (1 pre-existing deposition failure, out of scope)

---

## 1. Machine Limit Model — `machine_limits.py`

**Builds on:** `machine_envelope.py` (`MachineLimits.from_envelope`), consumes the
deterministic `TwinTimeline`.

**Analytical basis.** A trajectory is realizable iff every axis derivative stays
within capacity: `|v_x|≤v_max`, `|a_x|≤a_max`, `|j_x|≤j_max`, `|ω_a|≤ω_max`,
`|α_a|≤α_max`, `|j_a|≤j_max`, `x_soft_min≤x≤x_soft_max`, spindle angle monotone.
Derivatives use deterministic backward finite differences on the uniform-dt grid:
`a[i]=(v[i]−v[i−1])/dt`, `j[i]=(a[i]−a[i−1])/dt`.

**Engineering assumptions.** Uniform-dt timeline (trajectory_builder guarantee);
global cumulative spindle angle is monotone in normal operation; feedrate ≈
carriage speed magnitude; 0.1% numerical tolerance on derivative gates (matches
`machine_envelope`).

**Deterministic tests.** `test_machine_limit_compliance`, `test_no_impossible_accelerations`,
`test_realistic_rpm`, `test_deterministic_output` — feasible trajectory passes;
synthetic infeasible trajectory is rejected with the correct violation kinds;
re-run is bit-identical.

**Manufacturability validation.** `validate_trajectory_against_machine()` returns a
pass/fail `is_realizable` verdict plus per-kind counts (feedrate, spindle
saturation, accel, jerk, overtravel, rotary desync, eye travel).

**Failure-mode analysis.** Saturated spindle + moving carriage → winding angle
cannot be held (desync); excess jerk → drive vibration + tension ripple + step
loss; overtravel → mechanical collision; unreachable feedrate → controller clamps
feed and the angle drifts.

---

## 2. Fiber Tension Model — `fiber_tension_model.py`

**Builds on:** `fiber_tension.py` (point physics), reuses `geodesic_validator`
`_compute_local_alpha` / `_merge_zones` / `Zone`.

**Analytical basis.** Path-wide tension via capstan (belt-friction) theory:
`T_eye = T_set·exp(μ_eye·φ_wrap)` at the payout eye, and surface capstan
amplification `T(s)=T_set·exp(μ_surf·Φ(s))` where `Φ(s)` is the cumulative
meridian-tangent turn from pass start. Cylinder meridian is straight → `Φ=0` →
no amplification (correct); dome meridian curves → tension builds up (correct).
Contact pressure `p=T/(r·w)`; slip margin `tan(α)/μ`.

**Engineering assumptions.** Capstan amplification resets per pass (the tensioner
re-establishes setpoint — quasi-static); amplification is capped by
`max_amplification`, and reaching the cap is reported as a runaway warning, not
hidden; geodesic paths have zero true lateral slip (margin reported for
non-geodesic deviation).

**Deterministic tests.** `test_deterministic_output` (tension profile bit-identical),
`test_stl_taper_stability` (finite tension on taper). Cylinder smoke: amplification
≈ eye-only (1.15×), dome shows surface build-up.

**Manufacturability validation.** Emits the estimated tension profile, unstable
zones (low contact pressure or amplification saturation), and a minimum-safe
setpoint estimate `T_set ≥ p_min·r_max·w`.

**Failure-mode analysis.** Low contact pressure → loose fiber, bridging, voids;
excess amplification → fiber breakage, resin squeeze-out, uneven compaction;
high slip margin → fiber slip, angle drift, dry zones.

---

## 3. Dome Transition Physics — `dome_transition.py`

**Builds on:** `geometry_engine.MandrelProfile` geometry.

**Analytical basis.** Clairaut `c=r·sinα` (constant on a geodesic); polar
turnaround at `r=c` (α→90°); local geodesic angle `α(z)=arcsin(c/r)`; meridian
curvature `κ=|r''|/(1+r'²)^{3/2}`; band axial projection `W/sinα`; tangent
continuity from meridian tangent-angle jumps.

**Engineering assumptions.** Rotational symmetry (2D meridian analysis); polar
turnaround where `r→c`; lift-off (impossible geodesic) where `r<c`; **`tan(α)/μ`
is a warning-level aggressiveness indicator only** — a true geodesic has `k_g=0`
and does not slip even at high angle, so it must not reject geodesic dome winding.

**Deterministic tests.** `test_dome_transition_continuity` — cylinder & cone-taper
traversable and tangent-continuous; full hemisphere correctly flagged for lift-off
(r→0<c) and rejected; polar turnaround detected.

**Manufacturability validation.** `is_traversable` verdict; lift-off and tangent
discontinuity are critical (genuine physical impossibilities), bandwidth
distortion and high-angle aggressiveness are warnings.

**Failure-mode analysis.** Tangent discontinuity → fiber bridging, gaps/pile-up at
dome tip; `r<c` → lift-off (impossible path); excessive band narrowing → pole
pile-up / resin excess.

---

## 4. Multi-Layer Stacking Model — `layer_stacking.py`

**Builds on:** `layer_buildup.py` (`LayerBuildup`), `fiber_band.py`.

**Analytical basis.** Nominal layer thickness `t_nom = tow_thickness·compaction`;
nesting-effective thickness `t_eff = t_nom·(1−nesting_factor)` for layers > 0;
effective radius `r_k = r_0 + Σt_eff`; circumference `C_k=2πr_k`; fixed-circuit
step `step=C_k/N`; overlap evolution `overlap_k=1−step_k/W` (negative → gap);
band axial projection `W/sin(α_k)`.

**Engineering assumptions.** Uniform radial growth (consistent with layer_buildup);
circuit count either held from base (realistic machine repeat) or recomputed per
layer; nesting applied after the first layer.

**Deterministic tests.** `test_cumulative_layer_correctness` — monotone radius,
`effective_total < nominal_total` (nesting), `final_radius = base + Σt_eff`,
overlap shrinks with radius under fixed circuits, finite bandwidth projection.

**Manufacturability validation.** `is_consistent` flag (no negative-overlap gap
layers) plus warnings for shrinking overlap and excess (>50%) overlap.

**Failure-mode analysis.** Overlap → negative → inter-layer gaps → dry fiber /
leak path; excess overlap accumulation → resin excess, thickness bloat; bandwidth
shift → coverage pattern drift, poor edge quality.

---

## 5. Advanced Coverage Analysis — `coverage_solver.py` (extended)

**Builds on / extends:** the existing `CoverageMap` / `solve_coverage` (appended,
not rewritten).

**Analytical basis.** True band projection `W/sin(α)`; local coverage density =
per-cell circuit count normalized to max; overlap histogram = frequency of pass
counts; statistical metrics (mean/std/median/p95) over the (z×θ) grid;
excess-resin zones = cells with count ≥ threshold, dry-fiber zones = empty cells,
grouped by θ-wrapping flood fill.

**Engineering assumptions.** Cells deeper than `overlap_threshold` passes are
resin-rich; empty cells are dry; band footprint sampled at ±W/2 (existing solver).

**Deterministic tests.** Exercised in `test_industrial_validity` via the coverage
pipeline; smoke-verified density-map shape, histogram, and risk percentages.

**Manufacturability validation.** `CoverageRiskReport.is_acceptable` against
`max_excess_pct` / `max_dry_pct` thresholds, with zone lists and warnings.

**Failure-mode analysis.** Excess overlap → resin-rich weak zones, weight gain;
gaps → dry fiber / open area → leak path, mechanical weakness.

---

## 6. G-code Safety — `gcode_postprocessor.py` (extended)

**Builds on / extends:** existing `generate_gcode` left intact (backward
compatible); new `MachineSafetyConfig` + `generate_safe_gcode`.

**Analytical basis.** Machine-zero offset `X_out = X_path + zero`; soft-limit
gate per move (clamp or skip+warn); safe-retract park moves at start/end; spindle
ramp via graded startup/shutdown dwells; synchronized spindle M3/M5.

**Engineering assumptions.** A-axis is the spindle (rotary); ramp realized as
graded dwell steps; controller dialects grbl/mach3/fanuc honored for G0/G1/G4.

**Deterministic tests.** Smoke-verified: generous limits → `is_safe`, M3/M5 +
park + ramp present; tight limits → violations counted and moves skipped;
baseline `generate_gcode` still emits valid G1 lines.

**Manufacturability validation.** `GCodeProgram.soft_limit_violations` / `is_safe`
/ `warnings` populated; out-of-limit moves never silently emitted.

**Failure-mode analysis.** Unbounded output → crash into hard stops; abrupt
spindle start → tension spike / fiber snap; no retract → eye/mandrel collision on
load/unload. Each is mitigated by the safety pass.

---

## 7. Industrial Validation Suite — `test_industrial_validity.py`

8 test groups, 47 assertions, all deterministic, covering every subsystem above
plus STL taper round-trip and dome continuity. See file header for the mapping.

---

## Engineering rules honored

- **Rejected impossible trajectories** (machine_limits `is_realizable=False`).
- **Aggressive warnings** (every report carries `warnings` / `critical_issues`).
- **No simplified mathematics, no non-physical shortcuts** (capstan, Clairaut,
  meridian curvature, finite-difference derivatives used directly).
- **No hidden instability** (amplification cap, lift-off, desync are surfaced).
- **Deterministic** (bit-identical re-runs verified in tests).
- **No cosmetic UI added.**

---

*End of INDUSTRIAL_VALIDATION.md*
