"""
core/non_geodesic_engine.py — Non-Geodezik Sarma Yolu Motoru (Koussios)
========================================================================
Dönel mandrel üzerinde sürtünme-kontrollü non-geodezik sarma yolu üretir.
Klasik Clairaut geodezik koşulundan sapma, slippage tendency parametresi
λ ile kontrol edilir.

Matematiksel Model (Koussios 2004)
-----------------------------------
Dönel mandrel z ekseninde r(z) profili için. Meridyen yay elemanı:
    ds = sqrt(1 + (dr/dz)²) dz                                          (1)

Geodezik durumda (sürtünmesiz):
    Clairaut: r·sin(α) = c  sabit                                       (2)
    Türev:    dα/dz = -(tan α / r) × (dr/dz)                            (3)

Non-geodezik durumda, sürtünme-tahrikli düzeltme:
    dα/dz = -(tan α / r) × (dr/dz)
            + (λ/r) × (1/cos α) × sqrt(1 + (dr/dz)²)                    (4)

Burada λ slippage tendency parametresi:
    λ = k_g / k_n                                                        (5)
    k_g : fiber yolunun geodezik eğriliği [1/mm]
    k_n : yüzey normal eğriliği [1/mm]

Sürtünme katsayısı μ ile kayma kısıtı:
    |λ| ≤ μ  →  fiber kaymaz (statik sürtünme yeterli)                  (6)
    |λ| > μ  →  KAYMA RİSKİ; reçete reddedilmeli                        (7)

İş mili açısı (kümülatif derece):
    dA/dz = tan α / r                                                    (8)
    A(z) = A₀ + ∫₀^z tan α(z') / r(z') dz'                              (9)

λ = 0 sınırı: denklem (4) → (3), geodezik Clairaut korunur.

Sayısal Yöntem
--------------
4. derece Runge-Kutta entegrasyonu (RK4):
    k₁ = f(α, z)
    k₂ = f(α + 0.5·dz·k₁, z + 0.5·dz)
    k₃ = f(α + 0.5·dz·k₂, z + 0.5·dz)
    k₄ = f(α + dz·k₃, z + dz)
    α_yeni = α + (dz/6) × (k₁ + 2k₂ + 2k₃ + k₄)

Yerel hata O(dz⁵); küresel hata O(dz⁴).

Kaynaklar
---------
- Koussios, S. "Filament Winding: A Unified Approach", PhD Tezi,
  Delft University, 2004.
- Wells, G.M. & McAnulty, K.F. "Computer Aided Filament Winding Using
  Non-Geodesic Trajectories", ICCM-VI, 1987. [non_geodesic_validator.py
  modülünde bağımsız doğrulama integrasyonu]
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from .geometry_engine import MandrelProfile
from .path_generator import WindingPath, WindingPathParams, WindingPoint


# ══════════════════════════════════════════════════════════════════════════════
# Parametreler ve Çıktı Tanımları
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class NonGeodesicParams:
    """
    Non-geodezik yol çözücüsü parametreleri.

    profile             : Mandrel geometrisi (z, r profili).
    alpha_start_deg     : Başlangıç sarma açısı (z_start'ta, derece).
                          Aralık: [1°, 89°]. 0° kutupsal singüler;
                          90° lift-off riski.
    lambda_slip         : Slippage tendency parametresi λ.
                          λ = 0: geodezik (Clairaut korunur)
                          0 < |λ| ≤ μ: sürtünme-kontrollü non-geodezik
                          |λ| > μ: KAYMA; reddedilir
    friction_coefficient: Fiber-reçine statik sürtünme katsayısı μ
                          (tipik: 0.20-0.40; ıslak epoksi ~ 0.30).
    integration_step_mm : RK4 z-eksen adımı. Küçük → doğru, yavaş.
                          Tipik: 0.5 mm (300 mm mandrel için 600 adım).
    z_start_mm          : Başlangıç z koordinatı (None → profile[0]).
    z_end_mm            : Bitiş z koordinatı (None → profile[-1]).
    n_circuits          : Devre sayısı (full kapsama için kullanılır).
    spindle_angle_offset_deg : Başlangıç iş mili açısı (offset).
    """
    profile: MandrelProfile
    alpha_start_deg: float
    lambda_slip: float = 0.0
    friction_coefficient: float = 0.30
    integration_step_mm: float = 0.5
    z_start_mm: Optional[float] = None
    z_end_mm: Optional[float] = None
    n_circuits: int = 1
    spindle_angle_offset_deg: float = 0.0

    def __post_init__(self) -> None:
        if not (0.5 <= self.alpha_start_deg <= 89.5):
            raise ValueError(
                f"alpha_start_deg ∈ [0.5, 89.5]: {self.alpha_start_deg}"
            )
        if not (0.0 <= self.friction_coefficient <= 1.0):
            raise ValueError(
                f"friction_coefficient ∈ [0, 1]: {self.friction_coefficient}"
            )
        if self.integration_step_mm <= 0:
            raise ValueError(
                f"integration_step_mm > 0: {self.integration_step_mm}"
            )
        if self.n_circuits < 1:
            raise ValueError(f"n_circuits ≥ 1: {self.n_circuits}")


@dataclass
class NonGeodesicReport:
    """
    Non-geodezik çözüm sonuç raporu.

    path                       : WindingPath (path_generator API'siyle uyumlu).
    z_samples_mm               : RK4 örnekleme z koordinatları.
    alpha_samples_deg          : Her örnekte sarma açısı.
    spindle_samples_deg        : Her örnekte kümülatif iş mili açısı.
    lambda_used                : Kullanılan λ değeri.
    friction_coefficient       : Kullanılan μ değeri.
    max_slip_ratio             : max(|λ|)/μ; ≤ 1 ise kayma güvenli.
    is_slip_safe               : max_slip_ratio ≤ 1.
    clairaut_drift_max         : max|r·sin(α) − c₀| / c₀ (geodezik sapma metriği).
    clairaut_drift_mean        : ortalama Clairaut sapması.
    n_steps                    : RK4 adım sayısı.
    lift_off_detected          : True → bir noktada α ≥ 89.5° (lift-off).
    warnings                   : Uyarı mesajları.
    """
    path: WindingPath
    z_samples_mm: np.ndarray
    alpha_samples_deg: np.ndarray
    spindle_samples_deg: np.ndarray
    lambda_used: float
    friction_coefficient: float
    max_slip_ratio: float
    is_slip_safe: bool
    clairaut_drift_max: float
    clairaut_drift_mean: float
    n_steps: int
    lift_off_detected: bool
    warnings: List[str] = field(default_factory=list)

    @property
    def avg_alpha_deg(self) -> float:
        return float(np.mean(self.alpha_samples_deg))

    @property
    def alpha_range_deg(self) -> Tuple[float, float]:
        return (float(np.min(self.alpha_samples_deg)),
                float(np.max(self.alpha_samples_deg)))

    def summary(self) -> str:
        a_min, a_max = self.alpha_range_deg
        return (
            f"Non-Geo: α[{a_min:.1f}°→{a_max:.1f}°] | "
            f"λ={self.lambda_used:.3f} | μ={self.friction_coefficient:.2f} | "
            f"slip={self.max_slip_ratio:.2f} | "
            f"Clairaut sapma=%{self.clairaut_drift_max*100:.2f} | "
            f"adım={self.n_steps}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Yardımcı Fonksiyonlar
# ══════════════════════════════════════════════════════════════════════════════

def _interp_r(profile: MandrelProfile, z: float) -> float:
    """Profilden z konumunda yarıçap."""
    return float(np.interp(z, profile.z_mm, profile.r_mm))


def _interp_drdz(profile: MandrelProfile, z: float) -> float:
    """
    Profilden z konumunda dr/dz.

    Yinelenen z noktalarına karşı dayanıklı: önce uniform z ızgarasına
    yeniden örnekleme yapılır, sonra gradient hesaplanır, sonra
    sorgulanan z'ye interpolasyon yapılır.

    Pratikte profile başına bir kez hesaplama yeterli olur; küçük
    önbellek için (profile, n_uniform) anahtarlı cache kullanılır.
    """
    return _profile_drdz_cached(profile)(z)


_DRDZ_CACHE: dict = {}


def _profile_drdz_cached(profile: MandrelProfile):
    """Profil için dr/dz interpolatörünü önbelleğe al."""
    key = id(profile)
    cached = _DRDZ_CACHE.get(key)
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

    _DRDZ_CACHE[key] = interp
    return interp


def _dalpha_dz(
    alpha_rad: float,
    z: float,
    profile: MandrelProfile,
    lambda_slip: float,
) -> float:
    """
    Koussios non-geodezik diferansiyel: dα/dz (rad/mm).

    Denklem (4):
        dα/dz = -(tan α / r) × (dr/dz)
                + (λ/r) × sec α × sqrt(1 + (dr/dz)²)

    Singüler bölgeler:
        r → 0       : sınırlı r_min ile koru (kutupsal singülarite)
        α → 90°     : sec α → ∞; uyarı + sınırlı
        cos α → 0   : tan α → ∞; uyarı + sınırlı
    """
    r = _interp_r(profile, z)
    if r < 1e-6:
        return 0.0
    drdz = _interp_drdz(profile, z)

    cos_a = math.cos(alpha_rad)
    sin_a = math.sin(alpha_rad)

    # cos α ≈ 0 (lift-off): denklemi sınırla
    if abs(cos_a) < 1e-4:
        return 0.0

    tan_a = sin_a / cos_a
    sec_a = 1.0 / cos_a

    geodesic_term = -(tan_a / r) * drdz
    friction_term = (lambda_slip / r) * sec_a * math.sqrt(1.0 + drdz * drdz)

    return geodesic_term + friction_term


def _rk4_step(
    alpha_rad: float,
    z: float,
    dz: float,
    profile: MandrelProfile,
    lambda_slip: float,
) -> float:
    """4. derece Runge-Kutta tek adım: α_{i+1}."""
    k1 = _dalpha_dz(alpha_rad, z, profile, lambda_slip)
    k2 = _dalpha_dz(alpha_rad + 0.5 * dz * k1, z + 0.5 * dz, profile, lambda_slip)
    k3 = _dalpha_dz(alpha_rad + 0.5 * dz * k2, z + 0.5 * dz, profile, lambda_slip)
    k4 = _dalpha_dz(alpha_rad + dz * k3, z + dz, profile, lambda_slip)
    return alpha_rad + (dz / 6.0) * (k1 + 2.0 * k2 + 2.0 * k3 + k4)


# ══════════════════════════════════════════════════════════════════════════════
# Ana Çözücü
# ══════════════════════════════════════════════════════════════════════════════

def solve_non_geodesic_path(params: NonGeodesicParams) -> NonGeodesicReport:
    """
    Non-geodezik sarma yolunu RK4 ile entegre et.

    Çıktı yolu (WindingPath) üç koordinatı içerir:
        - x_mm : eksenel z konumu (= taşıyıcı konumu)
        - a_deg: kümülatif iş mili açısı (derece)
        - feed : nominal feed (varsayılan; motion planner sonra modüle eder)

    Algoritma:
    1. dz adımıyla z_start'tan z_end'e entegrasyon
    2. Her adımda RK4 ile α güncellenir
    3. Aynı adımda iş mili açısı dA = tan(α) / r × dz entegre edilir
    4. Lift-off (α → 90°) ve kayma (|λ| > μ) tespit edilir
    5. Clairaut sapması ölçülür (geodezik sapma metriği)
    """
    profile = params.profile

    # Sınır koordinatları
    z_lo = float(profile.z_mm[0])
    z_hi = float(profile.z_mm[-1])
    z_start = params.z_start_mm if params.z_start_mm is not None else z_lo
    z_end = params.z_end_mm if params.z_end_mm is not None else z_hi

    if abs(z_end - z_start) < 1e-6:
        raise ValueError(f"z_start ≈ z_end: {z_start} ≈ {z_end}")

    # İlerleme yönü
    forward = z_end > z_start
    dz = params.integration_step_mm * (1.0 if forward else -1.0)
    n_steps = max(2, int(math.ceil(abs(z_end - z_start) / abs(dz))))
    dz = (z_end - z_start) / n_steps  # tam sayı adımlara böl

    # ── Entegrasyon dizileri ──────────────────────────────────────────────
    z_vals = np.zeros(n_steps + 1, dtype=np.float64)
    alpha_vals = np.zeros(n_steps + 1, dtype=np.float64)
    A_vals = np.zeros(n_steps + 1, dtype=np.float64)

    z_vals[0] = z_start
    alpha_vals[0] = math.radians(params.alpha_start_deg)
    A_vals[0] = math.radians(params.spindle_angle_offset_deg)

    lift_off = False
    warnings: List[str] = []

    # ── RK4 döngüsü ──────────────────────────────────────────────────────
    for i in range(n_steps):
        z_curr = z_vals[i]
        a_curr = alpha_vals[i]

        # α güncelle
        a_next = _rk4_step(a_curr, z_curr, dz, profile, params.lambda_slip)

        # Sınırla: lift-off algıla ama integrasyonu kesme
        if a_next >= math.radians(89.9):
            a_next = math.radians(89.9)
            lift_off = True
        elif a_next <= math.radians(0.1):
            a_next = math.radians(0.1)

        # İş mili açısı entegrasyonu (trapezoid)
        z_mid = z_curr + 0.5 * dz
        r_mid = _interp_r(profile, z_mid)
        a_mid = 0.5 * (a_curr + a_next)
        if r_mid > 1e-6 and abs(math.cos(a_mid)) > 1e-6:
            dA = (math.tan(a_mid) / r_mid) * dz
        else:
            dA = 0.0

        z_vals[i + 1] = z_curr + dz
        alpha_vals[i + 1] = a_next
        A_vals[i + 1] = A_vals[i] + dA

    # ── Çıktı doğrulamaları ──────────────────────────────────────────────
    # Kayma kontrolü
    if params.friction_coefficient > 1e-6:
        max_slip = abs(params.lambda_slip) / params.friction_coefficient
    else:
        max_slip = float('inf') if abs(params.lambda_slip) > 0 else 0.0
    is_slip_safe = max_slip <= 1.0
    if not is_slip_safe:
        warnings.append(
            f"KAYMA RİSKİ: |λ|/μ = {max_slip:.2f} > 1.0"
        )

    if lift_off:
        warnings.append(
            f"Lift-off: α 90°'ye ulaştı; fiber kalıp yüzeyinden kalkabilir"
        )

    # Clairaut sapması (geodezik referansa karşı)
    r_samples = np.interp(z_vals, profile.z_mm, profile.r_mm)
    sin_a_samples = np.sin(alpha_vals)
    clairaut_product = r_samples * sin_a_samples
    c0 = float(clairaut_product[0])
    if abs(c0) > 1e-9:
        clairaut_drift = np.abs(clairaut_product - c0) / abs(c0)
        clairaut_drift_max = float(np.max(clairaut_drift))
        clairaut_drift_mean = float(np.mean(clairaut_drift))
    else:
        clairaut_drift_max = 0.0
        clairaut_drift_mean = 0.0

    # ── WindingPath oluştur ───────────────────────────────────────────────
    points: List[WindingPoint] = []
    fiber_len_cum = 0.0
    for i in range(n_steps + 1):
        if i > 0:
            # Fiber yay uzunluğu: ds = sqrt(dz² + (r·dA)² + dr²)
            dr = float(np.interp(z_vals[i], profile.z_mm, profile.r_mm)) - \
                 float(np.interp(z_vals[i - 1], profile.z_mm, profile.r_mm))
            dA_seg = A_vals[i] - A_vals[i - 1]
            r_seg = 0.5 * (r_samples[i] + r_samples[i - 1])
            ds_seg = math.sqrt(
                (z_vals[i] - z_vals[i - 1]) ** 2 +
                (r_seg * dA_seg) ** 2 +
                dr ** 2
            )
            fiber_len_cum += ds_seg

        points.append(WindingPoint(
            x_mm=float(z_vals[i]),
            a_deg=math.degrees(A_vals[i]),
            feed=80.0,
            z_fiber=fiber_len_cum,
            layer=0,
            circuit=0,
        ))

    # path_generator.WindingPath sözleşmesi
    # WindingPathParams için stub (geriye uyumluluk)
    path_params = WindingPathParams(
        profile=profile,
        alpha_deg=params.alpha_start_deg,
        n_layers=1,
        winding_strategy="non_geodesic",
    )
    path = WindingPath(
        points=points,
        n_circuits=params.n_circuits,
        n_layers=1,
        total_fiber_length_mm=fiber_len_cum,
        estimated_time_s=fiber_len_cum / 80.0,
        coverage_pct=100.0,
        clairaut_c=c0,
        params=path_params,
    )

    return NonGeodesicReport(
        path=path,
        z_samples_mm=z_vals,
        alpha_samples_deg=np.degrees(alpha_vals),
        spindle_samples_deg=np.degrees(A_vals),
        lambda_used=params.lambda_slip,
        friction_coefficient=params.friction_coefficient,
        max_slip_ratio=max_slip,
        is_slip_safe=is_slip_safe,
        clairaut_drift_max=clairaut_drift_max,
        clairaut_drift_mean=clairaut_drift_mean,
        n_steps=n_steps,
        lift_off_detected=lift_off,
        warnings=warnings,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Yardımcı: Geodezik → non-geodezik karşılaştırma
# ══════════════════════════════════════════════════════════════════════════════

def compare_geodesic_vs_nongeodesic(
    profile: MandrelProfile,
    alpha_start_deg: float,
    lambda_slip: float,
    friction_coefficient: float = 0.30,
    integration_step_mm: float = 0.5,
) -> Tuple[NonGeodesicReport, NonGeodesicReport]:
    """
    Aynı başlangıç koşullarıyla geodezik (λ=0) ve non-geodezik çözüm üret.

    Döner: (geodesic_report, non_geodesic_report)

    Test/debug için: geodezik çözüm Clairaut sapması ~ 0 vermeli;
    non-geodezik çözüm |λ|/μ oranıyla orantılı sapma göstermeli.
    """
    base = NonGeodesicParams(
        profile=profile,
        alpha_start_deg=alpha_start_deg,
        lambda_slip=0.0,
        friction_coefficient=friction_coefficient,
        integration_step_mm=integration_step_mm,
    )
    geodesic = solve_non_geodesic_path(base)

    ng_params = NonGeodesicParams(
        profile=profile,
        alpha_start_deg=alpha_start_deg,
        lambda_slip=lambda_slip,
        friction_coefficient=friction_coefficient,
        integration_step_mm=integration_step_mm,
    )
    non_geodesic = solve_non_geodesic_path(ng_params)

    return geodesic, non_geodesic
