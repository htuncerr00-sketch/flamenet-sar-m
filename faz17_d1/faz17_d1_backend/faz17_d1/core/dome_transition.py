"""
core/dome_transition.py — Kubbe Geçiş Fiziği
=============================================
Faz: ENDÜSTRİYEL ÜRETİM DOĞRULUĞU — Görev 3

Mevcut sistem silindir-odaklıydı. Bu modül, dönel kubbe (dome) bölgelerinde
geodezik sarma fiziğini ekler:

- Teğet süreklilik (silindir↔kubbe meridyen geçişi)
- Kutupsal (polar) sarma geçişleri ve dönüş (turnaround) bölgesi
- Geodezik kubbe traversali (Clairaut: c = r·sin α sabit)
- Kubbede kayma riski tahmini (geodezik olmayan sapma + sürtünme)
- Eğrilik geçişlerinde bant genişliği distorsiyonu

Analitik temel
--------------
Clairaut sabiti           : c = r(z) · sin(α(z))  (geodezik boyunca sabit)
Kutup yarıçapı            : r_polar = c  (α → 90°, sarma dönüş yapar)
Lokal sarma açısı         : α(z) = arcsin(c / r(z))     (r ≥ c iken tanımlı)
Meridyen teğet açısı      : φ_m(z) = atan2(dr/dz, 1)    (eksenden sapma)
Meridyen eğriliği         : κ_m = |r''| / (1+r'²)^{3/2}
Bant eksenel projeksiyonu : W_axial = W / sin(α)        (α↑ → daralır)
Kayma marjı (geodezik dışı): λ = tan(α) / μ

Teğet süreklilik kriteri
------------------------
İki bitişik segment arasında meridyen teğet açısının değişimi küçük olmalıdır;
ani φ_m sıçraması (köşe) fiber köprülemesi ve bant kırışması üretir.

Mühendislik varsayımları
------------------------
- Profil dönel simetriktir; analiz (r, z) meridyen düzleminde yapılır.
- Kutupsal dönüş, lokal yarıçap Clairaut sabitine yaklaştığında (r → c) oluşur;
  bu noktada α → 90° ve dθ/dz → ∞ (geodezik denklem tekilliği).
- Bant distorsiyonu, eksenel projeksiyon W/sin(α)'nın silindire göre oranıdır.

Başarısızlık modu analizi
-------------------------
- Teğet süreksizliği → fiber köprüleme, kubbe ucunda boşluk/yığılma.
- r < c → fiber kalkışı (lift-off): yol fiziksel olarak imkânsız.
- Yüksek kayma marjı kubbede → fiber kayması, açı kontrolü kaybı.
- Aşırı bant daralması → kutupta aşırı yığılma, reçine fazlası.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from .geometry_engine import MandrelProfile


@dataclass
class DomeRegion:
    """Tespit edilen kubbe (eğri meridyen) bölgesi."""
    z_start_mm: float
    z_end_mm: float
    r_start_mm: float
    r_end_mm: float
    side: str           # "sol" | "sağ"
    max_curvature_1_mm: float


@dataclass
class DomeTransitionReport:
    """Kubbe geçiş fiziği analiz raporu."""
    clairaut_c_mm: float
    polar_radius_mm: float              # = c (kutupsal dönüş yarıçapı)
    dome_regions: List[DomeRegion]

    # Teğet süreklilik
    max_tangent_jump_deg: float         # En büyük meridyen teğet sıçraması
    tangent_continuous: bool

    # Geodezik traversal edilebilirlik
    min_radius_mm: float
    liftoff_risk: bool                  # min_r < c → kalkış
    turnaround_z_mm: List[float]        # r ≈ c kutupsal dönüş konumları

    # Kayma riski
    max_slip_margin: float              # maks tan(α)/μ kubbe bölgesinde
    slip_risk_zones: int

    # Bant distorsiyonu
    max_bandwidth_distortion: float     # maks (W_axial / W_axial_cyl)

    is_traversable: bool
    critical_issues: List[str]
    warnings: List[str]

    def summary(self) -> str:
        status = "TRAVERSAL EDİLEBİLİR ✓" if self.is_traversable else "TRAVERSAL EDİLEMEZ ✗"
        return (
            f"KubbeGeçişi: {status} | c={self.clairaut_c_mm:.2f}mm "
            f"(kutup r={self.polar_radius_mm:.2f}) | "
            f"{len(self.dome_regions)} kubbe | "
            f"teğet_sıçrama={self.max_tangent_jump_deg:.2f}° "
            f"({'sürekli' if self.tangent_continuous else 'SÜREKSİZ'}) | "
            f"min_r={self.min_radius_mm:.2f}mm "
            f"({'KALKIŞ' if self.liftoff_risk else 'OK'}) | "
            f"maks_kayma={self.max_slip_margin:.2f} | "
            f"bant_distorsiyon={self.max_bandwidth_distortion:.2f}×"
        )


def _meridian_curvature(z: np.ndarray, r: np.ndarray) -> np.ndarray:
    """Meridyen eğriliği κ = |r''| / (1+r'²)^{3/2} [1/mm]."""
    rp = np.gradient(r, z)
    rpp = np.gradient(rp, z)
    return np.abs(rpp) / np.power(1.0 + rp ** 2, 1.5)


def _detect_dome_regions(
    z: np.ndarray, r: np.ndarray,
    curvature_threshold_1_mm: float = 1e-3,
) -> List[DomeRegion]:
    """Eğri meridyen (kubbe) bölgelerini eğrilik eşiğiyle tespit et."""
    kappa = _meridian_curvature(z, r)
    flags = kappa > curvature_threshold_1_mm
    regions: List[DomeRegion] = []
    n = len(z)
    z_mid = (z[0] + z[-1]) * 0.5

    i = 0
    while i < n:
        if not flags[i]:
            i += 1
            continue
        j = i
        while j < n and flags[j]:
            j += 1
        regions.append(DomeRegion(
            z_start_mm=float(z[i]), z_end_mm=float(z[j - 1]),
            r_start_mm=float(r[i]), r_end_mm=float(r[j - 1]),
            side="sol" if z[i] < z_mid else "sağ",
            max_curvature_1_mm=float(np.max(kappa[i:j])),
        ))
        i = j
    return regions


def analyze_dome_transition(
    profile: MandrelProfile,
    alpha_nominal_deg: float,
    friction_coeff: float = 0.30,
    n_z: int = 400,
    tangent_jump_threshold_deg: float = 5.0,
    curvature_threshold_1_mm: float = 1e-3,
    polar_tol_mm: float = 1.0,
) -> DomeTransitionReport:
    """
    Bir mandrel profili üzerinde kubbe geçiş geodezik fiziğini analiz et.

    Clairaut sabiti, silindirik gövdenin nominal açısından türetilir:
        c = r_cyl · sin(α_nominal),   r_cyl = profil maksimum yarıçapı.

    Parametreler
    ----------
    profile        : Mandrel geometrisi (kubbe içermeli; silindir için kubbe=0).
    alpha_nominal_deg : Silindirik gövdedeki nominal sarma açısı.
    friction_coeff : Fiber-mandrel sürtünme katsayısı μ.
    tangent_jump_threshold_deg : Teğet süreksizlik eşiği (°).
    curvature_threshold_1_mm   : Kubbe tespiti eğrilik eşiği (1/mm).
    polar_tol_mm   : Kutupsal dönüş tespiti için |r − c| toleransı.

    Döner
    -----
    DomeTransitionReport — kubbe geçiş edilebilirlik kararı + riskler.
    """
    z = np.linspace(float(profile.z_mm[0]), float(profile.z_mm[-1]), n_z)
    r = np.interp(z, profile.z_mm, profile.r_mm)

    r_cyl = float(np.max(r))
    c = r_cyl * math.sin(math.radians(alpha_nominal_deg))

    # ── Kubbe bölgeleri ───────────────────────────────────────────────────────
    dome_regions = _detect_dome_regions(z, r, curvature_threshold_1_mm)

    # ── Lokal sarma açısı (geodezik): α(z) = arcsin(c / r) ──────────────────
    ratio = np.clip(c / np.maximum(r, 1e-9), 0.0, 1.0)
    alpha_local = np.degrees(np.arcsin(ratio))
    # r < c → kalkış (tanımsız); maskele
    liftoff_mask = r < c - 1e-9
    liftoff_risk = bool(liftoff_mask.any())
    min_r = float(np.min(r))

    # ── Teğet süreklilik (meridyen teğet açısı) ─────────────────────────────
    rp = np.gradient(r, z)
    phi_m = np.degrees(np.arctan2(rp, 1.0))
    tangent_jumps = np.abs(np.diff(phi_m))
    max_jump = float(np.max(tangent_jumps)) if len(tangent_jumps) else 0.0
    tangent_continuous = max_jump <= tangent_jump_threshold_deg

    # ── Kutupsal dönüş konumları: r ≈ c ──────────────────────────────────────
    turnaround_z: List[float] = []
    near_polar = np.abs(r - c) <= polar_tol_mm
    if near_polar.any():
        idx = np.where(near_polar)[0]
        # Bitişik grupların merkezlerini al
        splits = np.where(np.diff(idx) > 1)[0] + 1
        for grp in np.split(idx, splits):
            turnaround_z.append(float(z[grp].mean()))

    # ── Kayma marjı (kubbe bölgelerinde) ─────────────────────────────────────
    valid_alpha = ~liftoff_mask
    slip_margin = np.zeros_like(alpha_local)
    slip_margin[valid_alpha] = (
        np.tan(np.radians(np.clip(alpha_local[valid_alpha], 0.0, 89.5)))
        / max(friction_coeff, 1e-3)
    )
    max_slip = float(np.max(slip_margin)) if valid_alpha.any() else float("inf")
    slip_zones = int(np.sum(slip_margin > 1.0))

    # ── Bant genişliği distorsiyonu: W/sin(α) silindire göre ────────────────
    # Silindir referansı: W/sin(α_nominal). Kubbede α↑ → projeksiyon daralır.
    sin_nom = math.sin(math.radians(max(alpha_nominal_deg, 0.5)))
    sin_local = np.sin(np.radians(np.clip(alpha_local, 0.5, 90.0)))
    sin_local = np.where(valid_alpha, sin_local, sin_nom)
    # distorsiyon = (W/sin_local) / (W/sin_nom) = sin_nom/sin_local
    bandwidth_distortion = sin_nom / np.maximum(sin_local, 1e-6)
    max_distortion = float(np.max(bandwidth_distortion))

    # ── Kritik sorunlar ve uyarılar ───────────────────────────────────────────
    critical: List[str] = []
    warnings: List[str] = []

    if liftoff_risk:
        critical.append(
            f"Fiber kalkışı: min_r={min_r:.2f}mm < Clairaut c={c:.2f}mm "
            f"(yol fiziksel olarak imkânsız)")
    if not tangent_continuous:
        critical.append(
            f"Teğet süreksizliği: meridyen sıçraması {max_jump:.1f}° > "
            f"{tangent_jump_threshold_deg:.1f}° (köprüleme/kırışma)")
    # Kayma marjı tan(α)/μ yalnızca UYARI seviyesindedir: ideal geodezik yolda
    # geodezik eğrilik k_g = 0 olduğundan (Clairaut) yanal kuvvet yoktur ve
    # yüksek açıda bile fiber kaymaz. Bu metrik, geodezik-olmayan sapmalar için
    # mevcut sürtünme marjını (agresiflik göstergesi) ifade eder.
    if max_slip > 1.0:
        warnings.append(
            f"Kubbede yüksek açı agresifliği: maks tan(α)/μ = {max_slip:.2f} > 1 "
            f"(geodezik yol kayma üretmez; geodezik-olmayan düzeltme marjı dar)")
    if max_distortion > 2.0:
        warnings.append(
            f"Bant distorsiyonu {max_distortion:.2f}× (kutupta yığılma/reçine fazlası)")
    if not turnaround_z and dome_regions:
        warnings.append("Kutupsal dönüş bölgesi tespit edilmedi (c kutba ulaşmıyor olabilir)")

    is_traversable = len(critical) == 0

    return DomeTransitionReport(
        clairaut_c_mm=c,
        polar_radius_mm=c,
        dome_regions=dome_regions,
        max_tangent_jump_deg=max_jump,
        tangent_continuous=tangent_continuous,
        min_radius_mm=min_r,
        liftoff_risk=liftoff_risk,
        turnaround_z_mm=turnaround_z,
        max_slip_margin=max_slip,
        slip_risk_zones=slip_zones,
        max_bandwidth_distortion=max_distortion,
        is_traversable=is_traversable,
        critical_issues=critical,
        warnings=warnings,
    )
