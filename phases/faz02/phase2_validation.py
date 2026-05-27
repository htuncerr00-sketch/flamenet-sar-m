#!/usr/bin/env python3
"""
phase2_validation.py — Faz 2 Tam Entegrasyon Testi
====================================================
Pattern çözücü → Puanlama → Katman yöneticisi → Kaplama haritası

Test parametreleri:
  R = 50mm, L = 300mm, α = 30°, b = 10mm, d = 1 katman
"""

import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from geometry import CylindricalMandrel
from winding_math import WindingParameters, ClairautCalculator
from motion_planner import MachineConfig
from pattern_solver import PatternSolver, PatternSearchConfig
from pattern_score_engine import PatternScoreEngine, ScoringWeights
from layer_manager import LayerManager
from coverage_map import CoverageMap, CoverageConfig

def sep(label="", w=65):
    print(f"\n{'='*4} {label} {'='*(w-len(label)-6)}" if label else "="*w)

def chk(cond, ok_msg, fail_msg=""):
    if cond: print(f"    ✓ {ok_msg}")
    else:
        print(f"    ✗ HATA: {fail_msg or ok_msg}")
        raise AssertionError(fail_msg or ok_msg)

# ── Parametreler ──────────────────────────────────────────
R, L, ALPHA, B, D = 50.0, 300.0, 30.0, 10.0, 1
S_PRIME = 100.0

# ── Temel nesneler ────────────────────────────────────────
mandrel   = CylindricalMandrel(R, L)
winding   = WindingParameters.from_degrees(ALPHA, B, S_PRIME, D)
calc      = ClairautCalculator(mandrel, winding)
constants = calc.compute()
machine   = MachineConfig.default_2axis()

# ─────────────────────────────────────────────────────────
sep("ADIM 1: PATTERN SOLVER")
# ─────────────────────────────────────────────────────────

cfg    = PatternSearchConfig(n_tolerance=0.40, overlap_limit=0.50, max_candidates=20)
solver = PatternSolver(mandrel, winding, constants, cfg)

K       = solver.turn_around_angle_rad
n_ideal = solver.n_ideal

print(f"    K (turn-around) = {math.degrees(K):.6f}°")
print(f"    n_ideal         = {n_ideal:.6f}")
print(f"    b_eff           = {solver.b_eff_mm:.4f} mm")

expected_K = 2 * L * math.tan(math.radians(ALPHA)) / R
chk(abs(K - expected_K) < 1e-10, f"K = 2L·tan(α)/R = {math.degrees(expected_K):.6f}°")

candidates = solver.solve()
chk(len(candidates) > 0, f"{len(candidates)} aday bulundu")

print(f"\n{solver.report(max_show=8)}")

# İlk adayın temel kontrolleri
best = candidates[0]
from math import gcd
chk(gcd(best.p, best.k) == 1,
    f"Best: gcd(p={best.p}, k={best.k}) = {gcd(best.p,best.k)} (= 1 olmalı)")

chk(abs(best.overlap_ratio) < 0.50,
    f"Best overlap ratio = {best.overlap_ratio:.4f} < 0.50")

# ε = k·K - p·2π doğrulaması (artık açı)
K_computed = solver.turn_around_angle_rad
eps_check  = best.k * K_computed - best.p * 2.0 * math.pi
chk(abs(eps_check - best.epsilon_rad) < 1e-9,
    f"ε = k·K - p·2π = {eps_check:.8f} rad ✓ (kaydedilen: {best.epsilon_rad:.8f})")

# Overlap mm dönüşümü doğrulaması
cos_a        = math.cos(math.radians(ALPHA))
overlap_exp  = best.epsilon_rad * R / cos_a
chk(abs(overlap_exp - best.overlap_mm) < 0.01,
    f"overlap = ε·R/cos(α) = {overlap_exp:.4f}mm ✓")

# ─────────────────────────────────────────────────────────
sep("ADIM 2: PATTERN SCORE ENGINE")
# ─────────────────────────────────────────────────────────

engine = PatternScoreEngine(mandrel, winding, constants, machine)
scores = engine.score_all(candidates)

print(f"\n{engine.report(scores, max_show=8)}")

chk(len(scores) == len(candidates), "Her aday için bir skor mevcut")
chk(scores[0].composite_score >= scores[-1].composite_score,
    "Skorlar azalan sırada sıralanmış")

top = scores[0]
print(f"\n  En İyi Pattern Detay Raporu:")
print(top.detail_report())

chk(top.composite_score > 0.0, "Top score > 0")
chk(top.composite_score <= 1.0, "Top score ≤ 1")

# Spindle hız kontrolü — 250rpm makinede 30° ile sorun yok
rpm = S_PRIME * math.sin(math.radians(ALPHA)) / (2*math.pi*R) * 60
chk(rpm < machine.max_a_rpm, f"Spindle {rpm:.4f} rpm < {machine.max_a_rpm:.0f} rpm (makine limiti)")

# ─────────────────────────────────────────────────────────
sep("ADIM 3: LAYER MANAGER")
# ─────────────────────────────────────────────────────────

# En iyi pattern'i seç (feasible olan ilk)
best_feasible = next((s.candidate for s in scores if s.is_feasible), candidates[0])
print(f"    Seçilen pattern: n={best_feasible.n}, p={best_feasible.p}, k={best_feasible.k}")

manager  = LayerManager(mandrel, winding, constants, best_feasible, include_hoop=True)
offsets  = manager.compute_layer_offsets()
print(f"    Layer offsets: {[f'{math.degrees(o):.4f}°' for o in offsets]}")

# Schedule (toolpath olmadan, hızlı)
schedule = manager.build_schedule(n_points_per_pass=50, compute_toolpath=True)
print(f"\n{schedule.summary()}")

chk(len(schedule.layers) == D + 1,  # D helical + 1 hoop
    f"Katman sayısı = {len(schedule.layers)} (beklenen: {D+1})")
chk(schedule.layers[0].n_circuits == best_feasible.k,
    f"Helical devre sayısı = {schedule.layers[0].n_circuits} == k={best_feasible.k}")

# Hoop katmanı kontrolü
hoop_layer = schedule.layers[-1]
chk(hoop_layer.winding_type.name == "HOOP", "Son katman HOOP")
expected_hoop_n = math.ceil(L / B)
chk(len(hoop_layer.circuits) == expected_hoop_n,
    f"Hoop devre sayısı = {len(hoop_layer.circuits)} = ceil(L/b) = {expected_hoop_n}")

# φ offset doğrulaması (d=1 için offset=0)
chk(abs(offsets[0]) < 1e-10, "d=1 → Layer 0 φ_offset = 0")

# İlk devrenin toolpath uzunluğu
c0 = schedule.layers[0].circuits[0]
chk(len(c0.toolpath) > 0, f"Circuit 0 toolpath: {len(c0.toolpath)} nokta")

# ─────────────────────────────────────────────────────────
sep("ADIM 4: COVERAGE MAP")
# ─────────────────────────────────────────────────────────

cov_cfg = CoverageConfig(n_z=150, n_phi=270, fiber_thickness=0.25)
cov_map = CoverageMap(mandrel, winding, constants, cov_cfg)
cov_map.add_schedule(schedule)

stats = cov_map.analyze()
print(f"\n{cov_map.full_report()}")

cov_map.print_ascii(z_bins=55, phi_bins=22)

chk(stats.coverage_fraction > 0.30,
    f"Coverage > 30%: {stats.coverage_fraction*100:.1f}% "
    f"(k={best_feasible.k} devre, b_eff={constants.bandwidth_eff:.2f}mm)")
chk(stats.uniformity_index < 2.0,
    f"Uniformity index = {stats.uniformity_index:.4f} < 2.0")

# Bookhart & Fowler teorik kaplama/devre
cos_a = math.cos(math.radians(ALPHA))
b_eff = B / cos_a
cpc_expected = b_eff / (math.pi * R * cos_a)
chk(abs(stats.coverage_per_circuit - cpc_expected) < 1e-10,
    f"Coverage/circuit = {stats.coverage_per_circuit:.6f} (Bookhart&Fowler)")

# ─────────────────────────────────────────────────────────
sep("SONUÇ")
# ─────────────────────────────────────────────────────────
print(f"""
  ✓ Tüm Faz 2 testleri başarılı.

  PatternSolver   → {len(candidates)} aday, en iyi: n={best_feasible.n}, p={best_feasible.p}, k={best_feasible.k}
  ScoreEngine     → composite={top.composite_score:.4f}  {'ÖNERİLİR' if top.is_recommended else 'KABUL'}
  LayerManager    → {schedule.total_circuits} devre, {schedule.total_fiber_m:.1f}m fiber
  CoverageMap     → {stats.coverage_fraction*100:.1f}% kaplama, σ/μ={stats.uniformity_index:.3f}

  Sonraki → Faz 3: DomeMandrel + GeodesicPathIntegrator (RK45, arc-length)
""")
sep()

if __name__ == "__main__":
    pass  # Doğrudan çalıştırılabilir
