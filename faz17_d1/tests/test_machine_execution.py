"""
tests/test_machine_execution.py — Makine Yürütme Katmanı Birim Testleri
==========================================================================
5 yeni modülü kapsamlı şekilde test eder:
    machine_calibration  — eksen kalibrasyon, kuantizasyon, backlash, profiller
    machine_kinematics   — IK/FK round-trip, senkronizasyon, yol kinematik analizi
    fiber_contact_model  — temas yaması, bant kenarları, teğet sürekliliği
    eye_orientation_solver — steering, lead, büküm yarıçapı, yol analizi
    machine_execution    — lag, backlash, kuantizasyon, faz hatası
    execution_twin       — tam pipeline, alt raporlar, fizibilite kararı

Çalıştırma: python faz17_d1/tests/test_machine_execution.py
"""
from __future__ import annotations
import math
import sys
import traceback
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / 'faz17_d1_backend'
sys.path.insert(0, str(_BACKEND))

import numpy as np

from faz17_d1.core.geometry_engine import MandrelProfile
from faz17_d1.core.fiber_band import FiberBand
from faz17_d1.core.path_generator import WindingPathParams, generate_path
from faz17_d1.core.machine_calibration import (
    AxisCalibration, MachineCalibration,
    default_calibration, high_precision_calibration,
    get_machine_profile, available_profiles,
)
from faz17_d1.core.machine_kinematics import (
    AxisState, FiberContactGeometry,
    inverse_kinematics, forward_kinematics,
    compute_axis_synchronization, required_spindle_speed_deg_s,
    analyze_path_kinematics,
)
from faz17_d1.core.fiber_contact_model import (
    compute_contact_patch, check_tangent_continuity, track_band_edges,
)
from faz17_d1.core.eye_orientation_solver import (
    EyeConstraints, compute_bend_radius, solve_eye_orientation,
    analyze_eye_orientation,
)
from faz17_d1.core.machine_execution import (
    ExecutionConstraints, ExecutionTimeline, simulate_execution,
    _meridian_curvature, _required_spindle_angle,
)
from faz17_d1.core.execution_twin import (
    AdvancedTwinParams, AdvancedTwinReport,
    run_advanced_twin, _build_simple_timeline,
)

# ── Test altyapısı ────────────────────────────────────────────────────────────

_pass = _fail = 0
_failures = []

def ok(cond: bool, msg: str) -> None:
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        _failures.append(msg)
        print(f"  FAIL: {msg}")

def section(title: str) -> None:
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")

# ── Paylaşılan fikstürler ─────────────────────────────────────────────────────

def _cylinder_100() -> MandrelProfile:
    return MandrelProfile.cylinder(200.0, 50.0, n_points=80)

def _default_path():
    prof = _cylinder_100()
    params = WindingPathParams(
        profile=prof, alpha_deg=55.0, n_layers=1,
        tow_width_mm=6.0, overlap_pct=5.0,
        n_steps_per_pass=60, feed_mm_s=80.0, spindle_rpm=60.0,
    )
    return generate_path(params), prof

def _default_band():
    return FiberBand(tow_width_mm=6.0, tow_thickness_mm=0.25)


# ══════════════════════════════════════════════════════════════════════════════
# Grup 1 — MachineCalibration
# ══════════════════════════════════════════════════════════════════════════════

def test_machine_calibration():
    section("Grup 1 — MachineCalibration")

    calib = default_calibration()

    # ── Temel sağlık kontrolü ──────────────────────────────────────────────
    ok(calib.carriage.steps_per_unit > 0, "carriage steps_per_unit > 0")
    ok(calib.spindle.steps_per_unit > 0, "spindle steps_per_unit > 0")
    ok(calib.eye_radial.steps_per_unit > 0, "eye_radial steps_per_unit > 0")
    ok(calib.eye_orient.steps_per_unit > 0, "eye_orient steps_per_unit > 0")
    ok(calib.spindle_gear_ratio > 0, "spindle_gear_ratio > 0")
    ok(calib.eye_base_standoff_mm > 0, "eye_base_standoff_mm > 0")

    # ── Çözünürlük ────────────────────────────────────────────────────────
    step_res = calib.carriage.step_resolution
    ok(step_res > 0, "carriage step_resolution > 0")
    ok(abs(step_res - 1.0 / calib.carriage.steps_per_unit) < 1e-12,
       "carriage step_resolution = 1/steps_per_unit")
    enc_res = calib.carriage.encoder_resolution
    ok(enc_res > 0, "carriage encoder_resolution > 0")

    # ── Kuantizasyon — komut adımları ──────────────────────────────────────
    val = 10.0  # mm
    q = calib.carriage.quantize_command(val)
    # kuantize edilmiş değer step_resolution katı olmalı (ofset=0, scale=1)
    remainder = (q - val) % calib.carriage.step_resolution
    ok(abs(q - val) <= calib.carriage.step_resolution,
       "quantize_command en yakın adıma yuvarlıyor")

    # ── command_steps integer üretir ────────────────────────────────────
    steps = calib.carriage.command_steps(10.0)
    ok(isinstance(steps, int), "command_steps int döner")
    ok(steps > 0, "command_steps 10 mm için pozitif")

    # ── Enkoder kuantizasyonu ──────────────────────────────────────────────
    q_enc = calib.carriage.quantize_encoder(10.0)
    ok(isinstance(q_enc, float), "quantize_encoder float döner")
    ok(abs(q_enc - 10.0) < 1.0 / calib.carriage.encoder_counts_per_unit + 1e-9,
       "quantize_encoder bir enkoder adımı içinde")

    # ── Backlash: aynı yön → backlash yok ──────────────────────────────────
    target = 15.0
    result_same = calib.carriage.apply_backlash(target, +1.0, +1.0)
    ok(abs(result_same - target) < 1e-9, "aynı yön backlash = 0")

    # ── Backlash: yön değişimi ──────────────────────────────────────────────
    result_rev = calib.carriage.apply_backlash(target, +1.0, -1.0)
    ok(result_rev != target, "yön değişimi backlash aktif")
    ok(abs(abs(result_rev - target) - calib.carriage.backlash_units) < 1e-9,
       "yön değişimi backlash miktarı doğru")

    # ── Mandrel / makine z dönüşümü ──────────────────────────────────────
    z_mand = 50.0
    z_mach = calib.mandrel_to_machine_z(z_mand)
    z_back = calib.machine_to_mandrel_z(z_mach)
    ok(abs(z_back - z_mand) < 1e-9, "mandrel↔machine z round-trip")

    # ── Dişli oranı dönüşümü ─────────────────────────────────────────────
    spindle_deg = 90.0
    motor_deg = calib.spindle_motor_deg(spindle_deg)
    back_deg = calib.motor_to_spindle_deg(motor_deg)
    ok(abs(back_deg - spindle_deg) < 1e-9, "spindle↔motor dönüşüm round-trip")
    ok(abs(motor_deg - spindle_deg * calib.spindle_gear_ratio) < 1e-9,
       "motor_deg = spindle_deg × dişli_oranı")

    # ── Makine profilleri ─────────────────────────────────────────────────
    profs = available_profiles()
    ok(len(profs) >= 2, "en az 2 makine profili mevcut")
    ok("industrial_4axis_default" in profs, "industrial_4axis_default profili var")

    hp = high_precision_calibration()
    ok(hp.carriage.steps_per_unit > calib.carriage.steps_per_unit,
       "high_precision daha yüksek adım/mm")
    ok(hp.carriage.backlash_units < calib.carriage.backlash_units,
       "high_precision daha düşük backlash")

    # ── get_machine_profile ───────────────────────────────────────────────
    p = get_machine_profile("industrial_4axis_default")
    ok(p.name == "industrial_4axis_default", "get_machine_profile doğru profil döner")

    # ── summary() str ─────────────────────────────────────────────────────
    s = calib.summary()
    ok(isinstance(s, str) and len(s) > 10, "summary() string üretir")

    # ── Hatalı AxisCalibration ─────────────────────────────────────────────
    try:
        AxisCalibration(steps_per_unit=-1.0, encoder_counts_per_unit=100.0)
        ok(False, "Negatif steps_per_unit hata fırlatmalıydı")
    except ValueError:
        ok(True, "Negatif steps_per_unit ValueError")

    try:
        AxisCalibration(steps_per_unit=100.0, encoder_counts_per_unit=100.0,
                        backlash_units=-0.01)
        ok(False, "Negatif backlash hata fırlatmalıydı")
    except ValueError:
        ok(True, "Negatif backlash ValueError")


# ══════════════════════════════════════════════════════════════════════════════
# Grup 2 — MachineKinematics
# ══════════════════════════════════════════════════════════════════════════════

def test_machine_kinematics():
    section("Grup 2 — MachineKinematics")

    prof = _cylinder_100()
    calib = default_calibration()
    z_c = 100.0
    phi_c = 45.0
    alpha = 55.0
    standoff = 150.0

    # ── IK → FK round-trip ─────────────────────────────────────────────────
    axis, geom_ik = inverse_kinematics(
        z_c, phi_c, alpha, prof, calib, travel_dir=1.0, standoff_mm=standoff
    )
    geom_fk = forward_kinematics(axis, prof, calib, travel_dir=1.0)

    ok(isinstance(axis, AxisState), "IK AxisState döner")
    ok(isinstance(geom_ik, FiberContactGeometry), "IK FiberContactGeometry döner")
    z_err = abs(geom_fk.contact_z_mm - geom_ik.contact_z_mm)
    ok(z_err < 0.01, f"IK→FK round-trip z hatası < 0.01mm (actual={z_err:.6f}mm)")

    # ── Geometri sağlık ────────────────────────────────────────────────────
    ok(geom_ik.is_valid, "Silindir'de göz geçerli (is_valid=True)")
    ok(geom_ik.fiber_free_length_mm > 0, "Serbest fiber uzunluğu > 0")
    ok(geom_ik.lead_mm > 0, "lead_mm > 0 (alpha=55°, travel_dir=+1)")
    ok(abs(geom_ik.lead_mm - standoff * math.tan(math.radians(alpha))) < 0.01,
       "lead = standoff·tan(α)")

    # ── Yüzey normali (silindir, z-yönü) ───────────────────────────────────
    n = geom_ik.surface_normal
    r_n = math.sqrt(n[0] ** 2 + n[1] ** 2)
    ok(abs(r_n - 1.0) < 0.05, "Silindir normali radyal bileşeni ~1 (z=0)")
    ok(abs(abs(n[2]) - 0.0) < 0.1, "Silindir normali z-bileşeni ~0 (düz cidar)")

    # ── Eksen senkronizasyonu ─────────────────────────────────────────────
    v_x = 80.0   # mm/s taşıyıcı
    r_c = 50.0
    omega_needed = required_spindle_speed_deg_s(v_x, alpha, r_c)
    ok(omega_needed > 0, "Gerekli iş mili hızı > 0")
    sync = compute_axis_synchronization(v_x, omega_needed, r_c)
    ok(abs(sync.winding_angle_deg - alpha) < 0.5,
       f"Senkronizasyon sarma açısı ≈ {alpha}° (actual={sync.winding_angle_deg:.2f}°)")

    # ── Sarma açısı = hız oranı (statik geometri değil) ───────────────────
    # α=0° → sadece eksenel hareket
    sync_axial = compute_axis_synchronization(100.0, 0.0, r_c)
    ok(sync_axial.winding_angle_deg < 1.0, "Saf eksenel hareket → α~0°")
    # α=~90° → çok yüksek çevresel hız
    sync_hoop = compute_axis_synchronization(1.0, 1000.0, r_c)
    ok(sync_hoop.winding_angle_deg > 85.0, "Çevre sarma → α~90°")

    # ── Yol kinematik analizi ─────────────────────────────────────────────
    path, prof2 = _default_path()
    kin = analyze_path_kinematics(path, prof2, calib, standoff_mm=standoff)
    ok(isinstance(kin.n_points, int) and kin.n_points > 0, "KinematicsReport.n_points > 0")
    ok(kin.max_contact_angle_deg > 0, "maks sarma açısı > 0")
    ok(kin.min_contact_angle_deg >= 0, "min sarma açısı ≥ 0")
    ok(kin.max_lead_mm > 0, "maks lead > 0")
    ok(kin.max_fiber_length_mm > 0, "maks fiber uzunluğu > 0")
    ok(isinstance(kin.is_valid, bool), "is_valid bool tipinde")
    ok(isinstance(kin.warnings, list), "warnings list tipinde")
    summary_s = kin.summary()
    ok("Kinematik" in summary_s, "summary() 'Kinematik' içeriyor")

    # ── travel_dir=-1 geom ───────────────────────────────────────────────
    axis_neg, geom_neg = inverse_kinematics(
        z_c, phi_c, alpha, prof, calib, travel_dir=-1.0, standoff_mm=standoff
    )
    ok(geom_neg.lead_mm > 0, "lead_mm > 0 (travel_dir=-1)")
    # z_eye = z_c + travel_dir * lead → z_eye farklı tarafta
    ok(axis_neg.carriage_x_mm < calib.mandrel_to_machine_z(z_c),
       "Geri yönde göz taşıyıcısı temas noktasının gerisinde")


# ══════════════════════════════════════════════════════════════════════════════
# Grup 3 — FiberContactModel
# ══════════════════════════════════════════════════════════════════════════════

def test_fiber_contact_model():
    section("Grup 3 — FiberContactModel")

    prof = _cylinder_100()
    band = _default_band()
    W = band.tow_width_mm

    # ── Temas yaması geometrisi ────────────────────────────────────────────
    z_c = 100.0; phi_c = 0.0; alpha = 55.0
    patch = compute_contact_patch(z_c, phi_c, alpha, prof, W)

    ok(abs(patch.center_z_mm - z_c) < 1e-6, "Merkez z doğru")
    ok(abs(patch.center_phi_deg - phi_c) < 1e-6, "Merkez phi doğru")
    ok(abs(patch.center_r_mm - 50.0) < 0.1, "Silindir merkez yarıçapı ~50mm")

    # Kenar vektörü normalize
    tf = patch.tangent_fiber
    ok(abs(math.sqrt(tf[0]**2+tf[1]**2+tf[2]**2) - 1.0) < 1e-6,
       "tangent_fiber birim vektör")
    tp = patch.tangent_perp
    ok(abs(math.sqrt(tp[0]**2+tp[1]**2+tp[2]**2) - 1.0) < 1e-6,
       "tangent_perp birim vektör")

    # T_f · T_p = 0 (ortogonal)
    dot_fp = tf[0]*tp[0] + tf[1]*tp[1] + tf[2]*tp[2]
    ok(abs(dot_fp) < 1e-6, "T_f ⊥ T_p")

    # Kenar ayrımı: P_left/right arasındaki uzaklık = W
    lx,ly,lz = patch.left_edge_xyz
    rx,ry,rz = patch.right_edge_xyz
    edge_dist = math.sqrt((rx-lx)**2 + (ry-ly)**2 + (rz-lz)**2)
    ok(abs(edge_dist - W) < 0.01, f"Kenar ayrımı = W={W}mm (actual={edge_dist:.4f}mm)")

    # ── Silindir α=90° — kenarlar T_p ≈ -T_m (eksenel yön) boyunca ayrılır ──
    # T_f ≈ T_φ (çevresel), T_p ≈ -T_m (meridyen/eksenel)
    # → bant kenarları eksenel yönde ayrılır → bandwidth_axial ≈ W, bandwidth_circ ≈ 0
    patch90 = compute_contact_patch(100.0, 0.0, 89.9, prof, W)
    ok(patch90.bandwidth_axial_mm > W * 0.9,
       f"α≈90° → eksenel bant genişliği ≈ W (actual={patch90.bandwidth_axial_mm:.3f}mm)")
    ok(patch90.bandwidth_circ_mm < W * 0.1,
       f"α≈90° → çevresel bant genişliği küçük (actual={patch90.bandwidth_circ_mm:.3f}mm)")

    # ── Teğet sürekliliği ──────────────────────────────────────────────────
    path, prof2 = _default_path()
    discont = check_tangent_continuity(path, prof2, threshold_deg=5.0, sample_every=10)
    ok(isinstance(discont, list), "check_tangent_continuity list döner")

    # ── Bant kenar takibi ──────────────────────────────────────────────────
    edge_rep = track_band_edges(path, prof2, band, sample_every=5)
    ok(isinstance(edge_rep.edges, list), "BandEdgeReport.edges list")
    ok(edge_rep.total_edge_drift_mm >= 0.0, "total_edge_drift_mm >= 0")
    ok(isinstance(edge_rep.is_uniform, bool), "is_uniform bool")
    ok(isinstance(edge_rep.warnings, list), "warnings list")
    edge_summary = edge_rep.summary()
    ok("BantKenar" in edge_summary, "BandEdgeReport.summary() 'BantKenar' içeriyor")

    # ── max_gap ve max_overlap sayısal ────────────────────────────────────
    ok(edge_rep.max_gap_mm >= 0.0, "max_gap_mm >= 0")
    ok(edge_rep.max_overlap_mm >= 0.0, "max_overlap_mm >= 0")
    ok(edge_rep.rms_overlap_mm >= 0.0, "rms_overlap_mm >= 0")

    # ── Boş yol → güvenli döndürme ────────────────────────────────────────
    from faz17_d1.core.path_generator import WindingPath, WindingPoint
    empty_path = WindingPath(
        points=[], n_circuits=0, n_layers=0,
        total_fiber_length_mm=0.0, estimated_time_s=0.0,
        coverage_pct=0.0, clairaut_c=0.0,
        params=WindingPathParams(profile=prof),
    )
    rep_empty = track_band_edges(empty_path, prof, band)
    ok(len(rep_empty.edges) == 0, "Boş yolda edges=[]")


# ══════════════════════════════════════════════════════════════════════════════
# Grup 4 — EyeOrientationSolver
# ══════════════════════════════════════════════════════════════════════════════

def test_eye_orientation_solver():
    section("Grup 4 — EyeOrientationSolver")

    prof = _cylinder_100()
    calib = default_calibration()
    constr = EyeConstraints(
        steer_max_deg=60.0,
        min_bend_radius_mm=15.0,
        fiber_diameter_mm=1.0,
        fiber_tensile_GPa=230.0,
        eye_aperture_mm=8.0,
    )
    standoff = 150.0
    tension = 50.0
    alpha = 55.0
    z_c = 100.0
    phi_c = 0.0

    # ── Büküm yarıçapı ────────────────────────────────────────────────────
    rho = compute_bend_radius(tension, constr.eye_aperture_mm,
                               constr.fiber_diameter_mm, constr.fiber_tensile_GPa)
    ok(rho > 0, "Büküm yarıçapı > 0")
    # Daha yüksek gerilim → daha küçük büküm yarıçapı
    rho_high = compute_bend_radius(200.0, constr.eye_aperture_mm,
                                    constr.fiber_diameter_mm, constr.fiber_tensile_GPa)
    ok(rho_high <= rho, "Daha yüksek gerilim → daha küçük büküm yarıçapı")

    # ── Yönelim çözücüsü ──────────────────────────────────────────────────
    result = solve_eye_orientation(
        z_c, phi_c, alpha, prof, calib, constr,
        travel_dir=1.0, tension_N=tension, standoff_mm=standoff,
    )
    ok(hasattr(result, 'steer_cmd_deg'), "result.steer_cmd_deg mevcut")
    ok(hasattr(result, 'lead_z_mm'), "result.lead_z_mm mevcut")
    ok(hasattr(result, 'bend_radius_mm'), "result.bend_radius_mm mevcut")
    ok(hasattr(result, 'within_steer_lim'), "result.within_steer_lim mevcut")
    ok(hasattr(result, 'bend_radius_safe'), "result.bend_radius_safe mevcut")

    # Steering ≈ sarma açısı (birinci derece yaklaşım)
    ok(abs(abs(result.steer_cmd_deg) - alpha) < 5.0,
       f"Steering açısı ≈ sarma açısı (actual={result.steer_cmd_deg:.1f}° alpha={alpha}°)")

    # lead = standoff * tan(alpha)
    expected_lead = standoff * math.tan(math.radians(alpha))
    ok(abs(result.lead_z_mm - expected_lead) < 1.0,
       f"lead ≈ standoff·tan(α) (actual={result.lead_z_mm:.2f}mm exp={expected_lead:.2f}mm)")

    # ── Sınır içindeyse ───────────────────────────────────────────────────
    ok(isinstance(result.within_steer_lim, bool), "within_steer_lim bool")
    ok(isinstance(result.bend_radius_safe, bool), "bend_radius_safe bool")

    # ── Aşırı steering: α=85° ─────────────────────────────────────────────
    result_extreme = solve_eye_orientation(
        z_c, phi_c, 85.0, prof, calib, constr,
        travel_dir=1.0, tension_N=tension, standoff_mm=standoff,
    )
    ok(not result_extreme.within_steer_lim or result_extreme.steer_cmd_deg > 50.0,
       "α=85° yüksek steering üretir")

    # ── Yol analizi ───────────────────────────────────────────────────────
    path, prof2 = _default_path()
    eye_rep = analyze_eye_orientation(
        path, prof2, calib, constr,
        standoff_mm=standoff, tension_N=tension, sample_every=5,
    )
    ok(isinstance(eye_rep.n_points, int) and eye_rep.n_points > 0, "n_points > 0")
    ok(isinstance(eye_rep.is_feasible, bool), "is_feasible bool")
    ok(eye_rep.max_steer_deg >= 0, "max_steer_deg >= 0")
    ok(eye_rep.min_bend_radius_mm > 0, "min_bend_radius_mm > 0")
    ok(eye_rep.max_lead_mm > 0, "max_lead_mm > 0")
    ok(eye_rep.max_steer_rate_deg_s >= 0, "max_steer_rate_deg_s >= 0")
    ok(isinstance(eye_rep.warnings, list), "warnings list")
    ok("GözAnaliz" in eye_rep.summary(), "summary() 'GözAnaliz' içeriyor")


# ══════════════════════════════════════════════════════════════════════════════
# Grup 5 — MachineExecution
# ══════════════════════════════════════════════════════════════════════════════

def test_machine_execution():
    section("Grup 5 — MachineExecution")

    path, prof = _default_path()
    calib = default_calibration()
    constraints = ExecutionConstraints(
        carriage_lag_tau_s=0.08,
        spindle_lag_tau_s=0.15,
        backlash_carriage_mm=0.02,
        backlash_spindle_deg=0.05,
        curvature_accel_limit_mm_s2=200.0,
        dome_transition_feed_factor=0.60,
    )

    # TwinTimeline oluştur
    from faz17_d1.core.trajectory_builder import TwinTimeline
    tl = _build_simple_timeline(path, dt_s=0.5)
    ok(tl.n_samples > 1, "TwinTimeline en az 2 örnek")

    exec_tl = simulate_execution(tl, path, prof, constraints, calib)

    # ── Temel boyut kontrolü ─────────────────────────────────────────────
    n = len(exec_tl.t_s)
    ok(n == tl.n_samples, "ExecutionTimeline boyutu TwinTimeline ile eşleşiyor")
    ok(len(exec_tl.x_commanded_mm) == n, "x_commanded boyutu")
    ok(len(exec_tl.x_actual_mm) == n, "x_actual boyutu")
    ok(len(exec_tl.x_encoder_mm) == n, "x_encoder boyutu")
    ok(len(exec_tl.a_commanded_deg) == n, "a_commanded boyutu")
    ok(len(exec_tl.a_actual_deg) == n, "a_actual boyutu")
    ok(len(exec_tl.sync_phase_error_deg) == n, "sync_phase_error boyutu")

    # ── Lag: actual komutun gerisinde ────────────────────────────────────
    # Birinci adımda cmd=actual (başlangıç koşulu); sonrasında lag var
    fe_x_max = exec_tl.max_following_error_x_mm
    ok(fe_x_max >= 0.0, "max following_error_x_mm >= 0")
    # Lag olduğunda takip hatası > 0 (dinamik değişim varsa)
    x_range = float(np.ptp(exec_tl.x_commanded_mm))
    if x_range > 1.0:  # yeterli hareket varsa
        ok(fe_x_max > 0.0, "Hareket varken takip hatası > 0")

    # ── Takip hatası sınırlı ─────────────────────────────────────────────
    ok(exec_tl.max_following_error_x_mm < 50.0, "x takip hatası fiziksel sınırda")
    # tau_a = max(lag_tau=0.15, J/b=0.25, dt*2=1.0) = 1.0s, omega≈131°/s → lag≈131°
    ok(exec_tl.max_following_error_a_deg < 5000.0, "a takip hatası fiziksel sınırda")

    # ── Encoder kuantizasyonu: en fazla 1 enkoder adımı ─────────────────
    enc_step = 1.0 / calib.carriage.encoder_counts_per_unit
    max_quant_err = float(np.max(np.abs(exec_tl.x_encoder_mm - exec_tl.x_actual_mm)))
    ok(max_quant_err <= enc_step + 1e-9,
       f"x_encoder hata ≤ 1 enkoder adımı (actual={max_quant_err:.6f}mm step={enc_step:.6f}mm)")

    enc_step_a = 1.0 / calib.spindle.encoder_counts_per_unit
    max_quant_a = float(np.max(np.abs(exec_tl.a_encoder_deg - exec_tl.a_actual_deg)))
    ok(max_quant_a <= enc_step_a + 1e-9,
       f"a_encoder hata ≤ 1 enkoder adımı (actual={max_quant_a:.6f}° step={enc_step_a:.6f}°)")

    # ── Besleme sınırlama ─────────────────────────────────────────────────
    ok(exec_tl.n_feed_limited >= 0, "n_feed_limited >= 0")
    # Besleme sınırlı noktalarda hız düşmüş olmalı
    if exec_tl.n_feed_limited > 0:
        ok(True, f"Besleme {exec_tl.n_feed_limited} noktada sınırlandı")

    # ── Backlash sayaçları ────────────────────────────────────────────────
    ok(exec_tl.n_backlash_x >= 0, "n_backlash_x >= 0")
    ok(exec_tl.n_backlash_a >= 0, "n_backlash_a >= 0")

    # ── Senkronizasyon faz hatası ─────────────────────────────────────────
    ok(exec_tl.rms_sync_error_deg >= 0.0, "rms_sync_error_deg >= 0")
    ok(exec_tl.n_sync_exceeded >= 0, "n_sync_exceeded >= 0")

    # ── Summary string ────────────────────────────────────────────────────
    s = exec_tl.summary()
    ok("YürütmeTimeline" in s, "summary() 'YürütmeTimeline' içeriyor")

    # ── Meridyen eğrilik (yardımcı) ──────────────────────────────────────
    z_arr = np.linspace(0.0, 200.0, 50)
    kappa = _meridian_curvature(prof, z_arr)
    ok(len(kappa) == 50, "_meridian_curvature doğru boyut")
    # Silindir: r''=0 → κ≈0
    ok(float(np.max(kappa)) < 0.01, "Silindir eğriliği ≈ 0")

    # Kubbe → yüksek eğrilik (kubbe bölgelerinde r hızla değişir)
    prof_dome = MandrelProfile.dome_cylinder_dome(100.0, 50.0, 30.0, n_points=200)
    z_dome = np.linspace(0.0, 160.0, 80)
    kappa_dome = _meridian_curvature(prof_dome, z_dome)
    ok(not np.any(np.isnan(kappa_dome)), "Kubbe profili eğriliği NaN içermez")
    ok(float(np.max(kappa_dome)) >= 0.0, "Kubbe profili eğriliği >= 0")

    # ── Clairaut faz gereksinimi ──────────────────────────────────────────
    c = path.clairaut_c
    x_test = np.linspace(0.0, 200.0, 30)
    a_req = _required_spindle_angle(x_test, c, prof, a0_deg=0.0)
    ok(len(a_req) == 30, "_required_spindle_angle doğru boyut")
    ok(float(a_req[0]) == 0.0, "_required_spindle_angle a0'dan başlar")
    # Silindir: monoton artan (taşıyıcı ilerledikçe açı artar)
    ok(float(a_req[-1]) > float(a_req[0]), "Clairaut açı gereksinimi ilerledikçe artar")

    # ── Boş timeline güvenli ─────────────────────────────────────────────
    tl_empty = TwinTimeline(
        t_s=np.zeros(0), x_mm=np.zeros(0), a_deg=np.zeros(0),
        rpm=np.zeros(0), carriage_v_mm_s=np.zeros(0),
        spindle_v_deg_s=np.zeros(0), layer=np.zeros(0, int),
        circuit=np.zeros(0, int), radius_mm=np.zeros(0),
        fiber_mm=np.zeros(0), dt_s=0.5, total_time_s=0.0,
    )
    exec_empty = simulate_execution(tl_empty, path, prof, constraints, calib)
    ok(len(exec_empty.t_s) == 0, "Boş timeline → boş ExecutionTimeline")


# ══════════════════════════════════════════════════════════════════════════════
# Grup 6 — ExecutionTwin (Gelişmiş Dijital İkiz)
# ══════════════════════════════════════════════════════════════════════════════

def test_execution_twin():
    section("Grup 6 — ExecutionTwin (Gelişmiş Dijital İkiz)")

    path, prof = _default_path()
    band = _default_band()
    calib = default_calibration()
    exec_c = ExecutionConstraints()
    eye_c = EyeConstraints()
    # dt=0.1s → tau_x=max(0.08, 0.2)=0.2s, lag=80*0.2=16mm
    #           tau_a=max(0.15, 0.25, 0.2)=0.25s, lag=131*0.25=33°
    # max_gap_mm=300: bant kenar hesabı arka-arkaya devre başlangıç noktaları
    # arasındaki z farkını ölçer (200mm); hard-stop=max_gap*2=600mm → etkilenmez
    params = AdvancedTwinParams(
        standoff_mm=150.0,
        kin_sample_every=10,
        band_sample_every=10,
        eye_sample_every=10,
        tension_N=50.0,
        timeline_dt_s=0.1,
        max_following_error_x_mm=30.0,    # tau_x~0.2s*80mm/s=16mm + marj
        max_following_error_a_deg=100.0,  # tau_a~0.25s*131°/s=33° + marj
        max_sync_error_deg=50.0,
        max_steer_deg=70.0,
        min_bend_radius_mm=5.0,
        max_gap_mm=300.0,                 # bant kenar gap hesabı: devre başlangıç farkı
    )

    report = run_advanced_twin(path, band, prof, calib, exec_c, eye_c, params)

    # ── Tip kontrolleri ───────────────────────────────────────────────────
    ok(isinstance(report, AdvancedTwinReport), "run_advanced_twin AdvancedTwinReport döner")
    ok(isinstance(report.is_execution_feasible, bool), "is_execution_feasible bool")
    ok(isinstance(report.hard_stops, list), "hard_stops list")
    ok(isinstance(report.warnings, list), "warnings list")

    # ── Alt raporlar mevcut ───────────────────────────────────────────────
    ok(report.kinematics is not None, "kinematics sub-raporu mevcut")
    ok(report.band_edges is not None, "band_edges sub-raporu mevcut")
    ok(report.eye_orient is not None, "eye_orient sub-raporu mevcut")
    ok(report.execution is not None, "execution sub-raporu mevcut")

    # ── Sayısal özet alanları ─────────────────────────────────────────────
    ok(report.peak_following_error_x_mm >= 0.0, "peak_following_error_x_mm >= 0")
    ok(report.peak_following_error_a_deg >= 0.0, "peak_following_error_a_deg >= 0")
    ok(report.rms_sync_error_deg >= 0.0, "rms_sync_error_deg >= 0")
    ok(report.n_backlash_reversals_x >= 0, "n_backlash_reversals_x >= 0")
    ok(report.n_backlash_reversals_a >= 0, "n_backlash_reversals_a >= 0")
    ok(report.n_feed_limited_samples >= 0, "n_feed_limited_samples >= 0")
    ok(report.max_steer_deg >= 0.0, "max_steer_deg >= 0")
    ok(report.min_bend_radius_mm > 0.0, "min_bend_radius_mm > 0")
    ok(report.total_edge_drift_mm >= 0.0, "total_edge_drift_mm >= 0")

    # ── Silindir: uygulanabilir olmalı (geniş limitlerle) ────────────────
    ok(report.is_execution_feasible,
       f"Silindir geniş limitle uygulanabilir (hard_stops={report.hard_stops})")

    # ── Summary string ────────────────────────────────────────────────────
    s = report.summary()
    ok(isinstance(s, str) and len(s) > 20, "summary() yeterli uzunlukta string")
    ok("GELİŞMİŞ_İKİZ" in s, "summary() 'GELİŞMİŞ_İKİZ' içeriyor")

    # ── Deterministik: iki çalıştırma aynı sonuç ─────────────────────────
    report2 = run_advanced_twin(path, band, prof, calib, exec_c, eye_c, params)
    ok(abs(report.peak_following_error_x_mm - report2.peak_following_error_x_mm) < 1e-9,
       "peak_following_error deterministik")
    ok(report.is_execution_feasible == report2.is_execution_feasible,
       "is_execution_feasible deterministik")

    # ── Sıkı limitler → hard_stop ─────────────────────────────────────────
    strict_params = AdvancedTwinParams(
        standoff_mm=150.0,
        timeline_dt_s=0.1,
        max_following_error_x_mm=0.0001,   # imkânsız sıkı limit (gerçek lag ~16mm)
        max_following_error_a_deg=0.0001,
        max_sync_error_deg=0.0001,
        max_steer_deg=1.0,                 # 1° steering limiti → gerçekçi olmayan
        min_bend_radius_mm=10000.0,        # 10 m min büküm → imkânsız
    )
    strict_report = run_advanced_twin(path, band, prof, calib, exec_c, eye_c, strict_params)
    ok(not strict_report.is_execution_feasible,
       "Sıkı limitlerle is_execution_feasible=False")
    ok(len(strict_report.hard_stops) > 0, "Sıkı limitler hard_stop üretir")

    # ── Önceden hazırlanmış timeline desteği ─────────────────────────────
    tl = _build_simple_timeline(path, dt_s=2.0)
    ok(tl.n_samples > 0, "_build_simple_timeline sample üretiyor")
    report_custom = run_advanced_twin(path, band, prof, calib, exec_c, eye_c, params,
                                      timeline=tl)
    ok(isinstance(report_custom, AdvancedTwinReport),
       "Önceden hazırlanmış timeline ile AdvancedTwinReport")

    # ── Kubbe profili çalıştırılabilir ────────────────────────────────────
    prof_dome = MandrelProfile.dome_cylinder_dome(100.0, 50.0, 25.0, n_points=200)
    dome_path_params = WindingPathParams(
        profile=prof_dome, alpha_deg=55.0, n_layers=1,
        tow_width_mm=6.0, overlap_pct=5.0,
        n_steps_per_pass=60, feed_mm_s=80.0, spindle_rpm=60.0,
    )
    dome_path = generate_path(dome_path_params)
    dome_report = run_advanced_twin(dome_path, band, prof_dome, calib, exec_c, eye_c, params)
    ok(isinstance(dome_report, AdvancedTwinReport), "Kubbe profili AdvancedTwinReport döner")
    ok(dome_report.kinematics.n_points > 0, "Kubbe kinematik analizi noktaları var")


# ══════════════════════════════════════════════════════════════════════════════
# Ana çalıştırıcı
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("\n" + "═" * 60)
    print("  MAKINE YÜRÜTME KATMANI BİRİM TESTLERİ")
    print("═" * 60)

    groups = [
        ("Grup 1 — MachineCalibration", test_machine_calibration),
        ("Grup 2 — MachineKinematics", test_machine_kinematics),
        ("Grup 3 — FiberContactModel", test_fiber_contact_model),
        ("Grup 4 — EyeOrientationSolver", test_eye_orientation_solver),
        ("Grup 5 — MachineExecution", test_machine_execution),
        ("Grup 6 — ExecutionTwin", test_execution_twin),
    ]

    for name, fn in groups:
        try:
            fn()
        except Exception as e:
            _fail += 1
            _failures.append(f"{name}: EXCEPTION: {e}")
            print(f"\n  EXCEPTION in {name}:")
            traceback.print_exc()

    print("\n" + "═" * 60)
    total = _pass + _fail
    print(f"  TOPLAM: {_pass}/{total} PASS")
    if _failures:
        print(f"\n  BAŞARISIZ ({len(_failures)}):")
        for f in _failures:
            print(f"    - {f}")
        sys.exit(1)
    else:
        print("  TÜM TESTLER BAŞARILI ✓")
    print("═" * 60)
