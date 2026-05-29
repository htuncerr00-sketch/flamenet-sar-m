"""
core/machine_execution.py — Gerçek Makine Yürütme Dinamiği
===========================================================
Sarma CAM yörüngesini gerçek makine yürütme etkileriyle simüle eder:

    1. Backlash — yön değişiminde dişli boşluk gecikmesi
    2. Eksen gecikmesi (lag) — ilk-derece servomechanizm filtresi
    3. İş mili ataleti — ikinci-derece açısal dinamik
    4. Adım kuantizasyonu — sürücü ve enkoder ayrıklığı
    5. Besleme düzeltmesi — eğrilik, kubbe, ivme-farkındalıklı dinamik sınırlama
    6. Rotary senkronizasyon — A-ekseninin X'e göre doğru fazı

Çıktı: `ExecutionTimeline` — ideal CAM değerlerinin yanında her etkinin
ayrı kanalı, takip hataları ve senkronizasyon faz hatası.

Fizik modelleri
---------------
Birinci-derece lag (eksen servo filtresi):
    τ·dx/dt + x = x_cmd  →  x[i] = x[i-1] + (dt/τ)·(x_cmd[i] − x[i-1])
    Kararlı çünkü dt << τ garantilenmiş.

İkinci-derece iş mili (inertia + sürtünme):
    J·α̈ + b·α̇ = k·(ω_cmd − ω)  →  zaman sabitesi τ_a = J / b
    dt-ayrık: ω[i] = ω[i-1] + dt/τ_a · (ω_cmd[i] − ω[i-1])
    (Birinci-derece yaklaşım geçerli: τ_a = J/b >> dt)

Backlash (yön bazlı):
    direction_prev → direction_curr:  yön değişince boşluk kadar geri-kal
    x_actual[i] = x_lag[i] − dir · backlash  (yönle aynı tarafta gecikme)

Besleme sınırlama (eğrilik bazlı):
    κ(z) = |r''(z)| / (1 + r'(z)²)^(3/2)       (meridyen eğriliği)
    v_max_curve = sqrt(a_limit / κ)   [κ > κ_min ise]
    v_max_feed = min(v_cmd, v_max_curve, v_dome_factor·v_cmd)

Rotary senkronizasyon (Clairaut):
    Silindir: dA_req/dz = tan(α_nom)/r_c · (180/π)   [deg/mm]
    Genel:    dA_req/dz = c / (r(z) · sqrt(r(z)²−c²)) · (180/π)
    Faz hatası: φ_err[i] = A_actual[i] − A_req(x_actual[i])
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from .geometry_engine import MandrelProfile
from .machine_calibration import MachineCalibration, default_calibration
from .path_generator import WindingPath
from .trajectory_builder import TwinTimeline


# ── Yürütme kısıtlamaları ─────────────────────────────────────────────────────

@dataclass
class ExecutionConstraints:
    """
    Gerçek makine yürütme fiziksel kısıtlamaları.

    Lag zaman sabitleri sürücü + kontrolör bandwdith'ini temsil eder.
    İş mili ataleti J ve sürtünme b ikinci-derece modelde kullanılır.
    """
    # Eksen lag (birinci-derece)
    carriage_lag_tau_s: float = 0.08       # X-ekseni lag zaman sabiti (s)
    spindle_lag_tau_s: float = 0.15        # A-ekseni lag zaman sabiti (s)

    # İş mili ataleti (ikinci-derece)
    spindle_inertia_kg_m2: float = 0.005   # J
    spindle_friction_Nm_s_rad: float = 0.02 # b  → τ_inertia = J/b

    # Backlash
    backlash_carriage_mm: float = 0.02
    backlash_spindle_deg: float = 0.05

    # İvme limitleri (besleme düzeltmesi için)
    max_carriage_accel_mm_s2: float = 500.0
    max_spindle_accel_deg_s2: float = 3600.0

    # Besleme düzeltme faktörleri
    curvature_accel_limit_mm_s2: float = 200.0   # eğrilik için izin verilen merkezcil ivme
    dome_transition_feed_factor: float = 0.60     # kubbe geçişinde besleme fraksiyonu
    dome_curvature_threshold: float = 0.005       # mm⁻¹ — bu üzeri "kubbe"

    # Senkronizasyon
    max_sync_phase_error_deg: float = 2.0         # izin verilen maks faz hatası


# ── Yürütme zaman çizelgesi ───────────────────────────────────────────────────

@dataclass
class ExecutionTimeline:
    """
    Gerçek makine yürütme simülasyon çıktısı.

    Her dizi `TwinTimeline` ile aynı zaman ızgarasındadır.
    commanded değerler ideal CAM değerleri; actual simüle edilmiş gerçek.
    """
    t_s:   np.ndarray

    # Taşıyıcı (X)
    x_commanded_mm:   np.ndarray
    x_actual_mm:      np.ndarray   # lag + backlash sonrası
    x_encoder_mm:     np.ndarray   # enkoder kuantizasyonu sonrası
    x_feed_mm_s:      np.ndarray   # dinamik sınırlı besleme
    following_error_x_mm: np.ndarray  # x_actual − x_commanded

    # İş mili (A)
    a_commanded_deg:  np.ndarray
    a_actual_deg:     np.ndarray   # lag + inertia sonrası
    a_encoder_deg:    np.ndarray   # enkoder kuantizasyonu sonrası
    spindle_rpm_commanded: np.ndarray
    spindle_rpm_actual:    np.ndarray
    following_error_a_deg: np.ndarray

    # Backlash durum
    backlash_active_x: np.ndarray   # bool uint8
    backlash_active_a: np.ndarray

    # Besleme sınırlama
    feed_limited: np.ndarray        # bool uint8
    dome_region:  np.ndarray        # bool uint8

    # Rotary senkronizasyon faz hatası
    sync_phase_error_deg: np.ndarray   # A_actual − A_required(x_actual)

    # Özet istatistikler (skalalar)
    dt_s: float
    max_following_error_x_mm: float
    max_following_error_a_deg: float
    rms_sync_error_deg: float
    n_feed_limited: int
    n_backlash_x: int
    n_backlash_a: int
    n_sync_exceeded: int

    def summary(self) -> str:
        return (
            f"YürütmeTimeline: {len(self.t_s)} örnek @ dt={self.dt_s*1000:.0f}ms | "
            f"maks_takip_x={self.max_following_error_x_mm:.3f}mm | "
            f"maks_takip_a={self.max_following_error_a_deg:.3f}° | "
            f"rms_faz_hatası={self.rms_sync_error_deg:.3f}° | "
            f"besleme_sınırlı={self.n_feed_limited} | "
            f"backlash_x={self.n_backlash_x} backlash_a={self.n_backlash_a}"
        )


# ── Yardımcı: meridyen eğrilik dizisi ────────────────────────────────────────

def _meridian_curvature(profile: MandrelProfile, z_arr: np.ndarray) -> np.ndarray:
    """
    κ(z) = |r''(z)| / (1 + r'(z)²)^(3/2)  —  meridyen eğriliği (mm⁻¹).

    Merkezi fark türevleri profile.z_mm ızgarasında hesaplanır;
    talep edilen z_arr noktalarına doğrusal interpolasyon uygulanır.
    """
    z = profile.z_mm
    r = profile.r_mm
    dz = np.diff(z)
    dz = np.where(np.abs(dz) < 1e-12, 1e-12, dz)

    # Birinci türev (tekdüze z ızgarasını zorla — duplicate noktaları önle)
    dz_arr = np.diff(z)
    if np.any(np.abs(dz_arr) < 1e-12):
        # Yinelenen z noktaları var; tekdüze yeniden örnekle
        z_uni = np.linspace(float(z[0]), float(z[-1]), len(z))
        r_uni = np.interp(z_uni, z, r)
    else:
        z_uni, r_uni = z, r

    with np.errstate(divide="ignore", invalid="ignore"):
        rp = np.gradient(r_uni, z_uni)
        rpp = np.gradient(rp, z_uni)
        kappa = np.abs(rpp) / (1.0 + rp ** 2) ** 1.5

    # NaN/Inf → 0 (örn. kubbe ucundaki sayısal sorunlar)
    kappa = np.nan_to_num(kappa, nan=0.0, posinf=0.0, neginf=0.0)

    # İstenen noktalara interpolasyon
    return np.interp(z_arr, z_uni, kappa)


# ── Yardımcı: Clairaut rotary senkron faz ────────────────────────────────────

def _required_spindle_angle(
    x_actual: np.ndarray,
    clairaut_c: float,
    profile: MandrelProfile,
    a0_deg: float = 0.0,
) -> np.ndarray:
    """
    Clairaut geodezikine göre x_actual konumlarında iş mili faz gereksinimi.

    Tümlev: A_req(x) = a0 + ∫_{x0}^{x} c/(r(z)·sqrt(r(z)²−c²)) dz · (180/π)
    Ayrık: kümülatif trapez toplamı olarak hesaplanır.
    c = Clairaut sabiti = r_cyl · sin(α_nominal).
    """
    r_arr = np.interp(x_actual, profile.z_mm, profile.r_mm)
    r2mc2 = r_arr ** 2 - clairaut_c ** 2

    # r < c olan noktalarda (lift-off bölgesi) integrand = 0 (hareket yok)
    valid = r2mc2 > 0.0
    integrand = np.where(valid, clairaut_c / (r_arr * np.sqrt(np.where(valid, r2mc2, 1.0))), 0.0)
    integrand_deg = integrand * (180.0 / math.pi)

    dx = np.diff(x_actual, prepend=x_actual[0])
    a_req = a0_deg + np.cumsum(integrand_deg * dx)
    return a_req


# ── Ana simülasyon fonksiyonu ─────────────────────────────────────────────────

def simulate_execution(
    timeline: TwinTimeline,
    path: WindingPath,
    profile: MandrelProfile,
    constraints: Optional[ExecutionConstraints] = None,
    calib: Optional[MachineCalibration] = None,
) -> ExecutionTimeline:
    """
    TwinTimeline'ı gerçek makine yürütme etkileriyle simüle et.

    Adımlar (her zaman adımında sırayla):
        1. Besleme sınırlama (eğrilik + kubbe)
        2. Taşıyıcı lag (birinci-derece filtre)
        3. İş mili lag + inertia (birinci-derece filtre)
        4. Backlash (yön değişiminde)
        5. Adım / enkoder kuantizasyonu
        6. Rotary faz hatası (Clairaut'dan beklenen − gerçek)

    Tüm işlemler vektörel (NumPy); döngüsüz.
    """
    if constraints is None:
        constraints = ExecutionConstraints()
    if calib is None:
        calib = default_calibration()

    n = timeline.n_samples
    if n == 0:
        _z = np.zeros(0)
        return ExecutionTimeline(
            t_s=_z, x_commanded_mm=_z, x_actual_mm=_z, x_encoder_mm=_z,
            x_feed_mm_s=_z, following_error_x_mm=_z,
            a_commanded_deg=_z, a_actual_deg=_z, a_encoder_deg=_z,
            spindle_rpm_commanded=_z, spindle_rpm_actual=_z,
            following_error_a_deg=_z, backlash_active_x=_z, backlash_active_a=_z,
            feed_limited=_z, dome_region=_z, sync_phase_error_deg=_z,
            dt_s=timeline.dt_s, max_following_error_x_mm=0.0,
            max_following_error_a_deg=0.0, rms_sync_error_deg=0.0,
            n_feed_limited=0, n_backlash_x=0, n_backlash_a=0, n_sync_exceeded=0,
        )

    dt = timeline.dt_s
    x_cmd = timeline.x_mm.copy()
    a_cmd = timeline.a_deg.copy()
    feed_cmd = np.abs(timeline.carriage_v_mm_s).copy()
    rpm_cmd = timeline.rpm.copy()

    # ── 1. Besleme sınırlama ────────────────────────────────────────────────
    kappa = _meridian_curvature(profile, x_cmd)
    dome_mask = kappa > constraints.dome_curvature_threshold

    # Eğrilik bazlı maks hız
    with np.errstate(divide="ignore", invalid="ignore"):
        v_max_curve = np.where(
            kappa > 1e-9,
            np.sqrt(constraints.curvature_accel_limit_mm_s2 / np.maximum(kappa, 1e-12)),
            feed_cmd,
        )
    v_max_curve = np.clip(v_max_curve, 0.0, None)

    feed_limited_mask = (feed_cmd > v_max_curve) | dome_mask
    feed_actual = feed_cmd.copy()
    feed_actual = np.minimum(feed_actual, v_max_curve)
    feed_actual[dome_mask] = np.minimum(
        feed_actual[dome_mask],
        feed_cmd[dome_mask] * constraints.dome_transition_feed_factor,
    )

    # ── 2. Taşıyıcı lag (birinci-derece IIR filtre) ─────────────────────────
    tau_x = max(constraints.carriage_lag_tau_s, dt * 2.0)
    alpha_x = dt / tau_x  # ∈ (0, 1)

    x_lag = np.zeros(n)
    x_lag[0] = x_cmd[0]
    for i in range(1, n):
        x_lag[i] = x_lag[i - 1] + alpha_x * (x_cmd[i] - x_lag[i - 1])

    # ── 3. İş mili lag + inertia (birinci-derece IIR) ──────────────────────
    # Efektif zaman sabiti: max(lag_tau, J/b)
    # J/b → saniye cinsinden (kg⋅m² / (Nm⋅s/rad) = s)
    tau_inertia = (constraints.spindle_inertia_kg_m2 /
                   max(constraints.spindle_friction_Nm_s_rad, 1e-12))
    tau_a = max(constraints.spindle_lag_tau_s, tau_inertia, dt * 2.0)
    alpha_a = dt / tau_a

    a_lag = np.zeros(n)
    a_lag[0] = a_cmd[0]
    for i in range(1, n):
        a_lag[i] = a_lag[i - 1] + alpha_a * (a_cmd[i] - a_lag[i - 1])

    # RPM gerçek = açısal hız türevi
    rpm_actual = np.abs(np.gradient(a_lag, dt)) / 360.0 * 60.0

    # ── 4. Backlash ─────────────────────────────────────────────────────────
    dir_x = np.sign(np.diff(x_lag, prepend=x_lag[0]))
    dir_a = np.sign(np.diff(a_lag, prepend=a_lag[0]))
    dir_x_prev = np.roll(dir_x, 1); dir_x_prev[0] = dir_x[0]
    dir_a_prev = np.roll(dir_a, 1); dir_a_prev[0] = dir_a[0]

    bl_x_mask = (dir_x != 0) & (dir_x_prev != 0) & (dir_x != dir_x_prev)
    bl_a_mask = (dir_a != 0) & (dir_a_prev != 0) & (dir_a != dir_a_prev)

    x_actual = x_lag.copy()
    a_actual = a_lag.copy()

    # Yön değişiminde gerçek pozisyon backlash kadar geri kalır
    backlash_x_mm = constraints.backlash_carriage_mm
    backlash_a_deg = constraints.backlash_spindle_deg

    x_backlog = 0.0
    a_backlog = 0.0
    for i in range(1, n):
        if bl_x_mask[i]:
            x_backlog = backlash_x_mm * dir_x[i - 1]
        x_actual[i] = x_lag[i] - x_backlog
        if bl_a_mask[i]:
            a_backlog = backlash_a_deg * dir_a[i - 1]
        a_actual[i] = a_lag[i] - a_backlog

    # ── 5. Enkoder kuantizasyonu ────────────────────────────────────────────
    enc_x = calib.carriage.encoder_counts_per_unit
    enc_a = calib.spindle.encoder_counts_per_unit

    x_encoder = np.round(x_actual * enc_x) / enc_x
    a_encoder = np.round(a_actual * enc_a) / enc_a

    # ── 6. Rotary senkronizasyon faz hatası ─────────────────────────────────
    c = path.clairaut_c
    a_req = _required_spindle_angle(x_actual, c, profile, a0_deg=float(a_actual[0]))
    phase_err = a_actual - a_req

    # ── Özet istatistikler ───────────────────────────────────────────────────
    fe_x = x_actual - x_cmd
    fe_a = a_actual - a_cmd
    sync_exceeded = np.abs(phase_err) > constraints.max_sync_phase_error_deg

    return ExecutionTimeline(
        t_s=timeline.t_s.copy(),
        x_commanded_mm=x_cmd,
        x_actual_mm=x_actual,
        x_encoder_mm=x_encoder,
        x_feed_mm_s=feed_actual,
        following_error_x_mm=fe_x,
        a_commanded_deg=a_cmd,
        a_actual_deg=a_actual,
        a_encoder_deg=a_encoder,
        spindle_rpm_commanded=rpm_cmd,
        spindle_rpm_actual=rpm_actual,
        following_error_a_deg=fe_a,
        backlash_active_x=bl_x_mask.astype(np.uint8),
        backlash_active_a=bl_a_mask.astype(np.uint8),
        feed_limited=feed_limited_mask.astype(np.uint8),
        dome_region=dome_mask.astype(np.uint8),
        sync_phase_error_deg=phase_err,
        dt_s=dt,
        max_following_error_x_mm=float(np.max(np.abs(fe_x))),
        max_following_error_a_deg=float(np.max(np.abs(fe_a))),
        rms_sync_error_deg=float(np.sqrt(np.mean(phase_err ** 2))),
        n_feed_limited=int(np.sum(feed_limited_mask)),
        n_backlash_x=int(np.sum(bl_x_mask)),
        n_backlash_a=int(np.sum(bl_a_mask)),
        n_sync_exceeded=int(np.sum(sync_exceeded)),
    )
