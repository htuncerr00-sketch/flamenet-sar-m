"""
tests/test_non_geodesic.py — Non-Geodezik Motor Doğrulama
==========================================================
Faz 22 Madde 1: non_geodesic_engine.py + non_geodesic_validator.py
için kapsamlı testler.

Test Grupları:
    G1 — Sınır Doğrulamaları & API Kontratı
    G2 — Geodezik Limit (λ=0 → Clairaut korunmalı)
    G3 — Non-Geodezik Davranış (λ ≠ 0)
    G4 — Sürtünme Kayma Sınırı
    G5 — Wells Bağımsız Doğrulayıcı (Koussios ↔ Wells)
    G6 — Sayısal Yakınsama & Geri-Uyumluluk

Beklenti: ≥ 50 assertion, < 10 saniye.
"""
from __future__ import annotations

import math
import os
import sys

# Backend yolunu ekle
sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                 '..', 'faz17_d1_backend'))

import numpy as np

from faz17_d1.core.geometry_engine import MandrelProfile
from faz17_d1.core.non_geodesic_engine import (
    NonGeodesicParams,
    NonGeodesicReport,
    solve_non_geodesic_path,
    compare_geodesic_vs_nongeodesic,
    _DRDZ_CACHE,
)
from faz17_d1.core.non_geodesic_validator import (
    WellsReport,
    CrossValidationReport,
    solve_wells_path,
    cross_validate,
    _DRDZ_CACHE_WELLS,
)
from faz17_d1.core.path_generator import WindingPath, WindingPoint


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


def _clear_caches() -> None:
    """Profil değiştirme arası dr/dz önbelleğini temizle."""
    _DRDZ_CACHE.clear()
    _DRDZ_CACHE_WELLS.clear()


# ══════════════════════════════════════════════════════════════════════════════
# Grup 1 — Sınır Doğrulamaları & API
# ══════════════════════════════════════════════════════════════════════════════

def test_group_validation() -> None:
    section("Grup 1: Sınır Doğrulamaları & API")
    t0 = _total
    prof = MandrelProfile.cylinder(300.0, 50.0)

    # 1.1 alpha_start_deg sınırları
    try:
        NonGeodesicParams(profile=prof, alpha_start_deg=0.0)
        ok(False, "1.1 α=0° ValueError bekleniyor")
    except ValueError:
        ok(True, "1.1 α=0° ValueError")

    try:
        NonGeodesicParams(profile=prof, alpha_start_deg=90.0)
        ok(False, "1.1 α=90° ValueError bekleniyor")
    except ValueError:
        ok(True, "1.1 α=90° ValueError")

    # 1.2 friction_coefficient sınırları
    try:
        NonGeodesicParams(profile=prof, alpha_start_deg=55.0,
                          friction_coefficient=-0.1)
        ok(False, "1.2 μ<0 ValueError bekleniyor")
    except ValueError:
        ok(True, "1.2 μ=-0.1 ValueError")

    try:
        NonGeodesicParams(profile=prof, alpha_start_deg=55.0,
                          friction_coefficient=1.5)
        ok(False, "1.2 μ>1 ValueError bekleniyor")
    except ValueError:
        ok(True, "1.2 μ=1.5 ValueError")

    # 1.3 integration_step_mm > 0
    try:
        NonGeodesicParams(profile=prof, alpha_start_deg=55.0,
                          integration_step_mm=0.0)
        ok(False, "1.3 dz=0 ValueError bekleniyor")
    except ValueError:
        ok(True, "1.3 dz=0 ValueError")

    # 1.4 n_circuits ≥ 1
    try:
        NonGeodesicParams(profile=prof, alpha_start_deg=55.0, n_circuits=0)
        ok(False, "1.4 n_circuits=0 ValueError bekleniyor")
    except ValueError:
        ok(True, "1.4 n_circuits=0 ValueError")

    # 1.5 Geçerli parametreler
    p = NonGeodesicParams(profile=prof, alpha_start_deg=55.0, lambda_slip=0.0)
    ok(p.alpha_start_deg == 55.0, "1.5 geçerli parametre kabul edilir")

    # 1.6 z_start ≈ z_end ValueError
    try:
        bad = NonGeodesicParams(profile=prof, alpha_start_deg=55.0,
                                  z_start_mm=10.0, z_end_mm=10.0)
        solve_non_geodesic_path(bad)
        ok(False, "1.6 z_start=z_end ValueError bekleniyor")
    except ValueError:
        ok(True, "1.6 z_start≈z_end ValueError")

    # 1.7 Çıktı tipleri
    p_ok = NonGeodesicParams(profile=prof, alpha_start_deg=55.0)
    rep = solve_non_geodesic_path(p_ok)
    ok(isinstance(rep, NonGeodesicReport), "1.7 NonGeodesicReport döner")
    ok(isinstance(rep.path, WindingPath), "1.7 .path WindingPath")
    ok(isinstance(rep.path.points[0], WindingPoint), "1.7 WindingPoint listesi")
    ok(isinstance(rep.z_samples_mm, np.ndarray), "1.7 z_samples np.ndarray")

    # 1.8 z_samples_mm uzunluk = n_steps + 1
    ok(len(rep.z_samples_mm) == rep.n_steps + 1,
       f"1.8 z örnek sayısı: {len(rep.z_samples_mm)} == {rep.n_steps + 1}")

    # 1.9 alpha_samples_deg uzunluk eşleşir
    ok(len(rep.alpha_samples_deg) == len(rep.z_samples_mm),
       "1.9 alpha_samples uzunluk eşleşir")

    # 1.10 spindle_samples_deg monoton artıyor (varsayılan λ=0, α=55°, ileri yön)
    ok(np.all(np.diff(rep.spindle_samples_deg) >= 0),
       "1.10 iş mili açısı monoton artıyor (ileri sarım)")

    # 1.11 WindingPath.points listesi boş değil
    ok(len(rep.path.points) > 0, f"1.11 path.points doldu: {len(rep.path.points)}")

    # 1.12 Fiber uzunluğu pozitif
    ok(rep.path.total_fiber_length_mm > 0,
       f"1.12 fiber_length > 0: {rep.path.total_fiber_length_mm:.1f}")

    print(f"   [OK grupta {_passed - t0} / {_total - t0}]")


# ══════════════════════════════════════════════════════════════════════════════
# Grup 2 — Geodezik Limit (λ=0 → Clairaut)
# ══════════════════════════════════════════════════════════════════════════════

def test_group_geodesic_limit() -> None:
    section("Grup 2: Geodezik Limit (λ=0)")
    t0 = _total
    _clear_caches()

    # 2.1 Silindir, λ=0: Clairaut sapması ≈ 0
    prof = MandrelProfile.cylinder(300.0, 50.0)
    p = NonGeodesicParams(profile=prof, alpha_start_deg=55.0, lambda_slip=0.0)
    rep = solve_non_geodesic_path(p)
    ok(rep.clairaut_drift_max < 1e-6,
       f"2.1 silindir λ=0 Clairaut sapma: {rep.clairaut_drift_max:.2e}")

    # 2.2 Silindir, λ=0: α sabit kalır (dr/dz=0 + geodezik)
    a_min, a_max = rep.alpha_range_deg
    ok(abs(a_max - a_min) < 1e-3,
       f"2.2 silindir λ=0 α sabit: [{a_min:.4f}°, {a_max:.4f}°]")

    # 2.3 Silindir, λ=0: ortalama α = başlangıç α
    ok(abs(rep.avg_alpha_deg - 55.0) < 1e-3,
       f"2.3 silindir avg α: {rep.avg_alpha_deg:.4f}° vs 55°")

    # 2.4 Koni, λ=0: Clairaut korunmalı (r sin α = c)
    _clear_caches()
    prof_cone = MandrelProfile.cone(300.0, 20.0, 50.0)
    p_cone = NonGeodesicParams(profile=prof_cone, alpha_start_deg=45.0,
                                 lambda_slip=0.0)
    rep_cone = solve_non_geodesic_path(p_cone)
    # Clairaut: r₀ × sin α₀ = r₁ × sin α₁
    c_start = 20.0 * math.sin(math.radians(45.0))
    a_end_rad = math.radians(float(rep_cone.alpha_samples_deg[-1]))
    c_end = 50.0 * math.sin(a_end_rad)
    ok(abs(c_start - c_end) < 0.01,
       f"2.4 koni Clairaut: c₀={c_start:.4f} vs c₁={c_end:.4f}")

    # 2.5 Koni: α azalır (r büyür → sin α küçülür)
    ok(rep_cone.alpha_samples_deg[-1] < rep_cone.alpha_samples_deg[0],
       f"2.5 koni α azaldı: {rep_cone.alpha_samples_deg[0]:.2f}° → "
       f"{rep_cone.alpha_samples_deg[-1]:.2f}°")

    # 2.6 Koni Clairaut sapma metriği < %1
    ok(rep_cone.clairaut_drift_max < 0.01,
       f"2.6 koni Clairaut sapma < %1: {rep_cone.clairaut_drift_max*100:.4f}%")

    # 2.7 Lift-off algılaması yok (geodezik, geniş aralık)
    ok(not rep.lift_off_detected, "2.7 geodezik silindir lift-off yok")
    ok(not rep_cone.lift_off_detected, "2.7 geodezik koni lift-off yok")

    # 2.8 Slip-safe (λ=0)
    ok(rep.is_slip_safe, "2.8 λ=0 slip-safe")
    ok(rep_cone.is_slip_safe, "2.8 koni λ=0 slip-safe")
    ok(rep.max_slip_ratio == 0.0, f"2.8 λ=0 slip_ratio=0: {rep.max_slip_ratio}")

    print(f"   [OK grupta {_passed - t0} / {_total - t0}]")


# ══════════════════════════════════════════════════════════════════════════════
# Grup 3 — Non-Geodezik Davranış (λ ≠ 0)
# ══════════════════════════════════════════════════════════════════════════════

def test_group_nongeodesic_behavior() -> None:
    section("Grup 3: Non-Geodezik Davranış")
    t0 = _total
    _clear_caches()

    prof = MandrelProfile.cylinder(300.0, 50.0)

    # 3.1 Silindir + λ>0: α monoton artar (geodezik terim=0, sürtünme→α↑)
    p_pos = NonGeodesicParams(profile=prof, alpha_start_deg=30.0,
                                lambda_slip=0.05, friction_coefficient=0.30)
    rep_pos = solve_non_geodesic_path(p_pos)
    diffs = np.diff(rep_pos.alpha_samples_deg)
    ok(np.all(diffs >= -1e-6),
       f"3.1 silindir λ>0 → α monoton artar: min Δα={diffs.min():.4f}")

    # 3.2 Silindir + λ<0: α monoton azalır
    _clear_caches()
    p_neg = NonGeodesicParams(profile=prof, alpha_start_deg=60.0,
                                lambda_slip=-0.05, friction_coefficient=0.30)
    rep_neg = solve_non_geodesic_path(p_neg)
    diffs_neg = np.diff(rep_neg.alpha_samples_deg)
    ok(np.all(diffs_neg <= 1e-6),
       f"3.2 silindir λ<0 → α monoton azalır: max Δα={diffs_neg.max():.4f}")

    # 3.3 |λ| büyüdükçe α değişimi artar
    _clear_caches()
    p_small = NonGeodesicParams(profile=prof, alpha_start_deg=45.0,
                                  lambda_slip=0.02, friction_coefficient=0.30,
                                  z_end_mm=100.0)
    rep_small = solve_non_geodesic_path(p_small)
    _clear_caches()
    p_large = NonGeodesicParams(profile=prof, alpha_start_deg=45.0,
                                  lambda_slip=0.10, friction_coefficient=0.30,
                                  z_end_mm=100.0)
    rep_large = solve_non_geodesic_path(p_large)
    delta_small = abs(rep_small.alpha_samples_deg[-1] - 45.0)
    delta_large = abs(rep_large.alpha_samples_deg[-1] - 45.0)
    ok(delta_large > delta_small,
       f"3.3 büyük λ → büyük α değişimi: {delta_large:.2f}° > {delta_small:.2f}°")

    # 3.4 Clairaut sapması λ ile büyür
    _clear_caches()
    p_lambda0 = NonGeodesicParams(profile=prof, alpha_start_deg=45.0,
                                    lambda_slip=0.0)
    rep_l0 = solve_non_geodesic_path(p_lambda0)
    _clear_caches()
    p_lambda1 = NonGeodesicParams(profile=prof, alpha_start_deg=45.0,
                                    lambda_slip=0.10,
                                    friction_coefficient=0.30,
                                    z_end_mm=100.0)
    rep_l1 = solve_non_geodesic_path(p_lambda1)
    ok(rep_l1.clairaut_drift_max > rep_l0.clairaut_drift_max,
       f"3.4 λ büyük → Clairaut sapma büyük: "
       f"{rep_l1.clairaut_drift_max:.4f} > {rep_l0.clairaut_drift_max:.4f}")

    # 3.5 compare_geodesic_vs_nongeodesic karşılaştırması
    _clear_caches()
    geo, ng = compare_geodesic_vs_nongeodesic(
        profile=prof, alpha_start_deg=50.0, lambda_slip=0.05,
        friction_coefficient=0.30,
    )
    ok(geo.clairaut_drift_max < ng.clairaut_drift_max,
       "3.5 geodezik Clairaut sapması < non-geodezik")
    ok(abs(geo.lambda_used) < 1e-9, "3.5 geodezik λ=0")
    ok(ng.lambda_used == 0.05, "3.5 non-geodezik λ=0.05")

    # 3.6 İş mili açısı her durumda pozitif (ileri sarım)
    ok(rep_pos.spindle_samples_deg[-1] > 0,
       f"3.6 iş mili sonu > 0: {rep_pos.spindle_samples_deg[-1]:.1f}°")
    ok(rep_neg.spindle_samples_deg[-1] > 0,
       f"3.6 iş mili sonu > 0 (λ<0 da): "
       f"{rep_neg.spindle_samples_deg[-1]:.1f}°")

    # 3.7 z örnekleri başlangıç ve bitiş noktalarını içerir
    ok(abs(rep_pos.z_samples_mm[0] - 0.0) < 1e-6,
       "3.7 z[0] = z_start")
    ok(abs(rep_pos.z_samples_mm[-1] - 300.0) < 1e-3,
       f"3.7 z[-1] = z_end: {rep_pos.z_samples_mm[-1]}")

    # 3.8 Lift-off riski uyarısı (α 90°'ye yaklaşırsa)
    _clear_caches()
    p_lift = NonGeodesicParams(profile=prof, alpha_start_deg=88.0,
                                 lambda_slip=0.3, friction_coefficient=0.30,
                                 z_end_mm=100.0)
    rep_lift = solve_non_geodesic_path(p_lift)
    ok(rep_lift.lift_off_detected or rep_lift.alpha_range_deg[1] >= 89.5,
       f"3.8 lift-off algılandı: max α = {rep_lift.alpha_range_deg[1]:.2f}°")
    has_warn = any('lift-off' in w.lower() or 'kayma' in w.lower()
                    for w in rep_lift.warnings)
    ok(has_warn, f"3.8 lift-off/kayma uyarısı: {rep_lift.warnings}")

    print(f"   [OK grupta {_passed - t0} / {_total - t0}]")


# ══════════════════════════════════════════════════════════════════════════════
# Grup 4 — Sürtünme Kayma Sınırı
# ══════════════════════════════════════════════════════════════════════════════

def test_group_friction_limit() -> None:
    section("Grup 4: Sürtünme Kayma Sınırı")
    t0 = _total
    _clear_caches()

    prof = MandrelProfile.cylinder(300.0, 50.0)

    # 4.1 |λ| = μ : sınırda, slip_safe=True
    p_edge = NonGeodesicParams(profile=prof, alpha_start_deg=45.0,
                                 lambda_slip=0.30, friction_coefficient=0.30)
    rep_edge = solve_non_geodesic_path(p_edge)
    ok(abs(rep_edge.max_slip_ratio - 1.0) < 1e-6,
       f"4.1 λ=μ: slip_ratio={rep_edge.max_slip_ratio:.4f}")
    ok(rep_edge.is_slip_safe,
       f"4.1 λ=μ sınırında slip_safe=True")

    # 4.2 |λ| < μ : slip_safe=True
    _clear_caches()
    p_safe = NonGeodesicParams(profile=prof, alpha_start_deg=45.0,
                                 lambda_slip=0.15, friction_coefficient=0.30)
    rep_safe = solve_non_geodesic_path(p_safe)
    ok(rep_safe.is_slip_safe,
       f"4.2 |λ|<μ slip_safe: ratio={rep_safe.max_slip_ratio:.3f}")
    ok(rep_safe.max_slip_ratio == 0.5,
       f"4.2 0.15/0.30=0.5: ratio={rep_safe.max_slip_ratio}")

    # 4.3 |λ| > μ : slip_safe=False + uyarı
    _clear_caches()
    p_unsafe = NonGeodesicParams(profile=prof, alpha_start_deg=45.0,
                                   lambda_slip=0.50,
                                   friction_coefficient=0.30,
                                   z_end_mm=80.0)
    rep_unsafe = solve_non_geodesic_path(p_unsafe)
    ok(not rep_unsafe.is_slip_safe,
       f"4.3 |λ|>μ slip_safe=False: ratio={rep_unsafe.max_slip_ratio:.3f}")
    ok(rep_unsafe.max_slip_ratio > 1.0,
       f"4.3 slip_ratio>1: {rep_unsafe.max_slip_ratio:.3f}")
    has_slip_warn = any('kayma' in w.lower() or 'risk' in w.lower()
                         for w in rep_unsafe.warnings)
    ok(has_slip_warn, f"4.3 kayma uyarısı: {rep_unsafe.warnings}")

    # 4.4 Negatif λ aynı |λ| ile aynı slip_ratio
    _clear_caches()
    p_neg = NonGeodesicParams(profile=prof, alpha_start_deg=55.0,
                                lambda_slip=-0.15, friction_coefficient=0.30)
    rep_neg = solve_non_geodesic_path(p_neg)
    ok(rep_neg.max_slip_ratio == 0.5,
       f"4.4 λ=-0.15/μ=0.30 → |λ|/μ=0.5: {rep_neg.max_slip_ratio}")

    print(f"   [OK grupta {_passed - t0} / {_total - t0}]")


# ══════════════════════════════════════════════════════════════════════════════
# Grup 5 — Wells Bağımsız Doğrulayıcı
# ══════════════════════════════════════════════════════════════════════════════

def test_group_wells_validator() -> None:
    section("Grup 5: Wells Bağımsız Doğrulayıcı")
    t0 = _total
    _clear_caches()

    prof = MandrelProfile.cylinder(300.0, 50.0)

    # 5.1 Silindir geodezik: Koussios ↔ Wells eşleşmesi mükemmel
    p = NonGeodesicParams(profile=prof, alpha_start_deg=55.0, lambda_slip=0.0)
    cv = cross_validate(p)
    ok(cv.is_consistent, f"5.1 silindir geo: {cv.summary()}")
    ok(cv.max_alpha_diff_deg < 0.01,
       f"5.1 max Δα < 0.01°: {cv.max_alpha_diff_deg:.6f}")

    # 5.2 Koni geodezik: tutarlı
    _clear_caches()
    prof_cone = MandrelProfile.cone(300.0, 25.0, 50.0)
    p_cone = NonGeodesicParams(profile=prof_cone, alpha_start_deg=45.0,
                                 lambda_slip=0.0)
    cv_cone = cross_validate(p_cone, alpha_tolerance_deg=0.5,
                               spindle_tolerance_deg=2.0)
    ok(cv_cone.is_consistent, f"5.2 koni geo: {cv_cone.summary()}")

    # 5.3 Silindir + λ küçük: tutarlı
    _clear_caches()
    p_ng = NonGeodesicParams(profile=prof, alpha_start_deg=55.0,
                                lambda_slip=0.05,
                                friction_coefficient=0.30,
                                z_end_mm=200.0)
    cv_ng = cross_validate(p_ng, alpha_tolerance_deg=1.0,
                             spindle_tolerance_deg=5.0)
    ok(cv_ng.is_consistent, f"5.3 silindir λ=0.05: {cv_ng.summary()}")

    # 5.4 CrossValidationReport alanları
    ok(cv.rms_alpha_diff_deg >= 0, "5.4 rms_alpha_diff ≥ 0")
    ok(cv.rms_spindle_diff_deg >= 0, "5.4 rms_spindle_diff ≥ 0")
    ok(cv.n_comparison_points > 0,
       f"5.4 n_comparison_points: {cv.n_comparison_points}")
    ok(cv.alpha_tolerance_deg == 0.5, "5.4 tol kaydedildi")

    # 5.5 Solve_wells_path WellsReport döner
    _clear_caches()
    wells = solve_wells_path(p)
    ok(isinstance(wells, WellsReport), "5.5 WellsReport döner")
    ok(len(wells.s_samples_mm) > 0, "5.5 s_samples doldu")
    ok(len(wells.z_samples_mm) == len(wells.alpha_samples_deg),
       "5.5 z ve α dizileri eşit uzunlukta")
    ok(wells.lambda_used == 0.0, "5.5 lambda_used kaydedildi")

    # 5.6 Wells z dizisi monoton ileri sarım için
    z_diffs = np.diff(wells.z_samples_mm)
    ok(np.all(z_diffs >= -1e-6) or np.all(z_diffs <= 1e-6),
       f"5.6 Wells z monoton: min_diff={z_diffs.min():.4f}")

    # 5.7 Wells silindirde α = sabit (Clairaut λ=0)
    a_range = wells.alpha_samples_deg.max() - wells.alpha_samples_deg.min()
    ok(a_range < 0.01,
       f"5.7 Wells silindir λ=0: α sabit (Δ={a_range:.6f}°)")

    # 5.8 Tolerans aşımı uyarı üretir (kubbede iki çözücü doğal olarak sapar)
    _clear_caches()
    prof_dome = MandrelProfile.dome_cylinder_dome(200.0, 50.0, 30.0)
    p_strict = NonGeodesicParams(profile=prof_dome, alpha_start_deg=20.0,
                                   lambda_slip=0.0,
                                   integration_step_mm=1.0)
    cv_strict = cross_validate(p_strict, alpha_tolerance_deg=1e-9,
                                 spindle_tolerance_deg=1e-9)
    ok(not cv_strict.is_consistent,
       "5.8 mikro-tolerans + kubbe → tutarsız")
    ok(len(cv_strict.warnings) > 0,
       f"5.8 uyarı listesi: {cv_strict.warnings}")

    # 5.9 Önceden hesaplanmış raporlar parametre olarak geçirilebilir
    _clear_caches()
    rep_k = solve_non_geodesic_path(p)
    rep_w = solve_wells_path(p)
    cv_provided = cross_validate(p, koussios_report=rep_k,
                                   wells_report=rep_w)
    ok(cv_provided.is_consistent,
       "5.9 ön-hesaplanmış raporlarla cross_validate")

    print(f"   [OK grupta {_passed - t0} / {_total - t0}]")


# ══════════════════════════════════════════════════════════════════════════════
# Grup 6 — Sayısal Yakınsama & Geri-Uyumluluk
# ══════════════════════════════════════════════════════════════════════════════

def test_group_convergence() -> None:
    section("Grup 6: Sayısal Yakınsama & Geri-Uyumluluk")
    t0 = _total
    _clear_caches()

    prof = MandrelProfile.cylinder(300.0, 50.0)

    # 6.1 Adım büyüklüğü değişince geodezik sapma sınırlı
    _clear_caches()
    p_coarse = NonGeodesicParams(profile=prof, alpha_start_deg=55.0,
                                   lambda_slip=0.0,
                                   integration_step_mm=2.0)
    rep_coarse = solve_non_geodesic_path(p_coarse)
    _clear_caches()
    p_fine = NonGeodesicParams(profile=prof, alpha_start_deg=55.0,
                                 lambda_slip=0.0,
                                 integration_step_mm=0.1)
    rep_fine = solve_non_geodesic_path(p_fine)
    ok(rep_coarse.clairaut_drift_max < 1e-6,
       f"6.1 kaba adım Clairaut: {rep_coarse.clairaut_drift_max:.2e}")
    ok(rep_fine.clairaut_drift_max < 1e-6,
       f"6.1 ince adım Clairaut: {rep_fine.clairaut_drift_max:.2e}")
    ok(rep_fine.n_steps > rep_coarse.n_steps,
       f"6.1 ince adım daha çok örnek: {rep_fine.n_steps} > {rep_coarse.n_steps}")

    # 6.2 Geri yön (z_end < z_start) çalışıyor
    _clear_caches()
    p_rev = NonGeodesicParams(profile=prof, alpha_start_deg=55.0,
                                z_start_mm=300.0, z_end_mm=0.0)
    rep_rev = solve_non_geodesic_path(p_rev)
    ok(rep_rev.z_samples_mm[0] > rep_rev.z_samples_mm[-1],
       f"6.2 geri yön: z[0]={rep_rev.z_samples_mm[0]} > z[-1]={rep_rev.z_samples_mm[-1]}")
    ok(rep_rev.n_steps > 0, f"6.2 adım sayısı > 0: {rep_rev.n_steps}")

    # 6.3 Kısmi z aralığı
    _clear_caches()
    p_part = NonGeodesicParams(profile=prof, alpha_start_deg=55.0,
                                 z_start_mm=50.0, z_end_mm=200.0)
    rep_part = solve_non_geodesic_path(p_part)
    ok(abs(rep_part.z_samples_mm[0] - 50.0) < 1e-6,
       f"6.3 kısmi z_start: {rep_part.z_samples_mm[0]}")
    ok(abs(rep_part.z_samples_mm[-1] - 200.0) < 1e-3,
       f"6.3 kısmi z_end: {rep_part.z_samples_mm[-1]}")

    # 6.4 İş mili açısı offset etkili
    _clear_caches()
    p_offset = NonGeodesicParams(profile=prof, alpha_start_deg=55.0,
                                   spindle_angle_offset_deg=45.0)
    rep_offset = solve_non_geodesic_path(p_offset)
    ok(abs(rep_offset.spindle_samples_deg[0] - 45.0) < 1e-3,
       f"6.4 spindle offset: {rep_offset.spindle_samples_deg[0]:.4f}° vs 45°")

    # 6.5 Konvergans: dz → 0 ise Clairaut sapması ↓
    _clear_caches()
    p_dz3 = NonGeodesicParams(profile=prof, alpha_start_deg=45.0,
                                lambda_slip=0.05,
                                friction_coefficient=0.30,
                                z_end_mm=100.0,
                                integration_step_mm=3.0)
    rep_dz3 = solve_non_geodesic_path(p_dz3)
    _clear_caches()
    p_dz_small = NonGeodesicParams(profile=prof, alpha_start_deg=45.0,
                                     lambda_slip=0.05,
                                     friction_coefficient=0.30,
                                     z_end_mm=100.0,
                                     integration_step_mm=0.1)
    rep_dz_small = solve_non_geodesic_path(p_dz_small)
    # İki çözüm aynı son α'ya yakınsamalı
    delta_dz3 = abs(rep_dz3.alpha_samples_deg[-1] - 45.0)
    delta_small = abs(rep_dz_small.alpha_samples_deg[-1] - 45.0)
    ok(abs(delta_dz3 - delta_small) < 0.5,
       f"6.5 dz konvergans: dz=3 → Δα={delta_dz3:.2f}°, dz=0.1 → Δα={delta_small:.2f}°")

    # 6.6 WindingPath path_generator API'siyle uyumlu
    _clear_caches()
    p_compat = NonGeodesicParams(profile=prof, alpha_start_deg=55.0)
    rep_compat = solve_non_geodesic_path(p_compat)
    path = rep_compat.path
    ok(hasattr(path, 'points'), "6.6 path.points var")
    ok(hasattr(path, 'n_circuits'), "6.6 path.n_circuits var")
    ok(hasattr(path, 'total_fiber_length_mm'), "6.6 path.total_fiber_length_mm var")
    ok(hasattr(path, 'clairaut_c'), "6.6 path.clairaut_c var")
    ok(hasattr(path, 'params'), "6.6 path.params var")
    # WindingPoint alan kontrolü
    pt = path.points[0]
    ok(hasattr(pt, 'x_mm') and hasattr(pt, 'a_deg') and
       hasattr(pt, 'feed') and hasattr(pt, 'z_fiber'),
       "6.6 WindingPoint alanları")

    # 6.7 z_fiber kümülatif olarak artar (fiber yay uzunluğu)
    z_fibers = [p.z_fiber for p in path.points]
    diffs = [z_fibers[i+1] - z_fibers[i] for i in range(len(z_fibers)-1)]
    ok(all(d >= 0 for d in diffs),
       f"6.7 z_fiber kümülatif artıyor: min Δ={min(diffs):.4f}")
    ok(z_fibers[0] == 0.0, f"6.7 z_fiber[0] = 0: {z_fibers[0]}")

    # 6.8 Reproducibility: aynı parametre → aynı sonuç (determinizm)
    _clear_caches()
    rep_a = solve_non_geodesic_path(
        NonGeodesicParams(profile=prof, alpha_start_deg=45.0, lambda_slip=0.05,
                          friction_coefficient=0.30, z_end_mm=100.0)
    )
    _clear_caches()
    rep_b = solve_non_geodesic_path(
        NonGeodesicParams(profile=prof, alpha_start_deg=45.0, lambda_slip=0.05,
                          friction_coefficient=0.30, z_end_mm=100.0)
    )
    ok(np.allclose(rep_a.alpha_samples_deg, rep_b.alpha_samples_deg),
       "6.8 determinizm: aynı parametre → aynı α dizisi")
    ok(np.allclose(rep_a.spindle_samples_deg, rep_b.spindle_samples_deg),
       "6.8 determinizm: aynı A dizisi")

    print(f"   [OK grupta {_passed - t0} / {_total - t0}]")


# ══════════════════════════════════════════════════════════════════════════════
# Koşu
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("Non-Geodezik Motor Doğrulama Süiti (Faz 22 Madde 1)")
    print("=" * 60)

    test_group_validation()
    test_group_geodesic_limit()
    test_group_nongeodesic_behavior()
    test_group_friction_limit()
    test_group_wells_validator()
    test_group_convergence()

    print("\n" + "=" * 60)
    if _failed:
        print(f"BAŞARISIZ: {len(_failed)} / {_total}")
        for msg in _failed:
            print(f"  ✗  {msg}")
        sys.exit(1)
    else:
        print(f"TÜM TESTLER GEÇTİ: {_passed} / {_total}")
        print("★★★ NON-GEODESIC ENGINE READY ★★★")
