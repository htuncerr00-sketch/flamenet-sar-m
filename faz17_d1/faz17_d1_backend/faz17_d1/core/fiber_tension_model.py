"""
core/fiber_tension_model.py — Yol Boyunca Fiber Gerilim Profili
================================================================
Faz: ENDÜSTRİYEL ÜRETİM DOĞRULUĞU — Görev 2

`fiber_tension.py` (NOKTA fiziği: basınç, kayma, sıkıştırma) üzerine, bir sarma
YOLU boyunca tahmini gerilim PROFİLİ üretir. Tek noktada değil, tüm yol boyunca
gerilim evrimini ve kararsız bölgeleri verir.

Modellenen etkiler
-------------------
1. Payout drag (capstan): Fiber, payout gözünden/kılavuzlarından geçerken
   sürtünmeyle gerilim kazanır:  T_eye = T_set · exp(μ_eye · φ_wrap).
2. Eğrilik kaynaklı amplifikasyon (dome): Fiber, eğri bir meridyen üzerinde
   yüzeye sürtünürken kapstan (belt-friction) etkisiyle gerilim birikir:
       T(s) = T_set · exp(μ_yüzey · Φ(s))
   Φ(s) = pass başlangıcından itibaren meridyen teğetinin kümülatif dönmesi.
   Silindirde meridyen düz → Φ=0 → amplifikasyon yok (doğru).
   Kubbede meridyen eğri → Φ>0 → gerilim amplifikasyonu (doğru).
3. Sürtünme etkileri: μ_yüzey hem kapstan amplifikasyonunu hem de kayma eşiğini
   yönetir (slip = tan(α)/μ).
4. Göz-temas mesafesi etkisi: serbest fiber uzunluğu boyunca doğrusal drag
   (yerçekimi/hava sürüklemesi — ikincil terim, varsayılan kapalı).

Üretilen çıktılar
-----------------
- Tahmini gerilim profili (her yol noktasında T_N + temas basıncı)
- Kararsız gerilim bölgeleri (temas basıncı < p_min veya amplifikasyon doygunluğu)
- Minimum güvenli gerilim tahmini (setpoint için alt sınır)

Mühendislik varsayımları
------------------------
- Kapstan amplifikasyonu pass başına sıfırlanır (payout gerginleştirici her pass'te
  setpoint'i yeniden kurar — yarı-statik yaklaşım).
- Amplifikasyon `max_amplification` ile sınırlanır; bu sınıra ulaşmak fiziksel
  gerilim kaçışı (runaway) UYARISIDIR, gizlenmez.
- Geodezik yolda yanal kayma teorik olarak sıfırdır; yine de tan(α)/μ kayma
  marjı raporlanır (geodezik olmayan sapmalar için).

Başarısızlık modu analizi
-------------------------
- Düşük temas basıncı → fiber gevşer, köprüleme (bridging), boşluk.
- Aşırı amplifikasyon → fiber kopması, reçine sıkışması, eşit olmayan sıkıştırma.
- Yüksek kayma marjı → fiber kayması, açı kayması, kuru bölge.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from .fiber_tension import FiberTensionModel
from .geometry_engine import MandrelProfile
from .geodesic_validator import Zone, _compute_local_alpha, _merge_zones
from .path_generator import WindingPath, WindingPoint


@dataclass
class TensionModelConfig:
    """Gerilim profili model parametreleri."""
    set_tension_N: float = 50.0           # Payout gerginleştirici setpoint'i
    eye_friction_coeff: float = 0.15      # Payout gözü/kılavuz kapstan μ
    surface_friction_coeff: float = 0.30  # Fiber-mandrel μ (kapstan + kayma)
    band_width_mm: float = 6.0
    max_amplification: float = 3.0        # Kapstan amplifikasyon tavanı (runaway eşiği)
    drag_per_mm_N: float = 0.0            # Serbest uzunluk başına drag (ikincil)
    eye_standoff_mm: float = 150.0        # Göz-yüzey radyal mesafesi (serbest uzunluk için)

    def __post_init__(self) -> None:
        if self.set_tension_N <= 0:
            raise ValueError("set_tension_N > 0 olmalı")
        if self.max_amplification < 1.0:
            raise ValueError("max_amplification >= 1 olmalı")
        if self.band_width_mm <= 0:
            raise ValueError("band_width_mm > 0 olmalı")


@dataclass
class TensionPoint:
    """Tek bir yol noktasındaki gerilim durumu."""
    z_mm: float
    r_mm: float
    alpha_deg: float
    tension_N: float
    contact_pressure_MPa: float
    amplification: float          # T / T_set
    slip_margin: float            # tan(α)/μ (>1 → kayma riski)
    is_unstable: bool
    reason: str = ""


@dataclass
class TensionProfileReport:
    """Yol boyunca gerilim profili analiz raporu."""
    points: List[TensionPoint]
    set_tension_N: float
    min_tension_N: float
    max_tension_N: float
    mean_tension_N: float
    min_safe_set_tension_N: float       # Setpoint için tahmini alt sınır
    max_contact_pressure_MPa: float
    max_amplification: float
    n_unstable: int
    unstable_zones: List[Zone]
    is_stable: bool
    warnings: List[str]

    def summary(self) -> str:
        status = "KARARLI ✓" if self.is_stable else "KARARSIZ ✗"
        return (
            f"GerilimProfili: {status} | T_set={self.set_tension_N:.1f}N | "
            f"T∈[{self.min_tension_N:.1f},{self.max_tension_N:.1f}]N "
            f"(ort {self.mean_tension_N:.1f}) | "
            f"maks_amp={self.max_amplification:.2f}× | "
            f"maks_basınç={self.max_contact_pressure_MPa:.3f}MPa | "
            f"min_güvenli_set={self.min_safe_set_tension_N:.1f}N | "
            f"kararsız={self.n_unstable} ({len(self.unstable_zones)} bölge)"
        )


def estimate_tension_profile(
    path: WindingPath,
    profile: MandrelProfile,
    base: Optional[FiberTensionModel] = None,
    config: Optional[TensionModelConfig] = None,
) -> TensionProfileReport:
    """
    Sarma yolu boyunca tahmini fiber gerilim profilini hesapla.

    Parametreler
    ----------
    path    : Değerlendirilecek sarma yolu.
    profile : Mandrel geometrisi.
    base    : Nokta-fiziği gerilim modeli (None ise config'ten türetilir).
    config  : Gerilim profili model yapılandırması.

    Döner
    -----
    TensionProfileReport — gerilim profili + kararsız bölgeler + min güvenli gerilim.
    """
    if config is None:
        config = TensionModelConfig()
    if base is None:
        base = FiberTensionModel(
            tension_N=config.set_tension_N,
            friction_coeff=config.surface_friction_coeff,
        )

    pts = path.points
    w = config.band_width_mm
    mu_surf = config.surface_friction_coeff
    mu_eye = config.eye_friction_coeff

    if len(pts) < 3:
        return TensionProfileReport(
            points=[], set_tension_N=config.set_tension_N,
            min_tension_N=0.0, max_tension_N=0.0, mean_tension_N=0.0,
            min_safe_set_tension_N=0.0, max_contact_pressure_MPa=0.0,
            max_amplification=1.0, n_unstable=0, unstable_zones=[],
            is_stable=False, warnings=["Yeterli yol noktası yok (< 3)"],
        )

    z_arr = np.array([p.x_mm for p in pts], dtype=np.float64)
    r_arr = np.interp(z_arr, profile.z_mm, profile.r_mm)
    alpha_arr = _compute_local_alpha(pts, profile)
    circuit_arr = np.array([p.circuit for p in pts], dtype=np.int64)
    layer_arr = np.array([p.layer for p in pts], dtype=np.int64)

    # ── Meridyen teğet dönmesi (kapstan wrap açısı) ──────────────────────────
    # phi_m = atan2(dr, dz); pass içinde kümülatif |Δphi_m| → wrap açısı Φ.
    n = len(pts)
    phi_m = np.zeros(n)
    if n > 1:
        dz = np.gradient(z_arr)
        dr = np.gradient(r_arr)
        phi_m = np.arctan2(dr, dz)

    # Payout gözü kapstan wrap açısı: fiber radyal yönden α kadar saparak çıkar.
    # φ_eye ≈ payout açısının radyan karşılığı (küçük açı yaklaşımı dışında da geçerli).
    # Burada α_local'i kullanırız: yüksek açı → gözde daha çok wrap.
    phi_eye = np.radians(np.clip(alpha_arr, 0.0, 89.9))
    eye_factor = np.exp(mu_eye * phi_eye)

    tension = np.zeros(n)
    amplification = np.zeros(n)

    # Pass (devre+kat) başına kümülatif wrap ile gerilim biriktir
    cum_wrap = 0.0
    prev_key = (layer_arr[0], circuit_arr[0])
    for i in range(n):
        key = (layer_arr[i], circuit_arr[i])
        if key != prev_key:
            cum_wrap = 0.0
            prev_key = key
        if i > 0 and key == (layer_arr[i - 1], circuit_arr[i - 1]):
            cum_wrap += abs(phi_m[i] - phi_m[i - 1])

        surf_amp = math.exp(mu_surf * cum_wrap)
        amp = min(config.max_amplification, eye_factor[i] * surf_amp)
        amplification[i] = amp

        # Serbest uzunluk drag (ikincil): göz standoff + lead yaklaşık
        free_len = config.eye_standoff_mm / max(math.sin(math.radians(
            max(alpha_arr[i], 1.0))), 1e-3)
        drag = config.drag_per_mm_N * free_len

        tension[i] = config.set_tension_N * amp + drag

    # ── Temas basıncı ve kayma marjı ─────────────────────────────────────────
    contact_p = np.where(r_arr > 0, tension / (r_arr * w), 0.0)
    slip_margin = np.tan(np.radians(np.clip(alpha_arr, 0.0, 89.9))) / max(mu_surf, 1e-3)

    # ── Kararsızlık: düşük temas basıncı veya amplifikasyon doygunluğu ───────
    p_min = base.min_contact_pressure_MPa
    loose = contact_p < p_min
    amp_sat = amplification >= config.max_amplification - 1e-9
    unstable = loose | amp_sat

    tps: List[TensionPoint] = []
    for i in range(n):
        reason = ""
        if loose[i]:
            reason = f"temas basıncı {contact_p[i]:.3f}<{p_min:.3f}MPa (gevşek)"
        elif amp_sat[i]:
            reason = f"amplifikasyon doygunluğu {amplification[i]:.2f}× (gerilim kaçışı)"
        tps.append(TensionPoint(
            z_mm=float(z_arr[i]), r_mm=float(r_arr[i]), alpha_deg=float(alpha_arr[i]),
            tension_N=float(tension[i]), contact_pressure_MPa=float(contact_p[i]),
            amplification=float(amplification[i]), slip_margin=float(slip_margin[i]),
            is_unstable=bool(unstable[i]), reason=reason,
        ))

    # ── Kararsız bölgeler ─────────────────────────────────────────────────────
    sev = np.where(loose, np.clip((p_min - contact_p) / max(p_min, 1e-9), 0.0, 1.0),
                   np.where(amp_sat, 1.0, 0.0))
    unstable_zones = _merge_zones(unstable, z_arr, sev, "Kararsız gerilim bölgesi")

    # ── Minimum güvenli setpoint tahmini ─────────────────────────────────────
    # En gevşek nokta amplifikasyonsuz (setpoint) noktadır; en büyük r en kritiktir.
    # p_min ≤ T_set / (r_max · w)  →  T_set ≥ p_min · r_max · w
    r_max = float(np.max(r_arr))
    min_safe_set = p_min * r_max * w

    warnings: List[str] = []
    if amp_sat.any():
        warnings.append(f"{int(amp_sat.sum())} noktada amplifikasyon tavanına ulaşıldı (gerilim kaçışı riski)")
    if loose.any():
        warnings.append(f"{int(loose.sum())} noktada temas basıncı yetersiz (gevşek fiber)")
    if config.set_tension_N < min_safe_set:
        warnings.append(
            f"Setpoint {config.set_tension_N:.1f}N < min güvenli {min_safe_set:.1f}N "
            f"(r_max={r_max:.1f}mm)")
    if (slip_margin > 1.0).any():
        warnings.append(f"{int((slip_margin>1.0).sum())} noktada kayma marjı > 1 (geodezik olmayan sapmada kayar)")

    return TensionProfileReport(
        points=tps,
        set_tension_N=config.set_tension_N,
        min_tension_N=float(np.min(tension)),
        max_tension_N=float(np.max(tension)),
        mean_tension_N=float(np.mean(tension)),
        min_safe_set_tension_N=min_safe_set,
        max_contact_pressure_MPa=float(np.max(contact_p)),
        max_amplification=float(np.max(amplification)),
        n_unstable=int(unstable.sum()),
        unstable_zones=unstable_zones,
        is_stable=(not loose.any()) and (not amp_sat.any()),
        warnings=warnings,
    )
