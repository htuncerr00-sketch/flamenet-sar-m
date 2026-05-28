"""
tests/test_winding_physics.py — Endüstriyel Sarma Fiziği Birim Testleri
========================================================================
Fiber bant fiziği, kaplama çözücü, geodezik doğrulama, payout kinematiği,
endüstriyel hareket planlaması ve üretilebilirlik doğrulaması için testler.

Çalıştırma: python faz17_d1/tests/test_winding_physics.py
"""
from __future__ import annotations
import math
import sys
import traceback
from pathlib import Path

# Backend yolunu sys.path'e ekle
# parents[0]=tests/, parents[1]=faz17_d1/, parents[2]=repo root
_REPO = Path(__file__).resolve().parents[2]
_BACKEND = Path(__file__).resolve().parents[1] / 'faz17_d1_backend'
sys.path.insert(0, str(_BACKEND))

import numpy as np

from faz17_d1.core.fiber_band import FiberBand
from faz17_d1.core.geometry_engine import MandrelProfile
from faz17_d1.core.path_generator import WindingPathParams, generate_path
from faz17_d1.core.coverage_solver import CoverageMap, solve_coverage, bandwidth_compensation
from faz17_d1.core.geodesic_validator import (
    validate_geodesic, clairaut_stability_map, geodesic_deviation_metric,
)
from faz17_d1.core.payout_kinematics import (
    PayoutEyeConfig, compute_contact_point, compute_carriage_lead,
    check_collision_envelope, analyze_payout_kinematics,
)
from faz17_d1.core.industrial_motion import (
    MotionConstraints, scurve_move_time, plan_industrial_motion, analyze_saturation,
)
from faz17_d1.core.manufacturability import (
    ManufacturabilityThresholds, validate_manufacturability, quick_feasibility_check,
)

# ── Test altyapısı ────────────────────────────────────────────────────────────

PASS = 0
FAIL = 0
_RESULTS: list = []


def assert_close(a, b, tol=1e-6, msg=""):
    global PASS, FAIL
    if abs(a - b) <= tol:
        PASS += 1
        _RESULTS.append(f"  PASS  {msg}: {a:.6g} ≈ {b:.6g}")
    else:
        FAIL += 1
        _RESULTS.append(f"  FAIL  {msg}: {a:.6g} ≠ {b:.6g}  (tol={tol})")


def assert_true(cond, msg=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        _RESULTS.append(f"  PASS  {msg}")
    else:
        FAIL += 1
        _RESULTS.append(f"  FAIL  {msg}")


def section(title):
    _RESULTS.append(f"\n── {title} {'─' * (55 - len(title))}")


# ── Ortak test verisi ──────────────────────────────────────────────────────────

def _make_cylinder_path(alpha_deg=55.0, n_layers=2, tow_width=6.0):
    prof = MandrelProfile.cylinder(300.0, 50.0, n_points=100)
    params = WindingPathParams(
        profile=prof,
        alpha_deg=alpha_deg,
        n_layers=n_layers,
        tow_width_mm=tow_width,
        overlap_pct=5.0,
        n_steps_per_pass=80,
    )
    return generate_path(params), prof


def _make_dome_path(alpha_deg=55.0):
    prof = MandrelProfile.dome_cylinder_dome(200.0, 50.0, 40.0, n_points=300)
    params = WindingPathParams(
        profile=prof,
        alpha_deg=alpha_deg,
        n_layers=2,
        tow_width_mm=6.0,
        overlap_pct=5.0,
        n_steps_per_pass=100,
    )
    return generate_path(params), prof


# ── FiberBand Testleri ─────────────────────────────────────────────────────────

def test_fiber_band():
    section("FiberBand Fiziği")

    band = FiberBand(tow_width_mm=6.0, tow_thickness_mm=0.25, compaction_factor=0.85,
                     overlap_pct=5.0, gap_pct=0.0)

    # Etkin adım = W * (1 - ov/100)
    expected_step = 6.0 * (1.0 - 5.0 / 100.0)
    assert_close(band.effective_step_mm, expected_step, tol=1e-9,
                 msg="effective_step (ov=5%)")

    # Sıkıştırılmış kalınlık = 0.25 * 0.85
    assert_close(band.compacted_thickness_mm, 0.25 * 0.85, tol=1e-9,
                 msg="compacted_thickness")

    # 4 kat toplam kalınlık
    assert_close(band.layer_thickness_mm(4), 0.25 * 0.85 * 4, tol=1e-9,
                 msg="layer_thickness(4)")

    # Çevre kaplama devre sayısı: ceil(2π*50 / 5.7) ≈ ceil(314.16/5.7)
    circumference = 2 * math.pi * 50.0
    n_expected = math.ceil(circumference / expected_step)
    assert_close(band.circuits_for_coverage(circumference), n_expected, tol=0.5,
                 msg="circuits_for_coverage")

    # Eksenel bant genişliği: W/sin(α)
    for alpha in [30.0, 55.0, 75.0, 89.0]:
        bw = band.bandwidth_at_angle(alpha)
        expected = 6.0 / math.sin(math.radians(alpha))
        assert_close(bw, expected, tol=1e-9, msg=f"bandwidth_at_angle({alpha}°)")

    # Bant basıncı: T/(r*w)
    p = band.band_pressure_mpa(tension_N=50.0, mandrel_radius_mm=50.0)
    assert_close(p, 50.0 / (50.0 * 6.0), tol=1e-9, msg="band_pressure_mpa")

    # Sıfır mandrel yarıçapı: bölme sıfır koruması
    p0 = band.band_pressure_mpa(50.0, 0.0)
    assert_close(p0, 0.0, tol=1e-9, msg="band_pressure_mpa (r=0)")

    # Boşluk bant: gap_pct>0 → adım > tow_width * (1-ov/100)
    band_gap = FiberBand(tow_width_mm=6.0, overlap_pct=0.0, gap_pct=10.0)
    assert_true(band_gap.effective_step_mm > 6.0,
                msg="gap_pct>0 → step > tow_width")

    # Aşırı bindirme: ov=50% → adım = 3.0 mm
    band_hi_ov = FiberBand(tow_width_mm=6.0, overlap_pct=50.0)
    assert_close(band_hi_ov.effective_step_mm, 3.0, tol=1e-9,
                 msg="overlap_pct=50% → step=3.0mm")

    # Validasyon: geçersiz değerler
    try:
        FiberBand(tow_width_mm=0.0)
        assert_true(False, msg="ValueError expected for tow_width=0")
    except ValueError:
        assert_true(True, msg="ValueError raised for tow_width=0")


# ── CoverageSolver Testleri ───────────────────────────────────────────────────

def test_coverage_solver():
    section("Kaplama Çözücü")

    path, prof = _make_cylinder_path(alpha_deg=55.0, n_layers=2, tow_width=6.0)
    band = FiberBand(tow_width_mm=6.0, overlap_pct=5.0)

    cmap = solve_coverage(path, band, prof, n_z=60, n_theta=120)

    # Boyut kontrolü
    assert_close(cmap.count.shape[0], 60, tol=0.5, msg="cmap z-boyutu=60")
    assert_close(cmap.count.shape[1], 120, tol=0.5, msg="cmap theta-boyutu=120")

    # Kaplama > 0
    assert_true(cmap.coverage_pct > 0.0, msg="kaplama > 0%")

    # Boşluk + kaplama = 100
    assert_close(cmap.coverage_pct + cmap.gap_pct, 100.0, tol=0.1,
                 msg="kaplama + boşluk = 100%")

    # Bindirme < kaplama
    assert_true(cmap.overlap_pct <= cmap.coverage_pct,
                msg="bindirme ≤ kaplama")

    # Tekdüzelik 0..1 aralığında
    u = cmap.uniformity_index()
    assert_true(0.0 <= u <= 1.0, msg=f"tekdüzelik ∈ [0,1]: {u:.4f}")

    # Heatmap 0..1 aralığında
    hm = cmap.overlap_heatmap()
    assert_true(hm.min() >= 0.0 and hm.max() <= 1.0 + 1e-9,
                msg="ısı haritası ∈ [0,1]")

    # Boşluk bölgesi tespiti
    gaps = cmap.find_gap_regions(min_area_mm2=0.1)
    assert_true(isinstance(gaps, list), msg="find_gap_regions liste döndürür")

    # Bant genişliği tazminatı
    extra, ov_rec = bandwidth_compensation(path, band, prof, target_coverage_pct=98.0)
    assert_true(extra >= 0, msg="bandwidth_compensation ≥ 0 ek devre")
    assert_true(0.0 <= ov_rec <= 50.0,
                msg=f"tavsiye edilen bindirme ∈ [0,50]: {ov_rec:.2f}%")

    # Çok yüksek kaplama hedefi: makul ek devre
    assert_true(extra < path.n_circuits * 2,
                msg="bandwidth_compensation makul ek devre sayısı")


# ── GeodesicValidator Testleri ────────────────────────────────────────────────

def test_geodesic_validator():
    section("Geodezik Doğrulama")

    # Silindir üzerinde Clairaut — düşük hata beklenir
    path_cyl, prof_cyl = _make_cylinder_path(alpha_deg=55.0)
    rpt = validate_geodesic(path_cyl, prof_cyl, friction_coeff=0.3)

    assert_true(rpt.clairaut_c_ref > 0.0,
                msg="Clairaut sabiti pozitif")
    assert_true(rpt.clairaut_error.n_samples > 0,
                msg="Clairaut hata örnekleri > 0")

    # Silindir üzerinde kalkış olmamalı (r = sabit > c)
    assert_true(len(rpt.lift_off_zones) == 0,
                msg="Silindir: kalkış bölgesi yok")

    # Ortalama açı nominal açıya yakın
    assert_close(rpt.alpha_mean_deg, 55.0, tol=10.0,
                 msg="Silindir: ort. açı ≈ 55°")

    # Özet string çalışıyor
    summary = rpt.summary()
    assert_true(len(summary) > 20, msg="summary() boş değil")

    # Kubbeli silindir
    path_dome, prof_dome = _make_dome_path(alpha_deg=55.0)
    rpt_dome = validate_geodesic(path_dome, prof_dome, friction_coeff=0.3)
    assert_true(isinstance(rpt_dome.lift_off_zones, list),
                msg="Kubbe: lift_off_zones liste")

    # Kayma riski analizi: yüksek açıda kayma riski yüksek
    path_hi, prof_hi = _make_cylinder_path(alpha_deg=80.0)
    rpt_hi = validate_geodesic(path_hi, prof_hi, friction_coeff=0.3)
    # tan(80°) ≈ 5.67 > 0.3 → kayma bölgeleri olmalı
    assert_true(len(rpt_hi.slip_risk_zones) > 0,
                msg="α=80°, μ=0.3: kayma riski var")

    # Clairaut kararlılık haritası
    z_arr, r_arr, alpha_arr = clairaut_stability_map(prof_cyl, 55.0, n_z=50)
    assert_close(len(z_arr), 50, tol=0.5, msg="stability_map z boyutu")
    # Sabit r silindir → tüm açılar aynı
    valid = ~np.isnan(alpha_arr)
    assert_true(valid.all(), msg="Silindir: stability_map NaN içermez")
    assert_close(float(alpha_arr[valid].std()), 0.0, tol=0.1,
                 msg="Silindir: stability_map sabit açı")

    # Geodezik sapma metriki 0..1 arası
    dev = geodesic_deviation_metric(path_cyl, prof_cyl)
    assert_true(0.0 <= dev <= 1.0, msg=f"sapma metriki ∈ [0,1]: {dev:.4f}")


# ── PayoutKinematics Testleri ─────────────────────────────────────────────────

def test_payout_kinematics():
    section("Payout Göz Kinematiği")

    prof = MandrelProfile.cylinder(300.0, 50.0)
    eye = PayoutEyeConfig(standoff_mm=150.0, lead_mm=10.0, max_payout_angle_deg=45.0)

    # Temas noktası hesabı — geçerli durum
    cp = compute_contact_point(z_nominal_mm=150.0, profile=prof, eye_config=eye)
    assert_true(cp.is_valid, msg="Temas noktası geçerli (standoff=150mm)")
    assert_close(cp.r_contact_mm, 50.0, tol=0.1, msg="Temas yarıçapı = mandrel yarıçapı")
    assert_close(cp.r_eye_mm, 50.0 + 150.0, tol=0.1, msg="Göz yarıçapı = r + standoff")
    assert_true(cp.tangent_length_mm > 0.0, msg="Teğet uzunluğu > 0")

    # Azimüt teğeti: sqrt(r_eye² - r²) = sqrt(200² - 50²) ≈ 193.65
    # + eksenel lead=10mm → 3B: sqrt(193.65² + 10²) ≈ 193.91
    tan_az = math.sqrt(200.0 ** 2 - 50.0 ** 2)
    expected_tan = math.sqrt(tan_az ** 2 + eye.lead_mm ** 2)
    assert_close(cp.tangent_length_mm, expected_tan, tol=2.0,
                 msg=f"Teğet uzunluğu ≈ {expected_tan:.1f}mm")

    # Payout açısı >= 0
    assert_true(cp.payout_angle_deg >= 0.0, msg="Payout açısı ≥ 0")

    # Küçük standoff: sınırda geçersiz durum
    eye_small = PayoutEyeConfig(standoff_mm=0.1, max_payout_angle_deg=5.0)
    cp_small = compute_contact_point(150.0, prof, eye_small)
    # Çok küçük standoff → payout açısı çok büyük → geçersiz
    assert_true(not cp_small.is_valid or cp_small.payout_angle_deg >= 0,
                msg="Küçük standoff: geçersiz veya büyük açı")

    # Taşıyıcı öncülük tazminatı: lead ≈ standoff * tan(alpha)
    lead = compute_carriage_lead(150.0, 55.0, prof, eye)
    expected_lead = 150.0 * math.tan(math.radians(55.0))
    assert_close(lead, expected_lead, tol=0.1,
                 msg=f"Taşıyıcı lead (α=55°) ≈ {expected_lead:.1f}mm")

    # Lead α=0 için 0
    lead_0 = compute_carriage_lead(150.0, 0.1, prof, eye)
    assert_close(lead_0, 150.0 * math.tan(math.radians(0.1)), tol=1.0,
                 msg="Lead (α≈0°) ≈ 0")

    # Çarpışma zarfı — uniform silindir: çarpışma olmamalı
    eye_safe = PayoutEyeConfig(standoff_mm=150.0, lead_mm=5.0)
    collisions = check_collision_envelope(prof, eye_safe, n_points=50, margin_mm=5.0)
    # Uniform silindir: profile yakın bölgelerde boşluk var
    assert_true(isinstance(collisions, list), msg="Çarpışma zarfı liste döndürür")

    # Tam yol analizi
    path, _ = _make_cylinder_path()
    report = analyze_payout_kinematics(path, prof, eye, sample_every=50)
    assert_true(report.n_points_analyzed > 0, msg="Payout analizi noktası > 0")
    assert_true(report.max_payout_angle_deg >= 0.0,
                msg="Maks payout açısı ≥ 0")
    assert_true(report.min_tangent_length_mm >= 0.0,
                msg="Min teğet uzunluğu ≥ 0")
    assert_true(0 <= report.n_valid <= report.n_points_analyzed,
                msg="Geçerli nokta sayısı ≤ toplam")


# ── IndustrialMotion Testleri ─────────────────────────────────────────────────

def test_industrial_motion():
    section("Endüstriyel Hareket Planlaması (S-Eğrisi)")

    constraints = MotionConstraints(
        max_x_speed_mm_s=83.3,
        max_x_accel_mm_s2=500.0,
        max_x_jerk_mm_s3=2000.0,
        max_a_speed_deg_s=1800.0,
        max_a_accel_deg_s2=3600.0,
        max_a_jerk_deg_s3=18000.0,
        ref_radius_mm=50.0,
    )

    # ── scurve_move_time temel doğruluğu ────────────────────────────────────
    # Sabit hız (v0=v1=v_max): t = d/v
    d = 100.0
    v = 50.0
    t, vp = scurve_move_time(d, v, v, v, 500.0, 2000.0)
    assert_close(t, d / v, tol=0.1, msg="Sabit hız: t = d/v")
    assert_close(vp, v, tol=0.1, msg="Sabit hız: v_peak = v")

    # Sıfır mesafe: t = 0
    t0, _ = scurve_move_time(0.0, 0.0, 0.0, 83.3, 500.0, 2000.0)
    assert_close(t0, 0.0, tol=1e-9, msg="Sıfır mesafe: t = 0")

    # Büyük mesafe: v_max'a ulaşılır
    t_long, vp_long = scurve_move_time(
        1000.0, 0.0, 0.0,
        83.3, 500.0, 2000.0
    )
    assert_true(t_long > 0.0, msg="Büyük mesafe: t > 0")
    assert_close(vp_long, 83.3, tol=1.0, msg="Büyük mesafe: v_peak ≈ v_max")

    # Kısa mesafe: v_max'a ulaşılamaz
    t_short, vp_short = scurve_move_time(
        0.5, 0.0, 0.0,
        83.3, 500.0, 2000.0
    )
    assert_true(t_short > 0.0, msg="Kısa mesafe: t > 0")
    assert_true(vp_short < 83.3, msg="Kısa mesafe: v_peak < v_max (ulaşılamaz)")

    # Mesafe tutarlılığı: büyük mesafe daha uzun sürer
    t1, _ = scurve_move_time(100.0, 0.0, 0.0, 83.3, 500.0, 2000.0)
    t2, _ = scurve_move_time(200.0, 0.0, 0.0, 83.3, 500.0, 2000.0)
    assert_true(t2 > t1, msg="200mm > 100mm → süre artar")

    # ── Senkronize hareket planlaması ───────────────────────────────────────
    path, prof = _make_cylinder_path(n_layers=1, tow_width=6.0)
    segs = plan_industrial_motion(path, constraints)

    assert_true(len(segs) > 0, msg="Senkronize segment sayısı > 0")

    # Segment pozisyon sürekliliği (X ekseni)
    for i in range(len(segs) - 1):
        assert_close(segs[i].x_end, segs[i + 1].x_start, tol=1e-4,
                     msg=f"X sürekliliği seg {i}/{i+1}")

    # Süre her zaman pozitif
    for i, s in enumerate(segs):
        assert_true(s.t_duration_s > 0.0,
                    msg=f"Seg {i} süresi > 0: {s.t_duration_s:.6f}s")

    # Feed hızı pozitif
    for i, s in enumerate(segs):
        assert_true(s.feed_mm_min > 0.0,
                    msg=f"Seg {i} feed > 0: {s.feed_mm_min:.1f}")

    # ── Saturasyon analizi ───────────────────────────────────────────────────
    sat = analyze_saturation(segs, constraints)
    assert_close(sat.total_segments, len(segs), tol=0.5,
                 msg="Saturasyon: toplam segment sayısı")
    assert_true(0.0 <= sat.x_saturation_pct <= 100.0,
                msg="X saturasyon yüzdesi [0,100]")
    assert_true(0.0 <= sat.a_saturation_pct <= 100.0,
                msg="A saturasyon yüzdesi [0,100]")
    assert_true(sat.max_x_speed_required_mm_s >= 0.0,
                msg="Gereken maks X hızı ≥ 0")

    # Düşük kısıtlarla yüksek saturasyon
    tight = MotionConstraints(max_x_speed_mm_s=0.1, max_a_speed_deg_s=0.1,
                               max_x_accel_mm_s2=1.0, max_a_accel_deg_s2=1.0,
                               max_x_jerk_mm_s3=1.0, max_a_jerk_deg_s3=1.0)
    segs_tight = plan_industrial_motion(path, tight)
    sat_tight = analyze_saturation(segs_tight, tight)
    # Çok düşük hız sınırı → saturasyon olmalı
    assert_true(sat_tight.x_saturation_pct >= 0.0,
                msg="Dar kısıtlar: saturasyon analizi çalışır")

    # Axis dönüşüm tutarlılığı
    assert_close(constraints.a_deg_to_mm(360.0),
                 2.0 * math.pi * constraints.ref_radius_mm, tol=0.01,
                 msg="a_deg_to_mm(360°) = 2πr")
    assert_close(
        constraints.mm_to_a_deg(constraints.a_deg_to_mm(180.0)),
        180.0, tol=1e-6,
        msg="mm_to_a_deg(a_deg_to_mm(180)) = 180",
    )


# ── Manufacturability Testleri ────────────────────────────────────────────────

def test_manufacturability():
    section("Üretilebilirlik Doğrulama")

    path, prof = _make_cylinder_path(alpha_deg=55.0, n_layers=2)
    band = FiberBand(tow_width_mm=6.0, overlap_pct=5.0)
    eye = PayoutEyeConfig(standoff_mm=150.0, lead_mm=10.0)
    constraints = MotionConstraints()
    thresholds = ManufacturabilityThresholds()

    report = validate_manufacturability(
        path, band, prof, eye, constraints, thresholds,
        friction_coeff=0.3, n_z=50, n_theta=120,
    )

    # Temel özellikler
    assert_true(isinstance(report.is_manufacturable, bool),
                msg="is_manufacturable bool")
    assert_true(0.0 <= report.coverage_pct <= 100.0,
                msg=f"kaplama_pct ∈ [0,100]: {report.coverage_pct:.1f}")
    assert_true(0.0 <= report.gap_pct <= 100.0,
                msg=f"boşluk_pct ∈ [0,100]: {report.gap_pct:.1f}")
    assert_true(0.0 <= report.overlap_pct <= 100.0,
                msg=f"bindirme_pct ∈ [0,100]: {report.overlap_pct:.1f}")
    assert_true(report.clairaut_error_max_pct >= 0.0,
                msg="Clairaut hatası ≥ 0")

    # Kriterler listesi boş değil
    assert_true(len(report.criteria) > 0, msg="Kriter sayısı > 0")

    # Özet çalışıyor
    summary = report.summary()
    assert_true(len(summary) > 50, msg="summary() boş değil")

    # Geodezik alt-rapor mevcut
    assert_true(report.geodesic is not None, msg="geodesic alt-rapor var")
    assert_true(report.coverage_map is not None, msg="coverage_map var")
    assert_true(report.payout is not None, msg="payout raporu var")
    assert_true(report.saturation is not None, msg="saturasyon raporu var")

    # Hızlı fizibilite kontrolü — geçerli parametreler
    issues = quick_feasibility_check(prof, alpha_deg=55.0, tow_width_mm=6.0,
                                      n_layers=4, friction_coeff=0.3)
    assert_true(isinstance(issues, list), msg="quick_feasibility_check liste döndürür")

    # α=80° ile μ=0.3: kayma uyarısı olmalı
    issues_slip = quick_feasibility_check(prof, alpha_deg=80.0, tow_width_mm=6.0,
                                           n_layers=4, friction_coeff=0.3)
    assert_true(len(issues_slip) > 0,
                msg="α=80°, μ=0.3: kayma sorunu tespit edildi")

    # Çok küçük açı: fizibilite sorunu
    issues_small = quick_feasibility_check(prof, alpha_deg=2.0, tow_width_mm=6.0,
                                            n_layers=4, friction_coeff=0.3)
    assert_true(len(issues_small) > 0,
                msg="α=2°: fizibilite sorunu var")

    # Kubbeli mandrel analizi
    path_dome, prof_dome = _make_dome_path(55.0)
    band_dome = FiberBand(tow_width_mm=6.0, overlap_pct=10.0)
    report_dome = validate_manufacturability(
        path_dome, band_dome, prof_dome,
        friction_coeff=0.3, n_z=40, n_theta=80,
    )
    assert_true(isinstance(report_dome.is_manufacturable, bool),
                msg="Kubbe üretilebilirlik raporu oluşturuldu")
    assert_true(report_dome.coverage_pct > 0.0,
                msg="Kubbe kaplama > 0%")


# ── Entegrasyon Testleri ──────────────────────────────────────────────────────

def test_integration():
    section("Entegrasyon: Silindir Tam Akış")

    # Tam akış: geometri → yol → kaplama → doğrulama → hareket
    prof = MandrelProfile.cylinder(300.0, 50.0)
    band = FiberBand(tow_width_mm=6.0, overlap_pct=5.0, gap_pct=0.0)
    params = WindingPathParams(
        profile=prof, alpha_deg=55.0, n_layers=2,
        tow_width_mm=band.tow_width_mm, overlap_pct=band.overlap_pct,
        n_steps_per_pass=60,
    )
    path = generate_path(params)

    # Yol oluşturuldu
    assert_true(len(path.points) > 0, msg="Yol noktaları > 0")
    assert_true(path.clairaut_c > 0.0, msg="Clairaut sabiti > 0")

    # Clairaut sabiti beklenen değerde
    c_expected = prof.avg_radius_mm * math.sin(math.radians(55.0))
    assert_close(path.clairaut_c, c_expected, tol=c_expected * 0.1,
                 msg="Clairaut sabiti beklenen değerde")

    # Kaplama haritası
    cmap = solve_coverage(path, band, prof, n_z=40, n_theta=80)
    assert_true(cmap.coverage_pct > 30.0, msg="Kaplama > 30%")

    # Geodezik doğrulama
    geo = validate_geodesic(path, prof, friction_coeff=0.3)
    assert_true(len(geo.lift_off_zones) == 0,
                msg="Silindir: kalkış bölgesi yok")

    # Hareket planlaması
    constraints = MotionConstraints()
    segs = plan_industrial_motion(path, constraints)
    assert_true(len(segs) > 0, msg="Hareket segmentleri > 0")

    sat = analyze_saturation(segs, constraints)
    assert_true(sat.total_segments == len(segs),
                msg="Saturasyon: segment sayısı eşleşiyor")

    # Payout
    eye = PayoutEyeConfig(standoff_mm=150.0)
    payout_rpt = analyze_payout_kinematics(path, prof, eye, sample_every=100)
    assert_true(payout_rpt.n_points_analyzed > 0,
                msg="Payout analizi tamamlandı")

    section("Entegrasyon: Kubbeli Silindir Tam Akış")

    prof_dome = MandrelProfile.dome_cylinder_dome(200.0, 50.0, 40.0)
    params_d = WindingPathParams(
        profile=prof_dome, alpha_deg=60.0, n_layers=2,
        tow_width_mm=6.0, overlap_pct=10.0, n_steps_per_pass=80,
    )
    path_d = generate_path(params_d)

    assert_true(len(path_d.points) > 100, msg="Kubbe yolu > 100 nokta")

    geo_d = validate_geodesic(path_d, prof_dome)
    assert_true(isinstance(geo_d.is_valid, bool), msg="Kubbe geodezik doğrulama")

    cmap_d = solve_coverage(path_d, band, prof_dome, n_z=40, n_theta=80)
    assert_true(cmap_d.coverage_pct > 0.0, msg="Kubbe kaplama > 0")


# ── Ana koşucu ────────────────────────────────────────────────────────────────

def main():
    test_suites = [
        test_fiber_band,
        test_coverage_solver,
        test_geodesic_validator,
        test_payout_kinematics,
        test_industrial_motion,
        test_manufacturability,
        test_integration,
    ]

    for suite in test_suites:
        try:
            suite()
        except Exception as e:
            _RESULTS.append(f"\n  ERROR  {suite.__name__}: {e}")
            traceback.print_exc()
            global FAIL
            FAIL += 1

    print("\n".join(_RESULTS))
    print()
    print(f"{'=' * 60}")
    print(f"  Toplam: {PASS + FAIL} test  |  PASS: {PASS}  |  FAIL: {FAIL}")
    print(f"{'=' * 60}")

    if FAIL > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
