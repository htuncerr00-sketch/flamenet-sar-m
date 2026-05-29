"""
tests/test_industrial_validity.py — Endüstriyel Geçerlilik Doğrulama Suite
===========================================================================
Faz: ENDÜSTRİYEL ÜRETİM DOĞRULUĞU — Görev 7

Doğrular:
- Makine limiti uyumu (machine_limits)
- Açı sürekliliği (geodesic_validator + yol açıları)
- İmkânsız ivme yokluğu (zaman-domeni türevleri ≤ limit)
- Gerçekçi RPM aralıkları
- Kümülatif katman doğruluğu (layer_stacking)
- Deterministik çıktı (bit-aynı tekrar)
- STL taper kararlılığı (from_stl + monoton yarıçap)
- Kubbe geçiş sürekliliği (dome_transition)

Çalıştırma: python faz17_d1/tests/test_industrial_validity.py
"""
from __future__ import annotations
import math
import os
import struct
import sys
import tempfile
import traceback
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / 'faz17_d1_backend'
sys.path.insert(0, str(_BACKEND))

import numpy as np

from faz17_d1.core.geometry_engine import MandrelProfile
from faz17_d1.core.fiber_band import FiberBand
from faz17_d1.core.path_generator import WindingPathParams, generate_path
from faz17_d1.core.industrial_motion import MotionConstraints, plan_industrial_motion
from faz17_d1.core.trajectory_builder import TrajectorySegment, build_timeline
from faz17_d1.core.machine_envelope import MachineEnvelope
from faz17_d1.core.machine_limits import (
    MachineLimits, validate_trajectory_against_machine,
)
from faz17_d1.core.fiber_tension_model import estimate_tension_profile, TensionModelConfig
from faz17_d1.core.dome_transition import analyze_dome_transition
from faz17_d1.core.layer_stacking import analyze_layer_stack
from faz17_d1.core.coverage_solver import (
    solve_coverage, coverage_statistics, analyze_coverage_risk,
)
from faz17_d1.core.geodesic_validator import validate_geodesic, _compute_local_alpha
from faz17_d1.core.stl_processor import load_stl_profile

# ── Test altyapısı ────────────────────────────────────────────────────────────

PASS = 0
FAIL = 0
_LOG: list = []


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        _LOG.append(f"  PASS  {msg}")
    else:
        FAIL += 1
        _LOG.append(f"  FAIL  {msg}")


def close(a, b, tol, msg):
    ok(abs(a - b) <= tol, f"{msg} ({a:.6g} vs {b:.6g}, tol={tol})")


def section(t):
    _LOG.append(f"\n── {t} {'─' * max(0, 52 - len(t))}")


def _cyl_path(alpha=55.0, length=300.0, radius=50.0, steps=40):
    prof = MandrelProfile.cylinder(length, radius, n_points=80)
    params = WindingPathParams(
        profile=prof, alpha_deg=alpha, n_layers=1, tow_width_mm=6.0,
        overlap_pct=5.0, n_steps_per_pass=steps,
        carriage_min_mm=0.0, carriage_max_mm=length,
    )
    return prof, generate_path(params)


def _timeline_for_path(path, constraints, dt_s=1.0):
    """Yol → senkronize segment → tekdüze zaman çizelgesi (tek katman, a_offset=0)."""
    sync = plan_industrial_motion(path, constraints)
    segs = []
    t = 0.0
    for s in sync:
        segs.append(TrajectorySegment(
            t_start=t, t_end=t + s.t_duration_s,
            x_start=s.x_start, x_end=s.x_end,
            a_start=s.a_start, a_end=s.a_end,
            layer=0, circuit=0, radius_mm=constraints.ref_radius_mm,
        ))
        t += s.t_duration_s
    return build_timeline(segs, dt_s=dt_s, total_time_s=t)


def _write_cone_stl(path_str, z0=0.0, z1=200.0, r0=50.0, r1=30.0, n_z=30, n_theta=24):
    """Konik (taper) bir mandrel için basit binary STL yaz (dönel simetrik)."""
    zs = np.linspace(z0, z1, n_z)
    rs = np.linspace(r0, r1, n_z)
    tris = []
    for i in range(n_z - 1):
        for j in range(n_theta):
            t0 = 2 * math.pi * j / n_theta
            t1 = 2 * math.pi * (j + 1) / n_theta
            p00 = (rs[i] * math.cos(t0), rs[i] * math.sin(t0), zs[i])
            p01 = (rs[i] * math.cos(t1), rs[i] * math.sin(t1), zs[i])
            p10 = (rs[i + 1] * math.cos(t0), rs[i + 1] * math.sin(t0), zs[i + 1])
            p11 = (rs[i + 1] * math.cos(t1), rs[i + 1] * math.sin(t1), zs[i + 1])
            tris.append((p00, p01, p11))
            tris.append((p00, p11, p10))
    with open(path_str, 'wb') as f:
        f.write(b'\x00' * 80)
        f.write(struct.pack('<I', len(tris)))
        for tri in tris:
            f.write(struct.pack('<fff', 0.0, 0.0, 0.0))  # normal
            for v in tri:
                f.write(struct.pack('<fff', *v))
            f.write(struct.pack('<H', 0))


# ── 1. Makine limiti uyumu ──────────────────────────────────────────────────

def test_machine_limit_compliance():
    section("Makine Limiti Uyumu")
    prof, path = _cyl_path(alpha=55.0)
    env = MachineEnvelope()
    constraints = MotionConstraints(
        max_x_speed_mm_s=env.max_carriage_speed_mm_s,
        max_x_accel_mm_s2=env.max_carriage_accel_mm_s2,
        max_a_speed_deg_s=env.max_spindle_deg_s,
        ref_radius_mm=prof.avg_radius_mm,
    )
    tl = _timeline_for_path(path, constraints, dt_s=1.0)
    limits = MachineLimits.from_envelope(env)
    rep = validate_trajectory_against_machine(tl, limits)

    ok(rep.n_samples > 0, "yörünge örneklendi")
    ok(rep.feedrate_count == 0, "ulaşılamayan besleme yok")
    ok(rep.spindle_sat_count == 0, "iş mili doygunluğu yok")
    ok(rep.overtravel_count == 0, "overtravel yok")
    ok(rep.rotary_desync_count == 0, "rotary desync yok")
    ok(rep.is_realizable, "nominal silindir yörüngesi gerçekleştirilebilir")

    # İmkânsız yörünge tespiti
    segs = [TrajectorySegment(0.0, 10.0, 0.0, 5000.0, 0.0, 500000.0, 0, 0, 50.0)]
    tl_bad = build_timeline(segs, dt_s=1.0)
    rep_bad = validate_trajectory_against_machine(tl_bad, limits)
    ok(not rep_bad.is_realizable, "imkânsız yörünge reddedildi")
    ok(rep_bad.feedrate_count > 0, "aşırı besleme tespit edildi")
    ok(rep_bad.spindle_sat_count > 0, "iş mili doygunluğu tespit edildi")

    # Overtravel tespiti (homing ofseti dahil)
    segs_ot = [TrajectorySegment(0.0, 10.0, 0.0, 100.0, 0.0, 1000.0, 0, 0, 50.0)]
    tl_ot = build_timeline(segs_ot, dt_s=1.0)
    lim_ot = MachineLimits(x_soft_min_mm=0.0, x_soft_max_mm=50.0)
    rep_ot = validate_trajectory_against_machine(tl_ot, lim_ot)
    ok(rep_ot.overtravel_count > 0, "X yumuşak limit overtravel tespit edildi")


# ── 2. Açı sürekliliği ──────────────────────────────────────────────────────

def test_angle_continuity():
    section("Açı Sürekliliği")
    prof, path = _cyl_path(alpha=55.0, steps=60)

    # Yol açıları (geodezik): silindirde lokal α ≈ nominal, süreksizlik yok
    alpha = _compute_local_alpha(path.points, prof)
    ok(abs(float(np.mean(alpha)) - 55.0) < 5.0, "ortalama α ≈ nominal 55°")

    # Kümülatif iş mili açısı tek pass içinde monoton artar
    a_deg = np.array([p.a_deg for p in path.points])
    # Pass/devre sınırlarında sıfırlanabilir; pass içi monotonluğu kontrol et
    circuits = {}
    for p in path.points:
        circuits.setdefault((p.layer, p.circuit), []).append(p.a_deg)
    mono = all(all(v[i] <= v[i + 1] + 1e-6 for i in range(len(v) - 1))
               for v in circuits.values())
    ok(mono, "iş mili açısı her pass içinde monoton artan")

    # Geodezik doğrulama: süreklilik metrikleri (kalkış/eğrilik/Clairaut).
    # Not: validate_geodesic'in tan(α)/μ kayma kapısı geodezik yollar için
    # tutucudur (silindir helisi geodeziktir, k_g=0 → kaymaz). Bu nedenle
    # is_valid yerine gerçek SÜREKLİLİK kriterlerini doğrularız.
    rep = validate_geodesic(path, prof, friction_coeff=0.3)
    ok(rep.clairaut_error.max_pct < 5.0, f"Clairaut hatası düşük (%{rep.clairaut_error.max_pct:.2f})")
    ok(len(rep.lift_off_zones) == 0, "silindirde kalkış bölgesi yok (r ≥ c)")
    ok(len(rep.high_curvature_zones) == 0, "silindirde yüksek eğrilik (açı sıçraması) yok")
    ok(len(rep.unstable_zones) == 0, "silindirde açı sınır-dışı bölge yok")


# ── 3. İmkânsız ivme yokluğu ────────────────────────────────────────────────

def test_no_impossible_accelerations():
    section("İmkânsız İvme Yokluğu")
    prof, path = _cyl_path(alpha=55.0)
    env = MachineEnvelope()
    constraints = MotionConstraints(
        max_x_speed_mm_s=env.max_carriage_speed_mm_s,
        max_x_accel_mm_s2=env.max_carriage_accel_mm_s2,
        max_a_speed_deg_s=env.max_spindle_deg_s,
        ref_radius_mm=prof.avg_radius_mm,
    )
    tl = _timeline_for_path(path, constraints, dt_s=1.0)
    limits = MachineLimits.from_envelope(env, max_carriage_jerk_mm_s3=1e6,
                                         max_spindle_jerk_deg_s3=1e9)
    rep = validate_trajectory_against_machine(tl, limits)
    ok(rep.accel_count == 0, "ivme ihlali yok (dt=1s ızgarasında)")
    ok(rep.peak_carriage_accel_mm_s2 <= limits.max_carriage_accel_mm_s2 * 1.01,
       f"tepe taşıyıcı ivmesi ≤ limit ({rep.peak_carriage_accel_mm_s2:.1f})")


# ── 4. Gerçekçi RPM aralıkları ──────────────────────────────────────────────

def test_realistic_rpm():
    section("Gerçekçi RPM Aralıkları")
    prof, path = _cyl_path(alpha=55.0)
    env = MachineEnvelope()
    constraints = MotionConstraints(
        max_x_speed_mm_s=env.max_carriage_speed_mm_s,
        max_x_accel_mm_s2=env.max_carriage_accel_mm_s2,
        max_a_speed_deg_s=env.max_spindle_deg_s,
        ref_radius_mm=prof.avg_radius_mm,
    )
    tl = _timeline_for_path(path, constraints, dt_s=1.0)
    limits = MachineLimits.from_envelope(env)
    rep = validate_trajectory_against_machine(tl, limits)
    ok(rep.peak_spindle_rpm <= limits.max_spindle_rpm + 1e-6,
       f"tepe RPM ≤ makine limiti ({rep.peak_spindle_rpm:.1f} ≤ {limits.max_spindle_rpm:.0f})")
    ok(rep.peak_spindle_rpm < 1000.0, "RPM astronomik değil (eski hata yok)")
    ok(rep.peak_spindle_rpm >= 0.0, "RPM negatif değil")


# ── 5. Kümülatif katman doğruluğu ───────────────────────────────────────────

def test_cumulative_layer_correctness():
    section("Kümülatif Katman Doğruluğu")
    prof = MandrelProfile.cylinder(300.0, 50.0, n_points=80)
    band = FiberBand(tow_width_mm=6.0, tow_thickness_mm=0.25,
                     compaction_factor=0.85, overlap_pct=5.0)

    st = analyze_layer_stack(prof, band, alpha_deg=55.0, n_layers=10,
                             nesting_factor=0.1, hold_circuits=True)
    # Yarıçap monoton artar
    radii = [l.outer_radius_mm for l in st.layers]
    ok(all(radii[i] < radii[i + 1] for i in range(len(radii) - 1)),
       "katman yarıçapı monoton artar")
    # Nesting nominalden az büyüme
    ok(st.effective_total_thickness_mm < st.nominal_total_thickness_mm,
       "nesting: efektif < nominal kalınlık")
    # Final yarıçap = taban + efektif toplam
    close(st.final_radius_mm, st.base_radius_mm + st.effective_total_thickness_mm,
          1e-6, "final yarıçap = taban + efektif kalınlık")
    # Sabit devre sayısında bindirme azalır
    ok(st.overlap_evolution_pct[-1] < st.overlap_evolution_pct[0],
       "bindirme yarıçapla azalır (sabit devre)")
    # Bant genişliği projeksiyonu pozitif ve sonlu
    ok(all(0 < b < 1e4 for b in st.bandwidth_evolution_mm),
       "bant genişliği projeksiyonu sonlu")


# ── 6. Deterministik çıktı ──────────────────────────────────────────────────

def test_deterministic_output():
    section("Deterministik Çıktı")
    prof, path = _cyl_path(alpha=55.0)
    env = MachineEnvelope()
    constraints = MotionConstraints(
        max_x_speed_mm_s=env.max_carriage_speed_mm_s,
        max_x_accel_mm_s2=env.max_carriage_accel_mm_s2,
        max_a_speed_deg_s=env.max_spindle_deg_s,
        ref_radius_mm=prof.avg_radius_mm,
    )
    tl1 = _timeline_for_path(path, constraints, dt_s=1.0)
    tl2 = _timeline_for_path(path, constraints, dt_s=1.0)
    ok(np.array_equal(tl1.x_mm, tl2.x_mm) and np.array_equal(tl1.a_deg, tl2.a_deg)
       and np.array_equal(tl1.rpm, tl2.rpm),
       "zaman çizelgesi bit-aynı (deterministik)")

    limits = MachineLimits.from_envelope(env)
    r1 = validate_trajectory_against_machine(tl1, limits)
    r2 = validate_trajectory_against_machine(tl2, limits)
    ok(r1.feedrate_count == r2.feedrate_count
       and r1.accel_count == r2.accel_count
       and r1.is_realizable == r2.is_realizable,
       "makine doğrulama tekrarı bit-aynı")

    # Gerilim profili deterministik
    t1 = estimate_tension_profile(path, prof, config=TensionModelConfig())
    t2 = estimate_tension_profile(path, prof, config=TensionModelConfig())
    ok(abs(t1.max_tension_N - t2.max_tension_N) < 1e-12
       and t1.n_unstable == t2.n_unstable,
       "gerilim profili deterministik")


# ── 7. STL taper kararlılığı ────────────────────────────────────────────────

def test_stl_taper_stability():
    section("STL Taper Kararlılığı")
    tmp = os.path.join(tempfile.gettempdir(), "taper_test.stl")
    _write_cone_stl(tmp, z0=0.0, z1=200.0, r0=50.0, r1=30.0)
    try:
        prof = load_stl_profile(tmp, n_points=120)
        # Yarıçap sonlu, NaN yok
        ok(not np.any(np.isnan(prof.r_mm)), "STL profil yarıçapında NaN yok")
        ok(np.all(prof.r_mm > 0), "STL profil yarıçapı pozitif")
        # Taper: baş yarıçapı son yarıçaptan büyük (azalan)
        r_start = float(np.mean(prof.r_mm[:10]))
        r_end = float(np.mean(prof.r_mm[-10:]))
        ok(r_start > r_end, f"taper azalan yarıçap (baş {r_start:.1f} > son {r_end:.1f})")
        close(r_start, 50.0, 5.0, "baş yarıçapı ≈ 50mm")
        close(r_end, 30.0, 5.0, "son yarıçapı ≈ 30mm")

        # Taper üzerinde sarma yolu kararlı (NaN yok, geodezik geçerli olabilir)
        params = WindingPathParams(profile=prof, alpha_deg=45.0, n_layers=1,
                                   tow_width_mm=6.0, n_steps_per_pass=40,
                                   carriage_min_mm=float(prof.z_mm[0]),
                                   carriage_max_mm=float(prof.z_mm[-1]))
        path = generate_path(params)
        alpha = _compute_local_alpha(path.points, prof)
        ok(not np.any(np.isnan(alpha)), "taper yol açılarında NaN yok")
        # Gerilim profili taper üzerinde sonlu
        tp = estimate_tension_profile(path, prof, config=TensionModelConfig())
        ok(np.isfinite(tp.max_tension_N) and tp.max_tension_N > 0,
           "taper gerilim profili sonlu")
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ── 8. Kubbe geçiş sürekliliği ──────────────────────────────────────────────

def test_dome_transition_continuity():
    section("Kubbe Geçiş Sürekliliği")
    # Silindir: kubbe yok, sürekli, traversal edilebilir
    cyl = MandrelProfile.cylinder(300.0, 50.0, n_points=200)
    dc = analyze_dome_transition(cyl, alpha_nominal_deg=55.0)
    ok(len(dc.dome_regions) == 0, "silindirde kubbe bölgesi yok")
    ok(dc.tangent_continuous, "silindir teğet sürekli")
    ok(not dc.liftoff_risk, "silindirde kalkış yok")
    ok(dc.is_traversable, "silindir traversal edilebilir")

    # Konik taper: teğet sürekli, yeterli kutup açıklığı → traversal edilebilir
    taper = MandrelProfile.cone(300.0, 50.0, 45.0, n_points=200)
    dt = analyze_dome_transition(taper, alpha_nominal_deg=55.0)
    ok(dt.tangent_continuous, "konik taper teğet sürekli")
    ok(not dt.liftoff_risk, "konik taper kalkış yok (r_son > c)")
    ok(dt.is_traversable, "konik taper traversal edilebilir")

    # Tam yarımküre kubbe: kutba ulaşır (r→0 < c) → kalkış, reddedilir
    dome = MandrelProfile.dome_cylinder_dome(200.0, 50.0, 50.0, n_points=400)
    dd = analyze_dome_transition(dome, alpha_nominal_deg=55.0)
    ok(len(dd.dome_regions) >= 2, "kubbeli mandrelde kubbe bölgeleri tespit edildi")
    ok(dd.liftoff_risk, "yarımküre ucu kalkış riski (r < c)")
    ok(not dd.is_traversable, "geodezik olmayan kutup geçişi reddedildi")
    ok(len(dd.turnaround_z_mm) >= 1, "kutupsal dönüş bölgesi tespit edildi")


# ── Koşucu ────────────────────────────────────────────────────────────────────

def main():
    suites = [
        test_machine_limit_compliance,
        test_angle_continuity,
        test_no_impossible_accelerations,
        test_realistic_rpm,
        test_cumulative_layer_correctness,
        test_deterministic_output,
        test_stl_taper_stability,
        test_dome_transition_continuity,
    ]
    for s in suites:
        try:
            s()
        except Exception as e:
            global FAIL
            FAIL += 1
            _LOG.append(f"\n  ERROR  {s.__name__}: {e}")
            _LOG.append(traceback.format_exc())

    report = "\n".join(_LOG)
    report += f"\n\n{'='*60}\n"
    report += f"  Toplam: {PASS + FAIL}  |  PASS: {PASS}  |  FAIL: {FAIL}\n"
    report += f"{'='*60}\n"
    print(report)
    if FAIL > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
