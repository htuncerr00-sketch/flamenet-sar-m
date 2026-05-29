"""
tests/test_digital_twin.py — Digital Twin Simülasyon Birim Testleri
=====================================================================
Katman birikimi, fiber gerilimi, payout dinamiği, tam digital twin,
makine zarfı, fiber yatırma, proses parametreleri, üretim raporu ve
oynatma motoru için kapsamlı testler.

Çalıştırma: python faz17_d1/tests/test_digital_twin.py
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
from faz17_d1.core.process_parameters import ProcessParameters
from faz17_d1.core.fiber_tension import FiberTensionModel
from faz17_d1.core.machine_envelope import MachineEnvelope, check_path_envelope
from faz17_d1.core.layer_buildup import (
    LayerBuildup, generate_layered_paths,
)
from faz17_d1.core.fiber_deposition import simulate_deposition
from faz17_d1.core.payout_dynamics import (
    PayoutDynamicsConfig, simulate_eye_response,
    compute_carriage_lead_safe, compute_contact_point_motion,
)
from faz17_d1.core.winding_twin import simulate_winding, TwinSimulationResult
from faz17_d1.core.production_report import generate_production_report
from faz17_d1.core.simulation_playback import SimulationPlayback

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


def _cyl(alpha=55.0, n_layers=1, tw=6.0, npts=80, steps=40):
    prof = MandrelProfile.cylinder(300.0, 50.0, n_points=npts)
    params = WindingPathParams(
        profile=prof, alpha_deg=alpha, n_layers=n_layers,
        tow_width_mm=tw, overlap_pct=5.0, n_steps_per_pass=steps,
    )
    return prof, params


# ── Proses Parametreleri ──────────────────────────────────────────────────────

def test_process_parameters():
    section("Proses Parametreleri")
    p = ProcessParameters(resin_content_pct=35.0, tow_count=4, tow_tex_g_km=800.0,
                          fiber_density_g_cm3=1.80, resin_density_g_cm3=1.20,
                          cure_shrinkage_pct=3.0)

    # Ağırlık oranları
    close(p.fiber_weight_fraction, 0.65, 1e-9, "W_f = 0.65")
    close(p.resin_weight_fraction, 0.35, 1e-9, "W_r = 0.35")

    # Hacim oranı: V_f = (0.65/1.8)/((0.65/1.8)+(0.35/1.2))
    vf_term = 0.65 / 1.8
    vr_term = 0.35 / 1.2
    vf_expected = vf_term / (vf_term + vr_term)
    close(p.fiber_volume_fraction, vf_expected, 1e-9, "V_f doğru")
    ok(0.0 < p.fiber_volume_fraction < 1.0, "V_f ∈ (0,1)")

    # Kompozit yoğunluğu fiber ve reçine arasında
    ok(p.resin_density_g_cm3 < p.composite_density_g_cm3 < p.fiber_density_g_cm3,
       "ρ_r < ρ_c < ρ_f")

    # Fiber lineer yoğunluk: 4*800/1e6 = 0.0032 g/mm
    close(p.fiber_linear_density_g_mm, 4 * 800 / 1e6, 1e-12, "fiber lineer yoğunluk")

    # Toplam lineer = fiber/W_f
    close(p.total_linear_density_g_mm, (4 * 800 / 1e6) / 0.65, 1e-12,
          "toplam lineer = fiber/W_f")

    # Kütle dengesi: fiber + resin = total
    L = 100000.0  # 100 m
    close(p.fiber_mass_g(L) + p.resin_mass_g(L), p.total_mass_g(L), 1e-6,
          "fiber + reçine = toplam kütle")

    # Reçine kütle oranı kontrolü
    close(p.resin_mass_g(L) / p.total_mass_g(L), 0.35, 1e-6, "reçine kütle oranı = 35%")

    # Hacim pozitif, çekme uygulanmış
    ok(p.composite_volume_cm3(L) > 0, "kompozit hacmi > 0")
    raw_vol = p.total_mass_g(L) / p.composite_density_g_cm3
    close(p.composite_volume_cm3(L), raw_vol * 0.97, raw_vol * 0.001,
          "çekme payı uygulandı (3%)")

    # Kür kalınlık çekmesi
    close(p.cured_thickness_mm(1.0), 0.97, 1e-9, "kür kalınlık çekmesi")

    # FAW pozitif
    ok(p.band_areal_weight_g_m2(6.0) > 0, "FAW > 0")

    # Hız limiti
    ok(p.speed_limit_violation(200.0), "200mm/s proses limitini aşar")
    ok(not p.speed_limit_violation(100.0), "100mm/s limit içinde")

    # Validasyon
    try:
        ProcessParameters(resin_content_pct=100.0)
        ok(False, "resin=100% ValueError beklenir")
    except ValueError:
        ok(True, "resin=100% ValueError fırlattı")


# ── Fiber Gerilimi ────────────────────────────────────────────────────────────

def test_fiber_tension():
    section("Fiber Gerilim Modeli")
    ft = FiberTensionModel(tension_N=50.0, friction_coeff=0.3,
                           tow_cross_section_mm2=0.45, fiber_modulus_GPa=230.0)

    # Temas basıncı p = T/(R*w)
    close(ft.contact_pressure_MPa(50.0, 6.0), 50.0 / (50.0 * 6.0), 1e-9,
          "temas basıncı p=T/(R·w)")
    close(ft.contact_pressure_MPa(0.0, 6.0), 0.0, 1e-9, "r=0 → basınç 0")

    # Normal çizgi yükü
    close(ft.normal_line_load_N_mm(50.0), 50.0 / 50.0, 1e-9, "normal yük T/R")

    # Sürtünme tutunması
    close(ft.friction_hold_N_mm(50.0), 0.3 * 50.0 / 50.0, 1e-9, "sürtünme μT/R")

    # Kayma oranı açıdan: tan(α)/μ
    close(ft.slip_ratio_from_angle(45.0), math.tan(math.radians(45.0)) / 0.3, 1e-9,
          "slip_ratio(45°)")
    # tan(45)/0.3 ≈ 3.33 > 1 → kayar
    ok(not ft.is_slip_safe(45.0), "45° kayma güvensiz (μ=0.3)")
    # Düşük açı güvenli: atan(0.3) ≈ 16.7°
    ok(ft.is_slip_safe(10.0), "10° kayma güvenli")
    close(ft._max_safe_angle_deg(), math.degrees(math.atan(0.3)), 1e-9,
          "α_max = atan(μ)")

    # Geodezik eğrilik kaymadan bağımsız: k_g=0 → λ=0
    close(ft.slip_ratio(0.0, 50.0), 0.0, 1e-12, "geodezik (k_g=0) → λ=0")
    ok(ft.slip_ratio(0.01, 50.0) > 0, "k_g>0 → λ>0")

    # Sıkıştırma faktörü: yüksek basınç → düşük faktör
    cf_low_p = ft.compaction_factor(200.0, 6.0)   # büyük R → düşük basınç
    cf_high_p = ft.compaction_factor(20.0, 6.0)    # küçük R → yüksek basınç
    ok(cf_high_p < cf_low_p, "yüksek basınç → daha çok sıkıştırma (düşük faktör)")
    ok(0.5 <= cf_high_p <= 1.0 and 0.5 <= cf_low_p <= 1.0,
       "sıkıştırma faktörü ∈ [0.5,1]")

    # Minimum kararlı gerilim T_min = p_min*R*w
    close(ft.min_stable_tension_N(50.0, 6.0), 0.03 * 50.0 * 6.0, 1e-9,
          "T_min = p_min·R·w")
    ok(ft.is_tension_stable(50.0, 6.0), "T=50N kararlı (R=50,w=6)")
    # Büyük R*w → yüksek T_min
    ft_low = FiberTensionModel(tension_N=1.0, min_contact_pressure_MPa=0.03)
    ok(not ft_low.is_tension_stable(200.0, 10.0), "T=1N büyük yüzeyde kararsız")

    # Fiber uzaması ε = T/(A·E)
    E_MPa = 230.0 * 1000.0
    close(ft.fiber_strain, 50.0 / (0.45 * E_MPa), 1e-12, "fiber uzaması")
    close(ft.fiber_stress_MPa, 50.0 / 0.45, 1e-9, "fiber gerilmesi σ=T/A")
    ok(ft.fiber_strain_pct < 1.0, "fiber uzaması < %1 (makul)")


# ── Makine Zarfı ──────────────────────────────────────────────────────────────

def test_machine_envelope():
    section("Makine Çalışma Zarfı")
    env = MachineEnvelope(x_hard_min_mm=-10, x_hard_max_mm=400,
                          x_soft_min_mm=-5, x_soft_max_mm=395)

    # Soft travel
    close(env.soft_travel_mm, 400.0, 1e-9, "yumuşak seyahat = 400mm")
    close(env.max_spindle_deg_s, 300.0 * 6.0, 1e-9, "max iş mili °/s")

    # Konum kontrolü
    ok(len(env.check_carriage_position(200.0)) == 0, "X=200 limit içinde")
    ok(any("SERT" in m for m in env.check_carriage_position(450.0)),
       "X=450 sert limit ihlali")
    ok(any("Yumuşak" in m for m in env.check_carriage_position(398.0)),
       "X=398 yumuşak limit ihlali")

    # Göz çalışma alanı
    ok(len(env.check_eye_position(200.0)) == 0, "göz r=200 OK")
    ok(len(env.check_eye_position(40.0)) > 0, "göz r=40 < min ihlali")
    ok(len(env.check_eye_position(600.0)) > 0, "göz r=600 > max ihlali")

    # Hız / ivme
    ok(len(env.check_speed(50.0, 100.0)) == 0, "hız OK")
    ok(len(env.check_speed(50.0, 400.0)) > 0, "RPM=400 ihlali")
    ok(len(env.check_accel(1000.0)) > 0, "ivme=1000 ihlali")

    # Validasyon: soft dışında hard
    try:
        MachineEnvelope(x_hard_min_mm=0, x_soft_min_mm=-5)
        ok(False, "soft<hard ValueError beklenir")
    except ValueError:
        ok(True, "soft<hard ValueError")

    # Yol zarf kontrolü — silindir nominal
    prof, params = _cyl()
    path = generate_path(params)
    rpt = check_path_envelope(path, env, prof, eye_standoff_mm=150.0)
    ok(rpt.n_points_checked == len(path.points), "tüm noktalar kontrol edildi")
    ok(rpt.hard_limit_count == 0, "nominal silindir: sert ihlal yok")
    ok(rpt.is_within_envelope, "nominal silindir zarf içinde")

    # X aralığı çok büyük → sert ihlal
    env_tiny = MachineEnvelope(x_hard_min_mm=0, x_hard_max_mm=100,
                               x_soft_min_mm=5, x_soft_max_mm=95)
    rpt2 = check_path_envelope(path, env_tiny, prof)
    ok(rpt2.hard_limit_count > 0, "dar zarf: sert ihlal var")
    ok(not rpt2.is_within_envelope, "dar zarf: zarf dışında")


# ── Katman Birikimi ───────────────────────────────────────────────────────────

def test_layer_buildup():
    section("Katman Birikim Geometrisi")
    prof, params = _cyl()
    band = FiberBand(tow_width_mm=6.0, tow_thickness_mm=0.25, compaction_factor=0.85)
    lb = LayerBuildup(prof, band)

    # Katman kalınlığı = compacted_thickness
    close(lb.thickness_per_layer_mm, 0.25 * 0.85, 1e-9, "katman kalınlığı")

    # Radyal büyüme lineer
    close(lb.radius_growth_mm(4), 0.25 * 0.85 * 4, 1e-9, "4 katman radyal büyüme")
    close(lb.radius_growth_mm(0), 0.0, 1e-9, "0 katman büyüme = 0")

    # Profil büyüme
    p2 = lb.profile_after_layers(4)
    close(p2.avg_radius_mm, 50.0 + 0.25 * 0.85 * 4, 1e-6, "4 katman sonrası yarıçap")
    close(p2.length_mm, prof.length_mm, 1e-6, "uzunluk değişmez")

    # Sıra
    seq = lb.build_sequence(3)
    ok(len(seq) == 3, "build_sequence 3 profil")
    close(seq[0].avg_radius_mm, 50.0, 1e-6, "ilk katman = çıplak yarıçap")
    ok(seq[2].avg_radius_mm > seq[0].avg_radius_mm, "yarıçap katmanla artar")

    # Çap
    close(lb.diameter_at_layer(0), 100.0, 1e-6, "katman 0 çapı = 100mm")

    # Katman katman yol — açı sabit, Clairaut büyür
    res = generate_layered_paths(prof, band, params, n_layers=4, hold_angle=True)
    ok(res.n_layers == 4, "4 katman yolu")
    ok(len(res.paths) == 4, "4 path nesnesi")
    ok(res.final_radius_mm > res.base_radius_mm, "final yarıçap > taban")
    close(res.total_radial_growth_mm, 0.25 * 0.85 * 4, 1e-6, "toplam büyüme")
    # hold_angle: Clairaut c yarıçapla büyür (monoton artan)
    cs = res.clairaut_per_layer
    ok(cs[-1] > cs[0], "hold_angle: Clairaut c katmanla büyür")
    ok(res.total_fiber_length_mm > 0, "toplam fiber > 0")

    # hold_angle=False: Clairaut c yaklaşık sabit
    res2 = generate_layered_paths(prof, band, params, n_layers=4, hold_angle=False)
    cs2 = res2.clairaut_per_layer
    ok(abs(cs2[-1] - cs2[0]) < abs(cs[-1] - cs[0]) + 1e-9,
       "hold_angle=False: Clairaut c daha sabit")


# ── Fiber Yatırma ─────────────────────────────────────────────────────────────

def test_fiber_deposition():
    section("Fiber Yatırma Simülasyonu")
    prof, params = _cyl(n_layers=2)
    band = FiberBand(tow_width_mm=6.0, tow_thickness_mm=0.25, compaction_factor=0.85,
                     overlap_pct=5.0)
    path = generate_path(params)

    dep = simulate_deposition([path], band, prof, n_z=60, n_theta=120)

    # Boyut
    ok(dep.thickness_mm.shape == (60, 120), "yatırma haritası boyutu")

    # Kaplama
    ok(dep.coverage_pct > 0, "kaplama > 0")
    close(dep.coverage_pct + dep.uncovered_pct, 100.0, 0.01, "kaplama+kaplanmamış=100")

    # Kalınlık: kaplanan hücrelerde ≥ tek ply
    t_ply = band.compacted_thickness_mm
    ok(dep.max_thickness_mm >= t_ply - 1e-9, "maks kalınlık ≥ 1 ply")
    ok(dep.mean_thickness_mm > 0, "ortalama kalınlık > 0")
    # Bindirme bölgelerinde kalınlık birikir → maks ≥ 2 ply mümkün
    ok(dep.max_thickness_mm >= t_ply, "kalınlık birikimi tutarlı")

    # Tekdüzelik ve yönelim aralıkları
    ok(0.0 <= dep.thickness_uniformity() <= 1.0, "tekdüzelik ∈ [0,1]")
    mo = dep.mean_orientation_deg()
    ok(0.0 <= mo <= 90.0, f"ortalama yönelim ∈ [0,90]: {mo:.1f}")
    # Yönelim nominal açıya yakın olmalı (55°)
    ok(abs(mo - 55.0) < 20.0, "ortalama yönelim ≈ sarma açısı")

    # Kaplanmamış bölge tespiti
    regions = dep.find_uncovered_regions(min_area_mm2=1.0)
    ok(isinstance(regions, list), "kaplanmamış bölge listesi")

    # İki katman tek katmandan daha fazla toplam malzeme yatırır.
    # Karşılaştırma: total_thickness_sum kullan (kaplama genişlemesi
    # per-hücre ortalamayı düşürür ama toplam malzeme monotonik artar).
    prof_1lyr = MandrelProfile.cylinder(300.0, 50.0, n_points=80)
    params_1lyr = WindingPathParams(
        profile=prof_1lyr, alpha_deg=55.0, n_layers=1,
        tow_width_mm=6.0, overlap_pct=5.0, n_steps_per_pass=40,
    )
    path_1lyr = generate_path(params_1lyr)
    dep_1lyr = simulate_deposition([path_1lyr], band, prof_1lyr, n_z=40, n_theta=80)

    res2 = generate_layered_paths(prof, band, params, n_layers=2, hold_angle=True)
    dep2 = simulate_deposition(res2.paths, band, prof, n_z=40, n_theta=80)

    ok(dep2.total_thickness_sum > dep_1lyr.total_thickness_sum - 1e-9,
       "2 katman ≥ 1 katman toplam kalınlık toplamı")
    ok(dep2.coverage_pct >= dep_1lyr.coverage_pct - 0.1,
       "2 katman kaplama ≥ 1 katman kaplama")
    ok(dep2.max_thickness_mm >= dep_1lyr.max_thickness_mm - 1e-9,
       "2 katman maks kalınlık ≥ 1 katman")

    # Sıkıştırma modeli: katman 0 > herhangi bir faktör ≤ 1
    from faz17_d1.core.fiber_deposition import compaction_factor
    cf0 = compaction_factor(0, tension_N=50.0, radius_mm=50.0)
    cf1 = compaction_factor(1, tension_N=50.0, radius_mm=50.0)
    cf3 = compaction_factor(3, tension_N=50.0, radius_mm=50.0)
    ok(0.0 < cf0 <= 1.0, f"sıkıştırma faktörü katman-0 ∈ (0,1]: {cf0:.4f}")
    ok(0.0 < cf1 <= 1.0, f"sıkıştırma faktörü katman-1 ∈ (0,1]: {cf1:.4f}")
    ok(cf1 <= cf0 + 1e-9, "katman-1 sıkıştırma ≤ katman-0 (azalan artış)")
    ok(cf3 <= cf1 + 1e-9, "katman-3 sıkıştırma ≤ katman-1")
    ok(compaction_factor(0, tension_N=200.0) <= cf0 + 1e-9,
       "yüksek gerilim → daha az ya da eşit sıkıştırma")

    # Tekrarlı simülasyon bit-özdeş olmalı
    dep2b = simulate_deposition(res2.paths, band, prof, n_z=40, n_theta=80)
    ok(np.allclose(dep2.thickness_mm, dep2b.thickness_mm),
       "yatırma simülasyonu belirleyici (bit-özdeş)")


# ── Payout Dinamiği ───────────────────────────────────────────────────────────

def test_payout_dynamics():
    section("Payout Göz Dinamiği")
    cfg = PayoutDynamicsConfig(carriage_mass_kg=5.0, max_drive_force_N=2000.0,
                               eye_lag_time_const_s=0.03, standoff_mm=150.0)

    # İvme limiti = F/m*1000
    close(cfg.accel_limit_mm_s2, 2000.0 / 5.0 * 1000.0, 1e-6, "ivme limiti F/m")

    # Sabit hedef → yakınsama (lag → 0)
    t = np.linspace(0, 2.0, 400)
    x_const = np.full_like(t, 100.0)
    resp = simulate_eye_response(t, x_const, cfg)
    ok(abs(resp.x_actual_mm[-1] - 100.0) < 0.5, "sabit hedefe yakınsar")
    ok(resp.max_lag_error_mm >= 0, "maks gecikme ≥ 0")

    # Rampa hedef → sınırlı gecikme
    x_ramp = 50.0 * t   # 50 mm/s rampa
    resp2 = simulate_eye_response(t, x_ramp, cfg)
    ok(resp2.rms_lag_error_mm < 20.0, "rampa gecikmesi sınırlı")
    # Takip ediyor: son konum hedefe yakın
    ok(abs(resp2.x_actual_mm[-1] - x_ramp[-1]) < 10.0, "rampa takip ediliyor")

    # Düşük kuvvet → ivme doygunluğu artar
    cfg_weak = PayoutDynamicsConfig(carriage_mass_kg=50.0, max_drive_force_N=50.0,
                                    eye_lag_time_const_s=0.03)
    # Hızlı kare-dalga hedef
    x_step = np.where(t > 1.0, 200.0, 0.0)
    resp3 = simulate_eye_response(t, x_step, cfg_weak)
    ok(resp3.n_accel_saturated > 0, "zayıf tahrik: ivme doygunluğu oluşur")
    ok(resp3.max_accel_mm_s2 <= cfg_weak.accel_limit_mm_s2 * 1.01,
       "ivme limit içinde tutuldu")

    # Statik lead: standoff*tan(α)
    close(compute_carriage_lead_safe(55.0, 150.0),
          150.0 * math.tan(math.radians(55.0)), 1e-6, "lead = standoff·tan(α)")
    ok(compute_carriage_lead_safe(80.0, 150.0) > compute_carriage_lead_safe(40.0, 150.0),
       "yüksek açı → büyük lead")

    # Temas noktası hareketi
    eye_x = 50.0 * t
    prof = MandrelProfile.cylinder(300.0, 50.0)
    cpm = compute_contact_point_motion(t, eye_x, prof, cfg)
    ok(cpm.max_contact_velocity_mm_s > 0, "temas noktası hareket ediyor")
    close(cpm.max_contact_velocity_mm_s, 50.0, 5.0, "temas hızı ≈ göz hızı")

    # Validasyon
    try:
        PayoutDynamicsConfig(carriage_mass_kg=0.0)
        ok(False, "mass=0 ValueError beklenir")
    except ValueError:
        ok(True, "mass=0 ValueError")


# ── Tam Digital Twin ──────────────────────────────────────────────────────────

def test_winding_twin():
    section("Tam Digital Twin Simülasyonu")
    prof, params = _cyl(alpha=55.0, steps=40)
    band = FiberBand(tow_width_mm=6.0, overlap_pct=5.0)

    res = simulate_winding(prof, band, params, n_layers=3, dt_s=0.1)

    # Temel yapı
    ok(len(res.states) > 0, "durum çerçeveleri üretildi")
    ok(res.total_time_s > 0, "toplam süre > 0")
    ok(res.n_layers == 3, "3 katman")
    ok(len(res.layer_time_ranges_s) == 3, "3 katman zaman aralığı")

    # Zaman monoton
    ts = [s.t_s for s in res.states]
    ok(all(ts[i] <= ts[i + 1] for i in range(len(ts) - 1)), "zaman monoton artan")

    # İlerleme 0→100
    close(res.states[0].progress_pct, 0.0, 1.0, "ilk durum ilerleme ≈ 0")
    close(res.states[-1].progress_pct, 100.0, 1.0, "son durum ilerleme ≈ 100")

    # Spindle açısı monoton artan (kümülatif)
    angs = [s.spindle_angle_deg for s in res.states]
    ok(all(angs[i] <= angs[i + 1] + 1e-6 for i in range(len(angs) - 1)),
       "iş mili açısı kümülatif artan")

    # Fiber birikimi monoton artan
    fibers = [s.fiber_deposited_mm for s in res.states]
    ok(all(fibers[i] <= fibers[i + 1] + 1e-6 for i in range(len(fibers) - 1)),
       "fiber birikimi monoton artan")
    ok(fibers[-1] > 0, "fiber yatırıldı")

    # Yarıçap katmanla büyür
    radii_by_layer = {}
    for s in res.states:
        radii_by_layer.setdefault(s.current_layer, s.current_radius_mm)
    ok(radii_by_layer[2] > radii_by_layer[0], "yüzey yarıçapı katmanla büyür")

    # Göz radyal = yüzey + standoff
    s0 = res.states[len(res.states) // 2]
    close(s0.eye_r_mm - s0.current_radius_mm, 150.0, 1e-6, "göz_r = yüzey + standoff")

    # RPM makine limiti içinde (300 cap)
    ok(res.max_spindle_rpm <= 300.0 * 1.05, "maks RPM ≈ makine limiti")

    # Katman zaman aralıkları örtüşmez, sıralı
    lr = res.layer_time_ranges_s
    for i in range(len(lr) - 1):
        ok(lr[i][1] <= lr[i + 1][0] + 1e-6, f"katman {i}/{i+1} aralıkları sıralı")

    # state_at çalışıyor
    mid = res.state_at(res.total_time_s / 2)
    ok(0 <= mid.progress_pct <= 100, "state_at orta nokta geçerli")

    # Yatırma haritası
    ok(res.final_deposition.coverage_pct > 0, "twin yatırma kaplaması > 0")


# ── Üretim Raporu ─────────────────────────────────────────────────────────────

def test_production_report():
    section("Üretim Raporu")
    prof, params = _cyl(alpha=55.0, n_layers=2)
    band = FiberBand(tow_width_mm=6.0, overlap_pct=5.0)
    path = generate_path(params)
    process = ProcessParameters()

    rpt = generate_production_report(path, band, prof, process=process,
                                     n_z=50, n_theta=120)

    # Alt-raporlar mevcut
    ok(rpt.manufacturability is not None, "üretilebilirlik alt-raporu")
    ok(rpt.deposition is not None, "yatırma haritası")
    ok(rpt.payout is not None, "payout raporu")
    ok(rpt.machine_limits is not None, "makine limiti raporu")
    ok(rpt.saturation is not None, "saturasyon raporu")

    # Malzeme tahmini tutarlı
    m = rpt.material
    ok(m.fiber_length_mm > 0, "fiber uzunluğu > 0")
    close(m.fiber_mass_g + m.resin_mass_g, m.total_mass_g, 1e-6,
          "malzeme kütle dengesi")
    ok(m.composite_volume_cm3 > 0, "kompozit hacmi > 0")
    ok(m.fiber_mass_g == process.fiber_mass_g(path.total_fiber_length_mm),
       "fiber kütlesi proses ile tutarlı")

    # Süre tahmini
    ok(rpt.time_estimate.motion_time_s > 0, "hareket süresi > 0")
    close(rpt.time_estimate.total_time_s,
          rpt.time_estimate.motion_time_s + rpt.time_estimate.setup_time_s, 1e-6,
          "toplam süre = hareket + kurulum")

    # Verdict
    ok(rpt.verdict() in ("★★★ ÜRETİME HAZIR ★★★", "DURUN — ÜRETİLEMEZ"),
       "verdict geçerli")
    ok(isinstance(rpt.is_production_ready, bool), "is_production_ready bool")

    # Özet
    ok(len(rpt.summary()) > 100, "özet üretildi")

    # Dar makine → engelleyici sorun
    tiny = MachineEnvelope(x_hard_min_mm=0, x_hard_max_mm=100,
                           x_soft_min_mm=5, x_soft_max_mm=95)
    rpt2 = generate_production_report(path, band, prof, machine=tiny,
                                     n_z=40, n_theta=80)
    ok(not rpt2.is_production_ready, "dar makine: üretilemez")
    ok(len(rpt2.blocking_issues) > 0, "dar makine: engelleyici sorun var")


# ── Oynatma Motoru ────────────────────────────────────────────────────────────

def test_simulation_playback():
    section("Simülasyon Oynatma Motoru")
    prof, params = _cyl(steps=30)
    band = FiberBand(tow_width_mm=6.0, overlap_pct=5.0)
    res = simulate_winding(prof, band, params, n_layers=2, dt_s=0.1)

    pb = SimulationPlayback(res)

    # Başlangıç durumu
    ok(not pb.is_playing, "başlangıçta duraklatılmış")
    close(pb.time_s, 0.0, 1e-9, "başlangıç zamanı 0")
    close(pb.duration_s, res.total_time_s, 1e-9, "süre eşleşiyor")

    # Oynat/duraklat
    pb.play()
    ok(pb.is_playing, "play çalışıyor")
    pb.pause()
    ok(not pb.is_playing, "pause çalışıyor")
    pb.toggle()
    ok(pb.is_playing, "toggle çalışıyor")

    # Hız çarpanı ile ilerleme
    pb.stop()
    pb.play()
    pb.set_speed(2.0)
    pb.advance(1.0)   # 1s wall × 2 = 2s sanal
    close(pb.time_s, 2.0, 1e-6, "2× hızda 1s → 2s sanal")

    # Duraklatılmışken ilerlemez
    pb.pause()
    t_before = pb.time_s
    pb.advance(5.0)
    close(pb.time_s, t_before, 1e-9, "duraklatılmışken ilerlemez")

    # Seek
    pb.seek(res.total_time_s / 2)
    close(pb.progress_pct, 50.0, 2.0, "seek ortaya → %50")
    pb.seek_fraction(0.25)
    close(pb.progress_pct, 25.0, 2.0, "seek_fraction(0.25) → %25")

    # Seek sınırları
    pb.seek(-10.0)
    close(pb.time_s, 0.0, 1e-9, "negatif seek → 0")
    pb.seek(1e9)
    close(pb.time_s, res.total_time_s, 1e-9, "aşırı seek → süre")

    # Sona ulaşınca durur
    pb.stop()
    pb.play()
    pb.set_speed(1000.0)
    pb.advance(res.total_time_s)
    ok(pb.at_end, "sona ulaşıldı")
    ok(not pb.is_playing, "sonda otomatik duraklatma")

    # Katman katman
    ok(pb.n_layers == 2, "2 katman")
    pb.seek_layer(1)
    t0, t1 = pb.layer_time_range(1)
    close(pb.time_s, t0, 1e-6, "seek_layer(1) → katman başı")

    # Telemetri
    pb.seek(res.total_time_s / 2)
    tele = pb.telemetry_snapshot()
    for key in ["t_s", "is_mili_rpm", "tasiyici_x_mm", "fiber_yatirilan_mm",
                "katman", "ilerleme_pct"]:
        ok(key in tele, f"telemetri anahtarı '{key}'")
    ok(tele["ilerleme_pct"] > 0, "telemetri ilerleme > 0")

    # Hız validasyonu
    try:
        pb.set_speed(0.0)
        ok(False, "speed=0 ValueError beklenir")
    except ValueError:
        ok(True, "speed=0 ValueError")


# ── Entegrasyon ───────────────────────────────────────────────────────────────

def test_integration():
    section("Entegrasyon: Tam Üretim Akışı")
    # Çıplak mandrel → çok katmanlı twin → üretim raporu → oynatma
    prof = MandrelProfile.cylinder(250.0, 45.0, n_points=80)
    band = FiberBand(tow_width_mm=5.0, tow_thickness_mm=0.22, compaction_factor=0.88,
                     overlap_pct=8.0)
    params = WindingPathParams(profile=prof, alpha_deg=52.0, n_layers=1,
                               tow_width_mm=5.0, overlap_pct=8.0, n_steps_per_pass=40)
    process = ProcessParameters(resin_content_pct=33.0, tow_count=3, tow_tex_g_km=600.0)
    machine = MachineEnvelope()
    tension = FiberTensionModel(tension_N=40.0, friction_coeff=0.35)

    # Gerilim kararlılık kontrolü
    ok(tension.is_tension_stable(45.0, 5.0), "gerilim kararlı")

    # Twin
    res = simulate_winding(prof, band, params, n_layers=4, machine=machine,
                           tension=tension, dt_s=0.1)
    ok(res.final_radius_mm > res.base_radius_mm, "yarıçap büyüdü")
    ok(len(res.states) > 10, "yeterli durum çerçevesi")

    # Üretim raporu (son katman yolu üzerinde)
    last_path = res.layered_paths.paths[-1]
    last_prof = res.layered_paths.profiles[-1]
    rpt = generate_production_report(last_path, band, last_prof, process=process,
                                     machine=machine, n_z=40, n_theta=80)
    ok(rpt.material.total_mass_g > 0, "malzeme kütlesi hesaplandı")

    # Oynatma
    pb = SimulationPlayback(res)
    pb.play()
    pb.set_speed(10.0)
    steps = 0
    while not pb.at_end and steps < 10000:
        pb.advance(0.1)
        steps += 1
    ok(pb.at_end, "oynatma sona ulaştı")
    final_tele = pb.telemetry_snapshot()
    close(final_tele["ilerleme_pct"], 100.0, 1.0, "oynatma sonunda %100")


# ── Sprint 4B: Zamanlama / Kararlılık / Belirleyicilik ──────────────────────────

def test_trajectory_builder():
    section("Sprint 4B — Yörünge Üreticisi (trajectory_builder)")
    from faz17_d1.core.trajectory_builder import (
        TrajectorySegment, TwinTimeline, build_timeline,
    )

    # Bilinen iki segment: X 0→100mm, A 0→720° (5s); sonra X 100→100, A 720→1080° (5s)
    segs = [
        TrajectorySegment(0.0, 5.0, 0.0, 100.0, 0.0, 720.0, 0, 0, 50.0),
        TrajectorySegment(5.0, 10.0, 100.0, 100.0, 720.0, 1080.0, 1, 0, 51.0),
    ]
    tl = build_timeline(segs, dt_s=1.0)

    # Tekdüze dt
    ok(tl.n_samples == 11, f"11 örnek (0..10s @ dt=1): {tl.n_samples}")
    dts = np.diff(tl.t_s)
    ok(np.allclose(dts, 1.0), "zaman ızgarası tekdüze (Δt=1.0)")

    # Küresel açı monoton artan (sıfırlama yok)
    ok(np.all(np.diff(tl.a_deg) >= -1e-9), "küresel açı monoton artan")
    close(tl.a_deg[0], 0.0, 1e-9, "a(0)=0")
    close(tl.a_deg[-1], 1080.0, 1e-6, "a(10)=1080°")

    # RPM formülü: ilk 5s'de 720°/5s = 144°/s = 24 RPM
    #   her 1s adımında Δa=144° → rpm = 144/360*60 = 24
    close(tl.rpm[1], 24.0, 1e-6, "rpm = |Δa/Δt|/360×60 = 24")
    # ikinci segment: 360°/5s = 72°/s → her adımda Δa=72° → rpm=12
    close(tl.rpm[7], 12.0, 1e-6, "ikinci segment rpm = 12")
    ok(np.all(tl.rpm >= 0.0), "rpm her zaman ≥ 0")

    # Spindle hızı işaretli ama burada pozitif
    close(tl.spindle_v_deg_s[1], 144.0, 1e-6, "iş mili hızı = 144°/s")

    # Taşıyıcı hızı: ilk 5s'de 100mm/5s = 20mm/s
    close(tl.carriage_v_mm_s[1], 20.0, 1e-6, "taşıyıcı hızı = 20mm/s")
    # ikinci segmentte X sabit → hız 0
    close(tl.carriage_v_mm_s[7], 0.0, 1e-9, "X sabitken taşıyıcı hızı = 0")

    # index_at O(1) ve doğru
    ok(tl.index_at(0.0) == 0, "index_at(0)=0")
    ok(tl.index_at(10.0) == 10, "index_at(10)=10")
    ok(tl.index_at(4.4) == 4, "index_at(4.4)=4 (round)")
    ok(tl.index_at(4.6) == 5, "index_at(4.6)=5 (round)")
    ok(tl.index_at(1e9) == tl.n_samples - 1, "aşırı t → son indeks (kenetli)")
    ok(tl.index_at(-5.0) == 0, "negatif t → 0 (kenetli)")

    # NaN yok
    for name, arr in [("x", tl.x_mm), ("a", tl.a_deg), ("rpm", tl.rpm),
                      ("v", tl.carriage_v_mm_s), ("fiber", tl.fiber_mm)]:
        ok(not np.any(np.isnan(arr)), f"{name} dizisinde NaN yok")

    # Fiber birikimi monoton
    ok(np.all(np.diff(tl.fiber_mm) >= -1e-9), "fiber birikimi monoton artan")

    # Yapılandırılabilir dt: daha küçük dt → daha çok örnek
    tl_fine = build_timeline(segs, dt_s=0.5)
    ok(tl_fine.n_samples == 21, f"dt=0.5 → 21 örnek: {tl_fine.n_samples}")

    # dt ≤ 0 reddedilir
    try:
        build_timeline(segs, dt_s=0.0)
        ok(False, "dt=0 ValueError beklenir")
    except ValueError:
        ok(True, "dt=0 ValueError")

    # Boş segment listesi → boş zaman çizelgesi
    empty = build_timeline([], dt_s=1.0)
    ok(empty.n_samples == 0, "boş segment → boş zaman çizelgesi")


def test_sprint4b_twin_timing():
    section("Sprint 4B — Twin Zamanlama / Kararlılık / Belirleyicilik")
    prof, params = _cyl(alpha=55.0, steps=30)
    band = FiberBand(tow_width_mm=6.0, overlap_pct=5.0)
    payout = PayoutDynamicsConfig(eye_lag_time_const_s=0.03, standoff_mm=150.0)
    machine = MachineEnvelope()

    res = simulate_winding(prof, band, params, n_layers=2, machine=machine,
                           payout=payout, dt_s=1.0)

    # ── RPM doğruluğu ────────────────────────────────────────────────────────
    # Astronomik RPM hatası giderildi: makul üst sınır içinde
    rpm_limit = machine.max_spindle_deg_s / 360.0 * 60.0  # = 300 RPM
    ok(res.max_spindle_rpm <= rpm_limit + 1e-6,
       f"maks RPM ≤ makine limiti ({res.max_spindle_rpm:.2f} ≤ {rpm_limit:.0f})")
    ok(res.max_spindle_rpm < 1000.0, "maks RPM astronomik DEĞİL (eski hata yok)")
    # Her durumda rpm = |Δa/Δt|/360×60 — örnekleme ile doğrula
    for i in range(1, min(50, len(res.states))):
        da = res.states[i].spindle_angle_deg - res.states[i - 1].spindle_angle_deg
        dt = res.states[i].t_s - res.states[i - 1].t_s
        expected = abs(da / dt) / 360.0 * 60.0 if dt > 0 else 0.0
        if abs(res.states[i].spindle_rpm - expected) > 1e-6:
            ok(False, f"rpm formülü durum {i}: {res.states[i].spindle_rpm} vs {expected}")
            break
    else:
        ok(True, "rpm = |Δa/Δt|/360×60 tüm örneklerde doğru")
    ok(res.max_spindle_rpm > 0.0, "maks RPM > 0 (iş mili dönüyor)")

    # ── Sınırlı gecikme ──────────────────────────────────────────────────────
    tau = payout.eye_lag_time_const_s
    peak_v = max(abs(s.carriage_v_mm_s) for s in res.states)
    expected_max_lag = peak_v * tau
    ok(res.max_lag_error_mm <= expected_max_lag + 1e-6,
       f"maks gecikme = peak_v·τ ile sınırlı ({res.max_lag_error_mm:.4f} ≤ {expected_max_lag:.4f})")
    ok(res.max_lag_error_mm < 10.0, "gecikme < 10mm (eski 17.736mm hatası yok)")
    # Her durumda lag = v·τ
    for s in res.states:
        if abs(s.lag_error_mm - s.carriage_v_mm_s * tau) > 1e-6:
            ok(False, "lag = v·τ ihlali")
            break
    else:
        ok(True, "gecikme = v·τ tüm durumlarda")

    # ── Salınımsız (oscillation yok) ─────────────────────────────────────────
    # Birinci-derece model: |x_actual − x_ref| = |lag| ≤ max_lag (büyüme yok)
    max_dev = max(abs(s.carriage_x_actual_mm - s.carriage_x_mm) for s in res.states)
    ok(max_dev <= res.max_lag_error_mm + 1e-6, "konum sapması gecikmeyle sınırlı (salınım yok)")
    # x_actual makul aralıkta (patlama yok)
    ok(all(-50.0 <= s.carriage_x_actual_mm <= 450.0 for s in res.states),
       "gerçek taşıyıcı konumu sınırlı aralıkta (ıraksama yok)")

    # ── NaN üretimi yok ──────────────────────────────────────────────────────
    fields = ("t_s", "spindle_angle_deg", "spindle_rpm", "carriage_x_mm",
              "carriage_x_actual_mm", "carriage_v_mm_s", "eye_x_mm", "eye_r_mm",
              "fiber_deposited_mm", "current_radius_mm", "lag_error_mm", "progress_pct")
    has_nan = False
    for s in res.states:
        for f in fields:
            if math.isnan(getattr(s, f)) or math.isinf(getattr(s, f)):
                has_nan = True
                break
        if has_nan:
            break
    ok(not has_nan, "hiçbir durum alanında NaN/Inf yok")

    # ── Belirleyicilik ───────────────────────────────────────────────────────
    res2 = simulate_winding(prof, band, params, n_layers=2, machine=machine,
                            payout=payout, dt_s=1.0)
    ok(len(res.states) == len(res2.states), "tekrar: aynı durum sayısı")
    identical = all(
        a.t_s == b.t_s and a.spindle_angle_deg == b.spindle_angle_deg
        and a.carriage_x_mm == b.carriage_x_mm and a.spindle_rpm == b.spindle_rpm
        and a.fiber_deposited_mm == b.fiber_deposited_mm
        for a, b in zip(res.states, res2.states)
    )
    ok(identical, "tekrar: bit-aynı durum dizisi (deterministik)")

    # ── Durum sayısı ölçeklenmesi ────────────────────────────────────────────
    res_half = simulate_winding(prof, band, params, n_layers=2, machine=machine,
                                payout=payout, dt_s=0.5)
    ratio = len(res_half.states) / len(res.states)
    ok(1.8 <= ratio <= 2.2, f"dt yarıya inince durum ~2× ({ratio:.2f})")
    expected_n = int(math.floor(res.total_time_s / 1.0 + 1e-9)) + 1
    ok(len(res.states) == expected_n, f"durum sayısı = floor(T/dt)+1 ({len(res.states)}={expected_n})")

    # dt=1.0 dt=0.1'e göre ~10× daha az durum (eski 32k → 3k)
    res_fine = simulate_winding(prof, band, params, n_layers=2, machine=machine,
                                payout=payout, dt_s=0.1)
    reduction = len(res_fine.states) / len(res.states)
    ok(9.0 <= reduction <= 11.0, f"dt=1.0, dt=0.1'e göre ~10× az durum ({reduction:.1f}×)")

    # ── state_at O(1) ────────────────────────────────────────────────────────
    mid = res.state_at(res.total_time_s / 2.0)
    ok(0 <= mid.progress_pct <= 100, "state_at orta nokta geçerli")
    ok(res.state_at(0.0) is res.states[0], "state_at(0) ilk durum (O(1) indeks)")
    ok(res.state_at(1e9) is res.states[-1], "state_at(∞) son durum (kenetli)")


# ── Koşucu ────────────────────────────────────────────────────────────────────

def main():
    suites = [
        test_process_parameters,
        test_fiber_tension,
        test_machine_envelope,
        test_layer_buildup,
        test_fiber_deposition,
        test_payout_dynamics,
        test_winding_twin,
        test_production_report,
        test_simulation_playback,
        test_integration,
        test_trajectory_builder,
        test_sprint4b_twin_timing,
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

    with open('/tmp/twin_test_result.txt', 'w') as f:
        f.write(report)
    print(report)
    if FAIL > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
