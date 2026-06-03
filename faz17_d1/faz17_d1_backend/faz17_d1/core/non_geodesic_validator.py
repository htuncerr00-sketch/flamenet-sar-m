"""
core/non_geodesic_validator.py — Wells & McAnulty Bağımsız Doğrulayıcı
========================================================================
Koussios formülasyonunun (non_geodesic_engine.py) bağımsız doğrulaması.
Wells & McAnulty (1987) meridyen yay-uzunluğu parametreli formu kullanır.

Amaç: aynı fizik için iki bağımsız sayısal yöntem; sonuçlar uyumluysa
çözüm güvenilir.

Matematiksel Model (Wells & McAnulty 1987)
-------------------------------------------
Dönel mandrel yüzeyi için meridyen yay parametresi s ile:
    ds/dz = sqrt(1 + (dr/dz)²)                                          (W1)
    dz/ds = 1 / sqrt(1 + (dr/dz)²) = cos(β)                             (W2)
    dr/ds = (dr/dz) / sqrt(1 + (dr/dz)²) = sin(β)                       (W3)

burada β = meridyen eğim açısı (dz/dr = tan β).

Sarma açısı α, meridyen tanjantına göre tanımlı. Koussios z-formundan
(denklem 4) zincir kuralı ile türetilir:
    dα/ds = dα/dz × dz/ds  ,  dz/ds = cos β  ,  dr/ds = sin β

Sonuç Wells formu:
    dα/ds = (λ − sin α × sin β) / (r × cos α)                           (W4)

Denklem (W4) Koussios denklemi (4) ile cebirsel olarak özdeştir; farklı
parametrelemede entegre edilir (ds, dz değil). Bağımsız sayısal yol,
RK4 yuvarlama hataları + parametreleme türevinden farklı yörünge örnekler
üretir; aynı son durumu vermesi gerekir.

Eşdeğerlik Kontrolü
-------------------
İki çözücü farklı parametre uzayında entegre eder:
- Koussios: z-eksen üzerinde dα/dz
- Wells: meridyen yay s üzerinde dα/ds

Çözümler aynı fiziği temsil ettiği için z-konumuna geri projeksiyonla
karşılaştırılabilir:
    α_K(z) ≈ α_W(z)   (RK4 sayısal hata payı içinde)

Doğrulama kriterleri:
    max|α_K(z) - α_W(z)| < TOLERANS  (varsayılan: 0.5°)
    max|A_K(z) - A_W(z)| < TOLERANS  (varsayılan: 2°)

Bu testler geçerse Koussios çözücüsü güvenilir kabul edilir.

Kaynaklar
---------
- Wells, G.M. & McAnulty, K.F. "Computer Aided Filament Winding Using
  Non-Geodesic Trajectories", ICCM-VI, 1987.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from .geometry_engine import MandrelProfile
from .non_geodesic_engine import (
    NonGeodesicParams,
    NonGeodesicReport,
    solve_non_geodesic_path,
)


# ══════════════════════════════════════════════════════════════════════════════
# Wells Çözücüsü (Bağımsız Doğrulayıcı)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class WellsReport:
    """Wells yay-uzunluğu parametreli çözücüsünün sonucu."""
    s_samples_mm: np.ndarray         # yay uzunluğu örnekleri
    z_samples_mm: np.ndarray         # z konumu (s'den projeksiyon)
    alpha_samples_deg: np.ndarray
    spindle_samples_deg: np.ndarray
    lambda_used: float
    n_steps: int


def _interp_drdz(profile: MandrelProfile, z: float) -> float:
    """
    Profilden z'de dr/dz (uniform yeniden örnekleme + gradient).

    Yinelenen z noktalarına dayanıklı.
    """
    return _profile_drdz_cached(profile)(z)


_DRDZ_CACHE_WELLS: dict = {}


def _profile_drdz_cached(profile: MandrelProfile):
    """Profil için dr/dz interpolatörü önbelleği."""
    key = id(profile)
    cached = _DRDZ_CACHE_WELLS.get(key)
    if cached is not None:
        return cached

    z_arr = profile.z_mm
    r_arr = profile.r_mm
    n_uni = max(len(z_arr), 200)
    z_uni = np.linspace(float(z_arr[0]), float(z_arr[-1]), n_uni)
    r_uni = np.interp(z_uni, z_arr, r_arr)
    dr_dz = np.gradient(r_uni, z_uni)

    def interp(z: float) -> float:
        return float(np.interp(z, z_uni, dr_dz))

    _DRDZ_CACHE_WELLS[key] = interp
    return interp


def _trapezoid(y, x):
    """NumPy 1.x/2.x uyumlu trapez integrali."""
    if hasattr(np, "trapezoid"):
        return float(np.trapezoid(y, x))
    return float(np.trapz(y, x))


def _dalpha_ds_wells(
    alpha_rad: float,
    z: float,
    profile: MandrelProfile,
    lambda_slip: float,
) -> Tuple[float, float, float]:
    """
    Wells diferansiyeli: dα/ds (rad/mm), dz/ds, dr/ds.

    Denklem (W4):
        dα/ds = (λ − sin α × sin β) / (r × cos α)

    β meridyen eğim açısı:
        sin β = dr/ds = (dr/dz) / sqrt(1 + (dr/dz)²)
        cos β = dz/ds = 1 / sqrt(1 + (dr/dz)²)
    """
    r = float(np.interp(z, profile.z_mm, profile.r_mm))
    if r < 1e-6:
        return 0.0, 1.0, 0.0

    drdz = _interp_drdz(profile, z)
    norm = math.sqrt(1.0 + drdz * drdz)
    sin_beta = drdz / norm
    cos_beta = 1.0 / norm

    sin_a = math.sin(alpha_rad)
    cos_a = math.cos(alpha_rad)

    # cos α ≈ 0 (lift-off): sınırla
    if abs(cos_a) < 1e-4:
        return 0.0, cos_beta, sin_beta

    dalpha_ds = (lambda_slip - sin_a * sin_beta) / (r * cos_a)
    return dalpha_ds, cos_beta, sin_beta


def _rk4_step_wells(
    alpha_rad: float,
    z: float,
    ds: float,
    profile: MandrelProfile,
    lambda_slip: float,
) -> Tuple[float, float]:
    """
    RK4 tek adım: yay uzunluğu üzerinde.

    Döner: (alpha_yeni, z_yeni)
    """
    # k1: başlangıç noktası
    k1_a, k1_dzds, _ = _dalpha_ds_wells(alpha_rad, z, profile, lambda_slip)
    # k2: yarı adım
    a_2 = alpha_rad + 0.5 * ds * k1_a
    z_2 = z + 0.5 * ds * k1_dzds
    k2_a, k2_dzds, _ = _dalpha_ds_wells(a_2, z_2, profile, lambda_slip)
    # k3: ikinci yarı adım
    a_3 = alpha_rad + 0.5 * ds * k2_a
    z_3 = z + 0.5 * ds * k2_dzds
    k3_a, k3_dzds, _ = _dalpha_ds_wells(a_3, z_3, profile, lambda_slip)
    # k4: tam adım
    a_4 = alpha_rad + ds * k3_a
    z_4 = z + ds * k3_dzds
    k4_a, k4_dzds, _ = _dalpha_ds_wells(a_4, z_4, profile, lambda_slip)

    alpha_new = alpha_rad + (ds / 6.0) * (k1_a + 2 * k2_a + 2 * k3_a + k4_a)
    z_new = z + (ds / 6.0) * (k1_dzds + 2 * k2_dzds + 2 * k3_dzds + k4_dzds)
    return alpha_new, z_new


def solve_wells_path(params: NonGeodesicParams) -> WellsReport:
    """
    Wells & McAnulty yay-uzunluğu parametreli non-geodezik çözücüsü.

    Koussios çözücüsünden bağımsız bir matematiksel yöntem; aynı sonucu
    vermeli. Test/doğrulama için kullanılır.
    """
    profile = params.profile
    z_lo = float(profile.z_mm[0])
    z_hi = float(profile.z_mm[-1])
    z_start = params.z_start_mm if params.z_start_mm is not None else z_lo
    z_end = params.z_end_mm if params.z_end_mm is not None else z_hi

    # Yay uzunluğu adımı: dz_step × sqrt(1 + (dr/dz)²) en kötü duruma
    # benzer. Basitleştirme: ds ≈ dz (silindir için kesin; kubbe için
    # küçük hata)
    forward = z_end > z_start
    ds = params.integration_step_mm * (1.0 if forward else -1.0)

    # Toplam yay uzunluğu yaklaşımı
    z_check = np.linspace(z_start, z_end, 200)
    dr_dz_check = np.gradient(np.interp(z_check, profile.z_mm, profile.r_mm),
                                z_check)
    s_total = _trapezoid(np.sqrt(1.0 + dr_dz_check ** 2), z_check)

    n_steps = max(2, int(math.ceil(abs(s_total) / abs(ds))))
    ds = (s_total if forward else -s_total) / n_steps

    s_vals = np.zeros(n_steps + 1)
    z_vals = np.zeros(n_steps + 1)
    alpha_vals = np.zeros(n_steps + 1)
    A_vals = np.zeros(n_steps + 1)

    z_vals[0] = z_start
    alpha_vals[0] = math.radians(params.alpha_start_deg)
    A_vals[0] = math.radians(params.spindle_angle_offset_deg)

    for i in range(n_steps):
        a_curr = alpha_vals[i]
        z_curr = z_vals[i]

        a_next, z_next = _rk4_step_wells(
            a_curr, z_curr, ds, profile, params.lambda_slip
        )

        # Sınırla
        a_next = max(min(a_next, math.radians(89.9)), math.radians(0.1))

        # Profil sınırlarında durdur
        if forward and z_next > z_end:
            z_next = z_end
        if not forward and z_next < z_end:
            z_next = z_end

        # İş mili açısı: dA/ds = tan α × dz/ds / r = tan α × cos β / r
        z_mid = 0.5 * (z_curr + z_next)
        r_mid = float(np.interp(z_mid, profile.z_mm, profile.r_mm))
        drdz_mid = _interp_drdz(profile, z_mid)
        cos_beta_mid = 1.0 / math.sqrt(1.0 + drdz_mid * drdz_mid)
        a_mid = 0.5 * (a_curr + a_next)
        if r_mid > 1e-6 and abs(math.cos(a_mid)) > 1e-6:
            dA = (math.tan(a_mid) * cos_beta_mid / r_mid) * ds
        else:
            dA = 0.0

        s_vals[i + 1] = s_vals[i] + ds
        z_vals[i + 1] = z_next
        alpha_vals[i + 1] = a_next
        A_vals[i + 1] = A_vals[i] + dA

    return WellsReport(
        s_samples_mm=s_vals,
        z_samples_mm=z_vals,
        alpha_samples_deg=np.degrees(alpha_vals),
        spindle_samples_deg=np.degrees(A_vals),
        lambda_used=params.lambda_slip,
        n_steps=n_steps,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Karşılaştırma Doğrulayıcısı
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class CrossValidationReport:
    """
    Koussios vs Wells karşılaştırma raporu.

    max_alpha_diff_deg     : İki çözücü arası max α farkı (derece).
    max_spindle_diff_deg   : Max iş mili açısı farkı (derece).
    rms_alpha_diff_deg     : RMS α farkı.
    rms_spindle_diff_deg   : RMS A farkı.
    is_consistent          : Toleranslar dahilinde mi?
    n_comparison_points    : Karşılaştırma noktası sayısı.
    alpha_tolerance_deg    : Kullanılan α toleransı.
    spindle_tolerance_deg  : Kullanılan A toleransı.
    """
    max_alpha_diff_deg: float
    max_spindle_diff_deg: float
    rms_alpha_diff_deg: float
    rms_spindle_diff_deg: float
    is_consistent: bool
    n_comparison_points: int
    alpha_tolerance_deg: float
    spindle_tolerance_deg: float
    warnings: List[str] = field(default_factory=list)

    def summary(self) -> str:
        verdict = "✓ TUTARLI" if self.is_consistent else "✗ TUTARSIZ"
        return (
            f"Koussios↔Wells: {verdict} | "
            f"Δα_max={self.max_alpha_diff_deg:.3f}° "
            f"(tol={self.alpha_tolerance_deg:.2f}°) | "
            f"ΔA_max={self.max_spindle_diff_deg:.3f}° "
            f"(tol={self.spindle_tolerance_deg:.2f}°)"
        )


def cross_validate(
    params: NonGeodesicParams,
    alpha_tolerance_deg: float = 0.5,
    spindle_tolerance_deg: float = 2.0,
    koussios_report: Optional[NonGeodesicReport] = None,
    wells_report: Optional[WellsReport] = None,
) -> CrossValidationReport:
    """
    Koussios ve Wells çözümlerini z koordinatına göre karşılaştır.

    Algoritma:
    1. Koussios: dα/dz entegrasyon → α_K(z)
    2. Wells: dα/ds entegrasyon → α_W(s) → z(s) projeksiyonu ile α_W(z)
    3. Ortak z grid'inde fark hesabı
    4. Tolerans kontrolü

    Geodezik durum (λ=0): her iki çözüm Clairaut'a yakın olmalı; farklar
    yalnızca sayısal hatadan kaynaklanır (RK4 O(dz⁴)).

    Non-geodezik durum (λ>0): her iki çözüm aynı fiziksel yolu izlemeli;
    farklar artık RK4 hatası + farklı parametreleme türevinden.
    """
    if koussios_report is None:
        koussios_report = solve_non_geodesic_path(params)
    if wells_report is None:
        wells_report = solve_wells_path(params)

    # Ortak z aralığında karşılaştırma
    z_min = max(float(koussios_report.z_samples_mm.min()),
                float(wells_report.z_samples_mm.min()))
    z_max = min(float(koussios_report.z_samples_mm.max()),
                float(wells_report.z_samples_mm.max()))
    n_compare = 50
    z_grid = np.linspace(z_min, z_max, n_compare)

    # Koussios: α(z) ve A(z) direkt
    alpha_K = np.interp(z_grid, koussios_report.z_samples_mm,
                          koussios_report.alpha_samples_deg)
    A_K = np.interp(z_grid, koussios_report.z_samples_mm,
                      koussios_report.spindle_samples_deg)

    # Wells: z(s) monoton; z üzerinden α ve A interpolasyon
    z_W = wells_report.z_samples_mm
    # Wells'in z dizisi monoton değilse (kubbe dönüşü gibi) sıralı uniqueme
    sort_idx = np.argsort(z_W)
    z_W_sorted = z_W[sort_idx]
    a_W_sorted = wells_report.alpha_samples_deg[sort_idx]
    A_W_sorted = wells_report.spindle_samples_deg[sort_idx]
    # Duplicate z'leri ortala
    _, unique_idx = np.unique(z_W_sorted, return_index=True)
    z_W_u = z_W_sorted[unique_idx]
    a_W_u = a_W_sorted[unique_idx]
    A_W_u = A_W_sorted[unique_idx]

    alpha_W = np.interp(z_grid, z_W_u, a_W_u)
    A_W = np.interp(z_grid, z_W_u, A_W_u)

    # Farklar
    alpha_diff = np.abs(alpha_K - alpha_W)
    A_diff = np.abs(A_K - A_W)

    max_a = float(np.max(alpha_diff))
    max_A = float(np.max(A_diff))
    rms_a = float(np.sqrt(np.mean(alpha_diff ** 2)))
    rms_A = float(np.sqrt(np.mean(A_diff ** 2)))

    is_consistent = (max_a <= alpha_tolerance_deg and
                     max_A <= spindle_tolerance_deg)

    warnings: List[str] = []
    if max_a > alpha_tolerance_deg:
        warnings.append(
            f"α farkı toleransı aştı: {max_a:.3f}° > {alpha_tolerance_deg:.2f}°"
        )
    if max_A > spindle_tolerance_deg:
        warnings.append(
            f"İş mili açısı farkı toleransı aştı: "
            f"{max_A:.3f}° > {spindle_tolerance_deg:.2f}°"
        )

    return CrossValidationReport(
        max_alpha_diff_deg=max_a,
        max_spindle_diff_deg=max_A,
        rms_alpha_diff_deg=rms_a,
        rms_spindle_diff_deg=rms_A,
        is_consistent=is_consistent,
        n_comparison_points=n_compare,
        alpha_tolerance_deg=alpha_tolerance_deg,
        spindle_tolerance_deg=spindle_tolerance_deg,
        warnings=warnings,
    )
