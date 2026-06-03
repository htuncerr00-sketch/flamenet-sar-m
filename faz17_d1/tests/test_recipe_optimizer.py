"""
tests/test_recipe_optimizer.py — Reçete Optimize Edici Modülleri Doğrulama
==========================================================================
6 modül için kapsamlı testler:
    Grup 1 — material_database   (Malzeme veritabanı)
    Grup 2 — laminate_builder    (Laminat oluşturucu)
    Grup 3 — thickness_predictor (Kalınlık tahmincisi)
    Grup 4 — production_estimator (Üretim süresi tahmincisi)
    Grup 5 — cost_estimator      (Maliyet tahmincisi)
    Grup 6 — recipe_optimizer    (Ana optimize edici)

Çalıştırma:
    python -m pytest faz17_d1/tests/test_recipe_optimizer.py -v
    veya
    python faz17_d1/tests/test_recipe_optimizer.py
"""
from __future__ import annotations

import math
import sys
import os

# Backend yolunu ekle
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'faz17_d1_backend'))

import numpy as np

from faz17_d1.core.material_database import (
    FiberSpec, ResinSpec, TowSpec, MaterialSpec,
    carbon_t700_standard_epoxy, carbon_t700_narrow_epoxy,
    carbon_im7_epoxy, eglass_epoxy, eglass_vinyl_ester, aramid_epoxy,
    get_material, available_materials,
)
from faz17_d1.core.laminate_builder import (
    AngleFamily, LayerSchedule, LaminatePly, LaminateStack,
    build_laminate, make_helical_schedule, make_hoop_schedule,
    make_polar_helical_hoop_schedule,
)
from faz17_d1.core.thickness_predictor import (
    ThicknessMap, predict_schedule_thickness, predict_cylinder_thickness,
    predict_coverage_pct, evaluate_thickness_error, _clairaut_coverage_factor,
)
from faz17_d1.core.production_estimator import (
    FamilyEstimate, CycleBreakdown,
    estimate_cycle_time, estimate_fiber_length,
    _circuits_per_set, _effective_feed, _fiber_length_per_set,
    _effective_traverse_length,
)
from faz17_d1.core.cost_estimator import (
    CostBreakdown, ProductionRates,
    estimate_cost, estimate_cost_quick,
    _mandrel_surface_area_mm2,
)
from faz17_d1.core.recipe_optimizer import (
    RecipeConstraints, RecipeObjective, RecipeInput, OptimizedRecipe,
    FeasibilityAnalysis, RecipeScore,
    optimize_recipe, quick_optimize,
    generate_feasible_families, _geodesic_min_angle,
    _effective_feed_at_alpha, _classify_strategy,
)
from faz17_d1.core.geometry_engine import MandrelProfile

# ── Test çerçevesi ────────────────────────────────────────────────────────────

_total = 0
_passed = 0
_failed: list = []


def ok(cond: bool, msg: str) -> None:
    global _total, _passed
    _total += 1
    if cond:
        _passed += 1
    else:
        _failed.append(msg)
        print(f"  FAIL: {msg}")


def section(title: str) -> None:
    print(f"\n── {title} ──────────────────────────")


# ── Yardımcı sabitler ─────────────────────────────────────────────────────────

def _std_mat() -> MaterialSpec:
    return carbon_t700_standard_epoxy()


def _std_prof() -> MandrelProfile:
    return MandrelProfile.cylinder(300.0, 50.0)


def _dome_prof() -> MandrelProfile:
    return MandrelProfile.dome_cylinder_dome(200.0, 50.0, 30.0)


# ══════════════════════════════════════════════════════════════════════════════
# GRUP 1 — material_database
# ══════════════════════════════════════════════════════════════════════════════

def test_group_material_database() -> None:
    section("Grup 1: material_database")
    mat = _std_mat()

    # 1.1 Kompozit yoğunluk — kural karışımı
    rho_c_expected = 0.55 * 1.80 + 0.45 * 1.25  # = 1.5525
    ok(abs(mat.composite_density_g_cm3 - rho_c_expected) < 1e-6,
       f"1.1 composite_density kural karışımı: {mat.composite_density_g_cm3:.4f} != {rho_c_expected:.4f}")

    # 1.2 Fiber kütlesi — lineer yoğunluk formülü
    L = 1_000_000.0  # 1 km = 1e6 mm
    m_f = mat.fiber_mass_kg(L)
    expected_kg = mat.tow.tex_g_km / 1e3   # 800 g/km = 0.8 kg/km
    ok(abs(m_f - expected_kg) < 1e-10,
       f"1.2 fiber_mass_kg (1km): {m_f:.6f} != {expected_kg:.6f}")

    # 1.3 Fiber kütlesi — birim analizi: L_mm × tex/(1e9) = kg
    L2 = 5e6  # 5 km
    ok(abs(mat.fiber_mass_kg(L2) - mat.tow.tex_g_km * L2 / 1e9) < 1e-12,
       "1.3 fiber_mass_kg birim analizi")

    # 1.4 Reçine/fiber kütle oranı
    Vf = mat.fiber_volume_fraction
    m_f_ref = 1.0  # kg
    m_r = mat.resin_mass_kg(m_f_ref)
    ratio_expected = (1 - Vf) / Vf * (mat.resin.density_g_cm3 / mat.fiber.density_g_cm3)
    ok(abs(m_r / m_f_ref - ratio_expected) < 1e-9,
       f"1.4 resin_mass_kg oranı: {m_r/m_f_ref:.6f} != {ratio_expected:.6f}")

    # 1.5 Reçine > 0 (ıslak sarma)
    ok(m_r > 0, "1.5 resin_mass_kg > 0")

    # 1.6 Kompozit kütle = fiber + reçine
    m_total = mat.total_composite_mass_kg(L)
    m_r_check = mat.resin_mass_kg(m_f)
    ok(abs(m_total - (m_f + m_r_check)) < 1e-12,
       "1.6 total_composite_mass = fiber + resin")

    # 1.7 Matris hacim fraksiyonu = 1 - Vf
    ok(abs(mat.matrix_volume_fraction - (1 - Vf)) < 1e-12,
       "1.7 matrix_volume_fraction = 1 - Vf")

    # 1.8 Elastik modül > 0 ve fiziksel sınırda
    E_c = mat.E_composite_GPa
    ok(E_c > 50.0 and E_c < 300.0,
       f"1.8 E_composite_GPa fiziksel aralık: {E_c:.1f}")

    # 1.9 Fiber parametre kontrolü — T700S
    fib = mat.fiber
    ok(fib.density_g_cm3 > 1.5 and fib.density_g_cm3 < 2.5,
       f"1.9 fiber density fiziksel: {fib.density_g_cm3}")
    ok(fib.E_GPa > 100.0,
       f"1.9 fiber E_GPa > 100: {fib.E_GPa}")
    ok(fib.tensile_MPa > 1000.0,
       f"1.9 fiber tensile_MPa > 1000: {fib.tensile_MPa}")

    # 1.10 Tow geometrisi
    tow = mat.tow
    ok(tow.tow_width_mm > 0, "1.10 tow_width_mm > 0")
    ok(tow.tow_thickness_mm > 0, "1.10 tow_thickness_mm > 0")
    ok(tow.tow_width_mm > tow.tow_thickness_mm, "1.10 width > thickness (flat tow)")

    # 1.11 Katalog — tüm malzemeler erişilebilir
    names = available_materials()
    ok(len(names) >= 6, f"1.11 catalog >= 6 malzeme: {len(names)}")
    for n in names:
        m2 = get_material(n)
        ok(isinstance(m2, MaterialSpec), f"1.11 catalog[{n}] MaterialSpec")

    # 1.12 Farklı malzemeler farklı özelliklere sahip
    m_glass = eglass_epoxy()
    m_carbon = _std_mat()
    ok(m_glass.fiber.density_g_cm3 > m_carbon.fiber.density_g_cm3,
       "1.12 cam daha yoğun (2.54 > 1.80)")

    # 1.13 IM7 daha yüksek modül
    m_im7 = carbon_im7_epoxy()
    ok(m_im7.fiber.E_GPa > m_carbon.fiber.E_GPa,
       f"1.13 IM7 modül > T700: {m_im7.fiber.E_GPa} > {m_carbon.fiber.E_GPa}")

    # 1.14 Vf kısıt kontrolü
    try:
        invalid = MaterialSpec("test", mat.fiber, mat.resin, mat.tow, fiber_volume_fraction=0.05)
        ok(False, "1.14 Vf<0.1 istisna bekleniyor")
    except ValueError:
        ok(True, "1.14 Vf=0.05 ValueError")

    # 1.15 Dar tow — daha ince
    mat_narrow = carbon_t700_narrow_epoxy()
    ok(mat_narrow.tow.tow_width_mm < mat.tow.tow_width_mm,
       "1.15 dar tow daha ince")

    # 1.16 mass_per_mm_kg = tex_g_km / 1e9
    ok(abs(mat.fiber.mass_per_mm_kg() - mat.tow.tex_g_km / 1e9) < 1e-15,
       "1.16 mass_per_mm_kg = tex/1e9")

    print(f"   [OK grupta {_passed} / {_total}]")


# ══════════════════════════════════════════════════════════════════════════════
# GRUP 2 — laminate_builder
# ══════════════════════════════════════════════════════════════════════════════

def test_group_laminate_builder() -> None:
    section("Grup 2: laminate_builder")
    t0 = _total
    mat = _std_mat()

    # 2.1 AngleFamily n_plies simetrik
    fam_h = AngleFamily(55.0, 3, "helical", True, 5.0)
    ok(fam_h.n_plies == 6, f"2.1 simetrik n_plies=6: {fam_h.n_plies}")

    # 2.2 AngleFamily n_plies tek yön
    fam_hoop = AngleFamily(88.0, 4, "hoop", False, 5.0)
    ok(fam_hoop.n_plies == 4, f"2.2 tek yön n_plies=4: {fam_hoop.n_plies}")

    # 2.3 is_dome_traversal
    ok(fam_h.is_dome_traversal, "2.3 helical is_dome_traversal")
    ok(AngleFamily(10.0, 1, "polar").is_dome_traversal, "2.3 polar is_dome_traversal")
    ok(not AngleFamily(88.0, 1, "hoop", False).is_dome_traversal, "2.3 hoop NOT is_dome_traversal")

    # 2.4 ply_angles simetrik
    angles = fam_h.ply_angles()
    ok(len(angles) == 6, f"2.4 ply_angles sayısı: {len(angles)}")
    ok(any(a > 0 for a in angles) and any(a < 0 for a in angles),
       "2.4 simetrik ±α var")

    # 2.5 ply_angles hoop
    angles_hoop = fam_hoop.ply_angles()
    ok(all(a == 88.0 for a in angles_hoop), "2.5 hoop açıları sabit")
    ok(len(angles_hoop) == 4, f"2.5 hoop ply_angles sayısı: {len(angles_hoop)}")

    # 2.6 AngleFamily değer aralığı kontrolü
    try:
        bad = AngleFamily(-5.0, 1, "helical")
        ok(False, "2.6 alpha<0 ValueError bekleniyor")
    except ValueError:
        ok(True, "2.6 alpha=-5 ValueError")

    try:
        bad = AngleFamily(55.0, -1, "helical")
        ok(False, "2.6 n<0 ValueError bekleniyor")
    except ValueError:
        ok(True, "2.6 n=-1 ValueError")

    # 2.7 LayerSchedule toplamlar
    sched = make_helical_schedule(55.0, 4)
    ok(sched.total_layer_sets == 4, f"2.7 total_layer_sets: {sched.total_layer_sets}")
    ok(sched.total_plies == 8, f"2.7 total_plies: {sched.total_plies}")

    # 2.8 LayerSchedule.is_empty()
    empty = LayerSchedule([AngleFamily(55.0, 0, "helical")])
    ok(empty.is_empty(), "2.8 is_empty() for n=0")
    ok(not sched.is_empty(), "2.8 not is_empty() for n=4")

    # 2.9 build_laminate — kat sayısı
    stack = build_laminate(sched, mat, radius_mm=50.0)
    ok(stack.n_plies == 8, f"2.9 build_laminate n_plies: {stack.n_plies}")

    # 2.10 build_laminate — toplam kalınlık pozitif
    ok(stack.total_thickness_mm > 0, f"2.10 total_thickness > 0: {stack.total_thickness_mm}")

    # 2.11 build_laminate — fiber alan kütlesi pozitif
    ok(stack.areal_fiber_mass_g_m2 > 0, f"2.11 areal_fiber_mass > 0: {stack.areal_fiber_mass_g_m2}")

    # 2.12 build_laminate — kompozit > fiber (reçine dahil)
    ok(stack.total_composite_mass_g_m2 > stack.areal_fiber_mass_g_m2,
       f"2.12 composite > fiber areal mass ({stack.total_composite_mass_g_m2:.1f} > {stack.areal_fiber_mass_g_m2:.1f})")

    # 2.13 Daha fazla kat → daha kalın
    sched2 = make_helical_schedule(55.0, 8)
    stack2 = build_laminate(sched2, mat, radius_mm=50.0)
    ok(stack2.total_thickness_mm > stack.total_thickness_mm,
       f"2.13 daha fazla kat daha kalın: {stack2.total_thickness_mm:.3f} > {stack.total_thickness_mm:.3f}")

    # 2.14 build_laminate — compaction (derin katlar daha ince)
    plies = stack2.plies
    ok(plies[0].thickness_mm >= plies[-1].thickness_mm,
       f"2.14 ilk kat ≥ son kat (compaction): {plies[0].thickness_mm:.4f} >= {plies[-1].thickness_mm:.4f}")

    # 2.15 3-aileli plan
    sched3 = make_polar_helical_hoop_schedule(1, 55.0, 2, 2)
    ok(len(sched3.families) >= 3, f"2.15 3-aile plan family sayısı: {len(sched3.families)}")

    # 2.16 make_hoop_schedule
    sched_h = make_hoop_schedule(3)
    ok(abs(sched_h.families[0].alpha_deg - 88.0) < 1.0, "2.16 hoop açısı ~88°")
    ok(not sched_h.families[0].symmetric, "2.16 hoop simetrik değil")

    # 2.17 LaminateStack.thickness_by_family
    sched_m = LayerSchedule([
        AngleFamily(55.0, 2, "helical", True, 5.0),
        AngleFamily(88.0, 2, "hoop", False, 5.0),
    ])
    stack_m = build_laminate(sched_m, mat, radius_mm=50.0)
    by_fam = stack_m.thickness_by_family(2)
    ok(abs(sum(by_fam) - stack_m.total_thickness_mm) < 1e-9,
       f"2.17 thickness_by_family toplamı: {sum(by_fam):.4f} vs {stack_m.total_thickness_mm:.4f}")

    # 2.18 Fiber alan kütlesi açı bağımlılığı
    sched_low = make_helical_schedule(20.0, 2)  # düşük açı: az çevresel, geniş yol
    sched_high = make_helical_schedule(80.0, 2)  # yüksek açı: çevresel yoğun
    stack_low = build_laminate(sched_low, mat, radius_mm=50.0)
    stack_high = build_laminate(sched_high, mat, radius_mm=50.0)
    # Yüksek açıda fiber 1/cos(α) daha uzun → alan kütlesi daha yüksek
    ok(stack_high.areal_fiber_mass_g_m2 > stack_low.areal_fiber_mass_g_m2,
       f"2.18 yüksek α → daha fazla fiber alan kütlesi: {stack_high.areal_fiber_mass_g_m2:.1f} > {stack_low.areal_fiber_mass_g_m2:.1f}")

    print(f"   [OK grupta {_passed - t0} / {_total - t0}]")


# ══════════════════════════════════════════════════════════════════════════════
# GRUP 3 — thickness_predictor
# ══════════════════════════════════════════════════════════════════════════════

def test_group_thickness_predictor() -> None:
    section("Grup 3: thickness_predictor")
    t0 = _total
    mat = _std_mat()
    prof_cyl = _std_prof()

    # 3.1 Silindir bölgesinde tekdüzelik = 1
    sched = make_helical_schedule(55.0, 3)
    t = predict_cylinder_thickness(sched, mat, radius_mm=50.0)
    ok(t > 0, f"3.1 cylinder thickness > 0: {t:.4f}")

    # 3.2 predict_schedule_thickness silindir tekdüzeliği
    tmap = predict_schedule_thickness(sched, prof_cyl, mat)
    ok(tmap.uniformity > 0.95,
       f"3.2 silindir tekdüzeliği > 0.95: {tmap.uniformity:.4f}")

    # 3.3 ThicknessMap ortalama ≈ cylinder ortalama (silindir profilinde)
    ok(abs(tmap.mean_mm - t) / max(t, 1e-6) < 0.10,
       f"3.3 ThicknessMap.mean ≈ cylinder_t: {tmap.mean_mm:.4f} vs {t:.4f}")

    # 3.4 Daha fazla kat → daha kalın
    t1 = predict_cylinder_thickness(make_helical_schedule(55.0, 2), mat)
    t2 = predict_cylinder_thickness(make_helical_schedule(55.0, 4), mat)
    ok(t2 > t1 * 1.8 and t2 < t1 * 2.2,
       f"3.4 2× kat seti ≈ 2× kalınlık: {t1:.4f} → {t2:.4f}")

    # 3.5 Artan kat sayısı ile monoton kalınlık artışı
    thicknesses = [
        predict_cylinder_thickness(make_helical_schedule(55.0, n), mat)
        for n in range(1, 6)
    ]
    ok(all(thicknesses[i] < thicknesses[i + 1] for i in range(4)),
       f"3.5 monoton kalınlık artışı: {[f'{t:.3f}' for t in thicknesses]}")

    # 3.6 ThicknessMap uzunluk kontrolü
    ok(len(tmap.z_mm) == 100, f"3.6 z_mm uzunluğu (varsayılan n_z=100): {len(tmap.z_mm)}")
    ok(len(tmap.thickness_mm) == 100, "3.6 thickness_mm uzunluğu")

    # 3.7 ThicknessMap fiziksel değerler
    ok(tmap.min_mm > 0, f"3.7 min_mm > 0: {tmap.min_mm:.4f}")
    ok(tmap.max_mm >= tmap.min_mm, f"3.7 max_mm >= min_mm: {tmap.max_mm:.4f} >= {tmap.min_mm:.4f}")

    # 3.8 Silindir kapsama = 100%
    cov = predict_coverage_pct(sched, prof_cyl)
    ok(abs(cov - 100.0) < 1.0, f"3.8 silindir kapsama = 100%: {cov:.1f}")

    # 3.9 Boş plan kapsama = 0%
    empty_sched = LayerSchedule([AngleFamily(55.0, 0, "helical")])
    cov_empty = predict_coverage_pct(empty_sched, prof_cyl)
    ok(cov_empty == 0.0, f"3.9 boş plan kapsama = 0: {cov_empty}")

    # 3.10 evaluate_thickness_error
    err = evaluate_thickness_error(sched, mat, target_thickness_mm=t, radius_mm=50.0)
    ok(err < 0.01, f"3.10 hedef = gerçek → hata ≈ 0: {err:.6f}")

    # 3.11 Hedef kalınlıktan sapma oransal hata
    err_half = evaluate_thickness_error(sched, mat, target_thickness_mm=t * 2.0)
    ok(err_half > 0.3 and err_half < 0.6,
       f"3.11 2× hedef → hata ~0.5: {err_half:.3f}")

    # 3.12 Clairaut kapsama faktörü — silindir bölgesinde = 1
    prof_pts = 200
    z_arr = np.linspace(0.0, 300.0, prof_pts)
    cf = _clairaut_coverage_factor(z_arr, 55.0, prof_cyl, 50.0)
    ok(float(np.mean(cf)) > 0.95 and float(np.mean(cf)) < 1.05,
       f"3.12 silindir Clairaut faktörü ≈ 1: {float(np.mean(cf)):.4f}")

    # 3.13 Clairaut kapsama faktörü — kubbe profili
    prof_dome = _dome_prof()
    z_dome = np.linspace(float(prof_dome.z_mm[0]), float(prof_dome.z_mm[-1]), 100)
    cf_dome = _clairaut_coverage_factor(z_dome, 55.0, prof_dome, float(np.max(prof_dome.r_mm)))
    # Kubbe bölgesinde faktör != 1 (eğri)
    ok(float(np.std(cf_dome)) > 0.01,
       f"3.13 kubbe Clairaut faktörü değişken: std={float(np.std(cf_dome)):.4f}")

    # 3.14 Kapsama faktörü negatif olmaz
    ok(float(np.min(cf_dome)) >= 0.0, f"3.14 Clairaut faktörü ≥ 0: {float(np.min(cf_dome)):.4f}")

    # 3.15 thickness_by_family toplamı = toplam
    sched2 = LayerSchedule([
        AngleFamily(55.0, 2, "helical", True, 5.0),
        AngleFamily(88.0, 2, "hoop", False, 5.0),
    ])
    tmap2 = predict_schedule_thickness(sched2, prof_cyl, mat)
    total_from_families = sum(f.sum() for f in tmap2.thickness_by_family)
    ok(abs(total_from_families - float(np.sum(tmap2.thickness_mm))) < 1e-6,
       f"3.15 thickness_by_family toplamı: {total_from_families:.4f} vs {float(np.sum(tmap2.thickness_mm)):.4f}")

    # 3.16 Farklı açılar — farklı kalınlık katkısı (compaction nedeniyle)
    t55 = predict_cylinder_thickness(make_helical_schedule(55.0, 2), mat)
    t30 = predict_cylinder_thickness(make_helical_schedule(30.0, 2), mat)
    # Kalınlık açı bağımsız (sadece tow_thickness × compaction_factor); değerler yakın olmalı
    ok(abs(t55 - t30) / max(t55, 1e-6) < 0.15,
       f"3.16 farklı açılar benzer kalınlık: {t55:.4f} vs {t30:.4f}")

    print(f"   [OK grupta {_passed - t0} / {_total - t0}]")


# ══════════════════════════════════════════════════════════════════════════════
# GRUP 4 — production_estimator
# ══════════════════════════════════════════════════════════════════════════════

def test_group_production_estimator() -> None:
    section("Grup 4: production_estimator")
    t0 = _total
    mat = _std_mat()
    prof = _std_prof()

    # 4.1 Devre sayısı formülü — n = ceil(2π·r / (W·(1-overlap/100)))
    r, W, ov = 50.0, 6.0, 5.0
    n_exp = math.ceil(2 * math.pi * r / (W * (1 - ov / 100)))
    n_got = _circuits_per_set(r, W, ov)
    ok(n_got == n_exp, f"4.1 devre sayısı formülü: {n_got} == {n_exp}")

    # 4.2 Devre sayısı ≥ 1
    ok(_circuits_per_set(10.0, 100.0, 0.0) >= 1, "4.2 n_circuits ≥ 1")

    # 4.3 Overlap artınca daha fazla devre
    n_low = _circuits_per_set(50.0, 6.0, 0.0)
    n_high = _circuits_per_set(50.0, 6.0, 30.0)
    ok(n_high > n_low, f"4.3 overlap artınca daha fazla devre: {n_high} > {n_low}")

    # 4.4 İş mili RPM sınırı — yüksek α'da devreye girer
    v_low_alpha = _effective_feed(80.0, 120.0, 5.0, 50.0)   # neredeyse serbest
    v_high_alpha = _effective_feed(80.0, 120.0, 88.0, 50.0)  # iş mili sınırlı
    ok(v_low_alpha > v_high_alpha,
       f"4.4 düşük α > yüksek α v_eff: {v_low_alpha:.1f} > {v_high_alpha:.1f}")

    # 4.5 Efektif hız ≤ nominal hız
    ok(v_high_alpha <= 80.0, f"4.5 v_eff ≤ nominal_feed: {v_high_alpha:.1f}")
    ok(v_low_alpha <= 80.0, f"4.5 v_eff ≤ nominal_feed (düşük α): {v_low_alpha:.1f}")

    # 4.6 Fiber uzunluğu formülü — fiber_per_circuit = 2L/cos(α)
    prof_pts = prof
    L = float(prof.z_mm[-1] - prof.z_mm[0])  # = 300mm
    alpha = 55.0
    n_circ = 10
    fiber_per_set = _fiber_length_per_set(prof_pts, alpha, n_circ)
    fiber_expected = n_circ * 2.0 * L / math.cos(math.radians(alpha))
    ok(abs(fiber_per_set - fiber_expected) < 1e-6,
       f"4.6 fiber uzunluğu formülü: {fiber_per_set:.2f} vs {fiber_expected:.2f}")

    # 4.7 Düşük α → daha kısa fiber per circuit (daha dik sarım)
    L_alpha5 = _fiber_length_per_set(prof, 5.0, 1)
    L_alpha85 = _fiber_length_per_set(prof, 85.0, 1)
    ok(L_alpha85 > L_alpha5,
       f"4.7 yüksek α → uzun fiber: {L_alpha85:.1f} > {L_alpha5:.1f}")

    # 4.8 estimate_cycle_time döner
    sched = make_helical_schedule(55.0, 3)
    cycle = estimate_cycle_time(sched, prof, mat)
    ok(isinstance(cycle, CycleBreakdown), "4.8 CycleBreakdown döner")
    ok(cycle.total_time_s > 0, f"4.8 total_time_s > 0: {cycle.total_time_s:.1f}")
    ok(cycle.total_fiber_length_mm > 0, f"4.8 total_fiber_length > 0: {cycle.total_fiber_length_mm:.1f}")

    # 4.9 Kurulum süresi dahil
    ok(cycle.total_time_s > 120.0, f"4.9 kurulum süresi (120s) dahil: {cycle.total_time_s:.1f}")

    # 4.10 Daha fazla kat → daha uzun süre
    cycle2 = estimate_cycle_time(make_helical_schedule(55.0, 6), prof, mat)
    ok(cycle2.total_time_s > cycle.total_time_s,
       f"4.10 daha fazla kat daha uzun süre: {cycle2.total_time_s:.0f} > {cycle.total_time_s:.0f}")

    # 4.11 CycleBreakdown.total_time_min
    ok(abs(cycle.total_time_min - cycle.total_time_s / 60.0) < 1e-9,
       "4.11 total_time_min = total_s / 60")

    # 4.12 total_fiber_length_m
    ok(abs(cycle.total_fiber_length_m - cycle.total_fiber_length_mm / 1000.0) < 1e-9,
       "4.12 total_fiber_length_m = mm / 1000")

    # 4.13 peak_carriage_feed_mm_s fiziksel
    ok(0 < cycle.peak_carriage_feed_mm_s <= 80.0,
       f"4.13 peak_carriage_feed ∈ (0, 80]: {cycle.peak_carriage_feed_mm_s:.1f}")

    # 4.14 peak_spindle_rpm fiziksel
    ok(cycle.peak_spindle_rpm >= 0 and cycle.peak_spindle_rpm <= 120.0,
       f"4.14 peak_spindle_rpm ∈ [0, 120]: {cycle.peak_spindle_rpm:.1f}")

    # 4.15 FamilyEstimate spindle_limited — yüksek α'da aktif
    sched_hoop = make_hoop_schedule(2)
    cycle_h = estimate_cycle_time(sched_hoop, prof, mat, spindle_rpm_max=5.0)  # çok düşük RPM
    limited = any(fe.is_spindle_limited for fe in cycle_h.family_estimates)
    ok(limited, "4.15 düşük max_rpm → spindle_limited=True")

    # 4.16 estimate_fiber_length tutarlılık
    fiber_quick = estimate_fiber_length(sched, prof, mat)
    ok(fiber_quick > 0, f"4.16 estimate_fiber_length > 0: {fiber_quick:.1f}")

    # 4.17 Helisel taşıyıcı geçiş uzunluğu > 2×L (lead payı nedeniyle)
    L_trav = _effective_traverse_length(prof, "helical", 55.0, 150.0)
    ok(L_trav > 2.0 * L, f"4.17 helical traverse > 2L: {L_trav:.1f} > {2*L:.1f}")

    # 4.18 Çevre sarma taşıyıcı geçiş = 2×L (lead yok)
    L_hoop = _effective_traverse_length(prof, "hoop", 88.0, 150.0)
    ok(abs(L_hoop - 2.0 * L) < 1.0,
       f"4.18 hoop traverse ≈ 2L: {L_hoop:.1f} vs {2*L:.1f}")

    # 4.19 Kubbe ailesi — döngü süresi kubbe ek süresi içeriyor
    sched_polar = LayerSchedule([AngleFamily(10.0, 2, "polar", True, 5.0)])
    cycle_polar = estimate_cycle_time(sched_polar, prof, mat, dome_overhead_per_family_s=60.0)
    ok(cycle_polar.dome_transition_overhead_s > 0,
       f"4.19 polar dome overhead > 0: {cycle_polar.dome_transition_overhead_s:.0f}")

    print(f"   [OK grupta {_passed - t0} / {_total - t0}]")


# ══════════════════════════════════════════════════════════════════════════════
# GRUP 5 — cost_estimator
# ══════════════════════════════════════════════════════════════════════════════

def test_group_cost_estimator() -> None:
    section("Grup 5: cost_estimator")
    t0 = _total
    mat = _std_mat()
    prof = _std_prof()

    # 5.1 Pappus yüzey alanı — saf silindir kontrolü
    A = _mandrel_surface_area_mm2(prof)
    L = float(prof.z_mm[-1] - prof.z_mm[0])
    r = float(np.max(prof.r_mm))
    A_exact = 2.0 * math.pi * r * L
    ok(abs(A - A_exact) < 1.0, f"5.1 Pappus saf silindir: {A:.2f} vs {A_exact:.2f}")

    # 5.2 estimate_cost_quick döner
    sched = make_helical_schedule(55.0, 4)
    cost = estimate_cost_quick(sched, prof, mat)
    ok(isinstance(cost, CostBreakdown), "5.2 CostBreakdown döner")
    ok(cost.total_cost_usd > 0, f"5.2 total_cost > 0: {cost.total_cost_usd:.2f}")

    # 5.3 Maliyet bileşenleri toplamı = toplam
    direct = cost.fiber_cost_usd + cost.resin_cost_usd + cost.labor_cost_usd
    overhead_expected = direct * 0.30
    total_expected = direct + overhead_expected
    ok(abs(cost.overhead_cost_usd - overhead_expected) < 0.01,
       f"5.3 overhead = direct × 0.30: {cost.overhead_cost_usd:.2f} vs {overhead_expected:.2f}")
    ok(abs(cost.total_cost_usd - total_expected) < 0.01,
       f"5.3 total = direct + overhead: {cost.total_cost_usd:.2f} vs {total_expected:.2f}")

    # 5.4 Fiber kütlesi > 0
    ok(cost.fiber_mass_kg > 0, f"5.4 fiber_mass > 0: {cost.fiber_mass_kg:.4f}")

    # 5.5 Reçine kütlesi > 0 (ıslak sarma)
    ok(cost.resin_mass_kg > 0, f"5.5 resin_mass > 0: {cost.resin_mass_kg:.4f}")

    # 5.6 Reçine/fiber kütle oranı fiziksel
    Vf = mat.fiber_volume_fraction
    ratio_expected = (1 - Vf) / Vf * (mat.resin.density_g_cm3 / mat.fiber.density_g_cm3)
    # Fire faktörü nedeniyle kesin eşit değil; yakın olmalı (scrap_factor etkisi)
    ratio_got = cost.resin_mass_kg / cost.fiber_mass_kg
    ok(abs(ratio_got - ratio_expected) < 0.05,
       f"5.6 resin/fiber kütle oranı: {ratio_got:.4f} vs {ratio_expected:.4f}")

    # 5.7 Yüzey alanı pozitif
    ok(cost.surface_area_mm2 > 0, f"5.7 surface_area > 0: {cost.surface_area_mm2:.1f}")

    # 5.8 cost_per_kg_usd fiziksel
    spesific = cost.cost_per_kg_usd()
    ok(spesific > 10.0, f"5.8 spesifik maliyet > 10 USD/kg: {spesific:.2f}")

    # 5.9 İşçilik maliyeti = (süre_saat) × ücret
    sched2 = make_helical_schedule(55.0, 4)
    from faz17_d1.core.production_estimator import estimate_cycle_time
    cycle = estimate_cycle_time(sched2, prof, mat)
    rates = ProductionRates(labor_rate_usd_per_hr=60.0, overhead_factor=0.30, scrap_factor=0.08)
    cost2 = estimate_cost(sched2, prof, mat, cycle, rates)
    labor_expected = (cycle.total_time_s / 3600.0) * 60.0
    ok(abs(cost2.labor_cost_usd - labor_expected) < 0.01,
       f"5.9 işçilik maliyet formülü: {cost2.labor_cost_usd:.2f} vs {labor_expected:.2f}")

    # 5.10 Daha fazla kat → daha yüksek maliyet
    cost_less = estimate_cost_quick(make_helical_schedule(55.0, 2), prof, mat)
    cost_more = estimate_cost_quick(make_helical_schedule(55.0, 6), prof, mat)
    ok(cost_more.total_cost_usd > cost_less.total_cost_usd,
       f"5.10 fazla kat → fazla maliyet: {cost_more.total_cost_usd:.2f} > {cost_less.total_cost_usd:.2f}")

    # 5.11 Yüksek işçilik ücreti → yüksek maliyet
    rates_cheap = ProductionRates(labor_rate_usd_per_hr=20.0, overhead_factor=0.10)
    rates_exp   = ProductionRates(labor_rate_usd_per_hr=120.0, overhead_factor=0.50)
    cycle3 = estimate_cycle_time(sched, prof, mat)
    c_cheap = estimate_cost(sched, prof, mat, cycle3, rates_cheap)
    c_exp   = estimate_cost(sched, prof, mat, cycle3, rates_exp)
    ok(c_exp.total_cost_usd > c_cheap.total_cost_usd,
       f"5.11 pahalı ücret → yüksek maliyet: {c_exp.total_cost_usd:.2f} > {c_cheap.total_cost_usd:.2f}")

    # 5.12 Fire faktörü etkisi — yüksek fire → daha fazla malzeme maliyeti
    r_low  = ProductionRates(scrap_factor=0.0)
    r_high = ProductionRates(scrap_factor=0.30)
    c_low  = estimate_cost(sched, prof, mat, cycle3, r_low)
    c_high = estimate_cost(sched, prof, mat, cycle3, r_high)
    ok(c_high.fiber_cost_usd > c_low.fiber_cost_usd,
       f"5.12 yüksek fire → daha fazla fiber maliyeti: {c_high.fiber_cost_usd:.2f} > {c_low.fiber_cost_usd:.2f}")

    # 5.13 Pappus — kubbe profilinde silindir alanından farklı
    prof_dome = _dome_prof()
    A_dome = _mandrel_surface_area_mm2(prof_dome)
    ok(A_dome > 0, f"5.13 kubbe Pappus > 0: {A_dome:.1f}")
    ok(A_dome != A, f"5.13 kubbe ≠ silindir alanı")

    # 5.14 composite_mass = fiber + resin
    ok(abs(cost.composite_mass_kg - (cost.fiber_mass_kg + cost.resin_mass_kg)) < 1e-9,
       "5.14 composite_mass = fiber + resin")

    # 5.15 summary() string içerik
    s = cost.summary()
    ok("USD" in s, "5.15 summary USD içeriyor")
    ok("fiber" in s or "fiber" in s.lower(), "5.15 summary fiber içeriyor")

    print(f"   [OK grupta {_passed - t0} / {_total - t0}]")


# ══════════════════════════════════════════════════════════════════════════════
# GRUP 6 — recipe_optimizer
# ══════════════════════════════════════════════════════════════════════════════

def test_group_recipe_optimizer() -> None:
    section("Grup 6: recipe_optimizer")
    t0 = _total
    mat = _std_mat()
    prof_cyl = _std_prof()
    prof_dome = _dome_prof()

    # Tüm optimizer çağrılarında arama uzayını sınırlı tut:
    # max_families=1, alfa kısıtı → <100 iterasyon / çağrı
    _FAST = dict(max_families=1, alpha_min_deg=50.0, alpha_max_deg=70.0,
                 max_layer_sets_per_family=10)

    def _fast_inp(profile, thickness, material=None):
        m = material if material is not None else mat
        c = RecipeConstraints(target_thickness_mm=thickness, **_FAST)
        return RecipeInput(profile=profile, material=m, constraints=c)

    # 6.1 Saf silindir geodezik min = 0°
    geo_cyl = _geodesic_min_angle(prof_cyl)
    ok(abs(geo_cyl) < 0.1,
       f"6.1 saf silindir geodezik min ≈ 0°: {geo_cyl:.3f}")

    # 6.2 Kubbe profili geodezik min = arcsin(r_min/r_max)
    geo_dome = _geodesic_min_angle(prof_dome)
    r_min = float(np.min(prof_dome.r_mm[prof_dome.r_mm > 1.0]))
    r_max = float(np.max(prof_dome.r_mm))
    expected_deg = math.degrees(math.asin(r_min / r_max))
    ok(abs(geo_dome - expected_deg) < 0.1,
       f"6.2 kubbe geodezik min: {geo_dome:.3f} vs {expected_deg:.3f}")

    # 6.3 Geodezik min > 0 için kubbe profili
    ok(geo_dome > 0, f"6.3 kubbe geodezik min > 0°: {geo_dome:.2f}")

    # 6.4 generate_feasible_families silindir için tüm makine açıları
    c_std = RecipeConstraints(target_thickness_mm=2.0)
    inp_full = RecipeInput(profile=prof_cyl, material=mat, constraints=c_std)
    feasible = generate_feasible_families(inp_full)
    ok(len(feasible) > 10, f"6.4 silindir için geniş açı yelpazesi: {len(feasible)} aile")

    # 6.5 Uygulanabilir aileler makine limitlerinde
    c_narrow = RecipeConstraints(target_thickness_mm=2.0, alpha_min_deg=40.0, alpha_max_deg=70.0)
    inp_narrow = RecipeInput(profile=prof_cyl, material=mat, constraints=c_narrow)
    feasible_narrow = generate_feasible_families(inp_narrow)
    for fa in feasible_narrow:
        ok(fa.alpha_deg >= 40.0 - 0.6 and fa.alpha_deg <= 70.0 + 0.6,
           f"6.5 aile makine limitlerinde: α={fa.alpha_deg}")

    # 6.6 _classify_strategy
    ok(_classify_strategy(10.0) == "polar", "6.6 α=10° → polar")
    ok(_classify_strategy(55.0) == "helical", "6.6 α=55° → helical")
    ok(_classify_strategy(85.0) == "hoop", "6.6 α=85° → hoop")

    # 6.7 optimize_recipe — en az 1 reçete döner (dar aralık, tek aile → hızlı)
    results = optimize_recipe(_fast_inp(prof_cyl, 2.0), top_k=3)
    ok(len(results) >= 1, f"6.7 optimize_recipe ≥ 1 reçete: {len(results)}")

    # 6.8 Reçete kalınlığı tolerans içinde
    for r in results:
        err_pct = 100.0 * abs(r.achieved_thickness_mm - 2.0) / 2.0
        ok(err_pct <= 15.0,
           f"6.8 kalınlık tolerans: {err_pct:.1f}% ≤ 15% ({r.schedule.angle_summary})")

    # 6.9 Reçeteler puana göre sıralı (düşük = iyi)
    if len(results) >= 2:
        ok(results[0].score.combined <= results[1].score.combined,
           f"6.9 skor sıralaması: {results[0].score.combined:.4f} ≤ {results[1].score.combined:.4f}")

    # 6.10 Sıra numaraları doğru
    for i, r in enumerate(results):
        ok(r.rank == i + 1, f"6.10 rank={r.rank} == {i+1}")

    # 6.11 Daha kalın hedef → daha fazla kat
    res_thin = optimize_recipe(_fast_inp(prof_cyl, 1.0), top_k=1)
    res_thick = optimize_recipe(_fast_inp(prof_cyl, 4.0), top_k=1)
    if res_thin and res_thick:
        ok(res_thick[0].schedule.total_plies > res_thin[0].schedule.total_plies,
           f"6.11 kalın hedef → fazla kat: {res_thick[0].schedule.total_plies} > {res_thin[0].schedule.total_plies}")

    # 6.12 Zaman kısıtı etkin — sıkı limit → daha az reçete
    c_tight = RecipeConstraints(target_thickness_mm=2.0, max_cycle_time_s=60.0, **_FAST)
    c_loose = RecipeConstraints(target_thickness_mm=2.0, max_cycle_time_s=7200.0, **_FAST)
    res_tight = optimize_recipe(RecipeInput(profile=prof_cyl, material=mat, constraints=c_tight), top_k=5)
    res_loose = optimize_recipe(RecipeInput(profile=prof_cyl, material=mat, constraints=c_loose), top_k=5)
    ok(len(res_loose) >= len(res_tight),
       f"6.12 gevşek limit ≥ sıkı limit reçete sayısı: {len(res_loose)} ≥ {len(res_tight)}")

    # 6.13 Bulunan reçetelerin süresi mantıksal sınırda
    for r in res_loose:
        ok(r.cycle.total_time_s <= 7200.0 * 1.5,
           f"6.13 süre kısıtı: {r.cycle.total_time_s:.0f}s ≤ 10800s")

    # 6.14 RecipeObjective normalize — ağırlık toplamı = 1
    obj = RecipeObjective(w_time=1.0, w_thickness=2.0, w_coverage=1.0, w_cost=0.5, w_manufacturability=0.5)
    obj_norm = obj.normalized()
    total_w = obj_norm.w_time + obj_norm.w_thickness + obj_norm.w_coverage + obj_norm.w_cost + obj_norm.w_manufacturability
    ok(abs(total_w - 1.0) < 1e-9, f"6.14 normalize ağırlık toplamı = 1: {total_w:.6f}")

    # 6.15 RecipeScore combined — ağırlıklı ortalama doğrulama
    if results:
        r0 = results[0]
        sc = r0.score
        obj_n = RecipeInput(
            profile=prof_cyl, material=mat,
            constraints=RecipeConstraints(target_thickness_mm=2.0, **_FAST),
        ).objective.normalized()
        combined_check = (obj_n.w_thickness * sc.f_thickness + obj_n.w_time * sc.f_time +
                         obj_n.w_coverage * sc.f_coverage + obj_n.w_cost * sc.f_cost +
                         obj_n.w_manufacturability * sc.f_manufacturability)
        ok(abs(sc.combined - combined_check) < 0.01,
           f"6.15 combined skor formülü: {sc.combined:.4f} vs {combined_check:.4f}")

    # 6.16 Üretilebilirlik skoru [0, 1]
    for r in results:
        ok(0.0 <= r.manufacturability_score <= 1.0,
           f"6.16 mfg_score ∈ [0,1]: {r.manufacturability_score}")

    # 6.17 RecipeConstraints doğrulama hatası
    try:
        bad = RecipeConstraints(target_thickness_mm=-1.0)
        ok(False, "6.17 negatif hedef ValueError bekleniyor")
    except ValueError:
        ok(True, "6.17 target_thickness_mm=-1 ValueError")

    # 6.18 max_families=1 → tüm reçeteler tek aile
    c_1fam = RecipeConstraints(target_thickness_mm=2.0, max_families=1,
                                alpha_min_deg=50.0, alpha_max_deg=70.0)
    res_1 = optimize_recipe(RecipeInput(profile=prof_cyl, material=mat, constraints=c_1fam), top_k=5)
    for r in res_1:
        ok(len(r.schedule.families) == 1,
           f"6.18 max_families=1 → tek aile: {len(r.schedule.families)}")

    # 6.19 Kubbe profili ile çalışıyor
    results_dome = optimize_recipe(_fast_inp(prof_dome, 2.0), top_k=1)
    ok(len(results_dome) >= 1, f"6.19 kubbe profili reçete üretiyor: {len(results_dome)}")

    # 6.20 Yüksek geodezik açı kısıtı — küçük açı aileleri engellenir
    if geo_dome > 1.0:
        c_dome_con = RecipeConstraints(target_thickness_mm=2.0, allow_non_geodesic=False)
        inp_dome_con = RecipeInput(profile=prof_dome, material=mat, constraints=c_dome_con)
        feas_dome = generate_feasible_families(inp_dome_con)
        for fa in feas_dome:
            ok(fa.alpha_deg >= geo_dome - 0.6 or fa.is_geodesic_feasible,
               f"6.20 geodezik kısıt: α={fa.alpha_deg:.0f} ≥ {geo_dome:.1f}°")

    # 6.21 OptimizedRecipe.summary() string
    if results:
        s = results[0].summary()
        ok("mm" in s, "6.21 summary mm içeriyor")
        ok("USD" in s, "6.21 summary USD içeriyor")
        ok("skor" in s, "6.21 summary skor içeriyor")

    # 6.22 Farklı malzeme — sistem malzeme bağımsız çalışmalı
    mat_glass = eglass_epoxy()
    res_carbon = optimize_recipe(_fast_inp(prof_cyl, 3.0, mat), top_k=1)
    res_glass  = optimize_recipe(_fast_inp(prof_cyl, 3.0, mat_glass), top_k=1)
    ok(len(res_carbon) >= 1, "6.22 karbon → reçete üretir")
    ok(len(res_glass) >= 1, "6.22 cam → reçete üretir")

    # 6.23 Reçete maliyeti pozitif
    for r in results:
        ok(r.cost.total_cost_usd > 0, f"6.23 maliyet > 0: {r.cost.total_cost_usd:.2f}")

    # 6.24 Kapsama skoru [0, 100]
    for r in results:
        ok(0.0 <= r.coverage_pct <= 100.0,
           f"6.24 coverage_pct ∈ [0,100]: {r.coverage_pct:.1f}")

    # 6.25 quick_optimize varsayılan malzeme T700S
    res_default = quick_optimize(prof_cyl, 2.0, max_families=1, top_k=1)
    ok(len(res_default) >= 1, "6.25 varsayılan malzeme → reçete üretir")

    print(f"   [OK grupta {_passed - t0} / {_total - t0}]")


# ══════════════════════════════════════════════════════════════════════════════
# Koşu
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("Reçete Optimizer Doğrulama Süiti")
    print("=" * 60)

    test_group_material_database()
    test_group_laminate_builder()
    test_group_thickness_predictor()
    test_group_production_estimator()
    test_group_cost_estimator()
    test_group_recipe_optimizer()

    print("\n" + "=" * 60)
    if _failed:
        print(f"BAŞARISIZ: {len(_failed)} / {_total}")
        for msg in _failed:
            print(f"  ✗  {msg}")
        sys.exit(1)
    else:
        print(f"TÜM TESTLER GEÇTİ: {_passed} / {_total}")
        print("★★★ RECIPE OPTIMIZER READY ★★★")
