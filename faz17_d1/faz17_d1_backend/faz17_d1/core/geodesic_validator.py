"""
core/geodesic_validator.py — Gerçek Geodezik Doğrulama Motoru
===============================================================
Sarma yolunun Clairaut geodezik koşuluna uyumunu, kayma riskini,
kalkış (lift-off) bölgelerini ve kararsız sarma bölgelerini analiz eder.

Temel ilişkiler
---------------
Clairaut sabiti  : c = r(z) · sin(α(z))  =  sabit boyunca geodezik
Kayma riski      : R_slip = tan(α) / μ         — μ: sürtünme katsayısı
Kalkış koşulu    : r(z) < c             — fiber mandrel yüzeyini terk eder
Açısal oran      : dθ/dz = c / (r · √(r² − c²))  → r = c'de patlıyor
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from .geometry_engine import MandrelProfile
from .path_generator import WindingPath, WindingPoint


# ── Sonuç yapıları ──────────────────────────────────────────────────────────

@dataclass
class ClairautErrorStats:
    """Clairaut hata istatistikleri."""
    max_pct: float           # Maksimum hata (%)
    mean_pct: float          # Ortalama hata (%)
    rms_pct: float           # RMS hata (%)
    n_samples: int           # Toplam örnek sayısı
    n_high_error: int        # %1'den fazla hata olan örnek sayısı
    high_error_threshold_pct: float = 1.0


@dataclass
class Zone:
    """Bir sorun bölgesi."""
    z_start_mm: float
    z_end_mm: float
    severity: float    # 0..1 (1 = kritik)
    description: str


@dataclass
class GeodesicValidationReport:
    """
    Geodezik yol doğrulama raporu.

    is_valid = True ancak ve ancak kritik sorun yoksa.
    """
    # Clairaut kalitesi
    clairaut_c_ref: float
    clairaut_error: ClairautErrorStats

    # Bölge tespiti
    lift_off_zones: List[Zone]      # r(z) < c bölgeleri
    slip_risk_zones: List[Zone]     # tan(α)/μ > 1 bölgeleri
    unstable_zones: List[Zone]      # α < α_min veya α > α_max bölgeleri
    high_curvature_zones: List[Zone]  # dα/dz çok büyük

    # Açı istatistikleri
    alpha_min_deg: float
    alpha_max_deg: float
    alpha_mean_deg: float

    # Doğrulama sonucu
    is_valid: bool
    critical_issues: List[str]
    warnings: List[str]

    def summary(self) -> str:
        status = "GEÇERLİ" if self.is_valid else "GEÇERSİZ"
        lines = [
            f"Geodezik Doğrulama: {status}",
            f"  Clairaut c = {self.clairaut_c_ref:.4f} mm",
            f"  Clairaut hatası: ort={self.clairaut_error.mean_pct:.3f}%  "
            f"maks={self.clairaut_error.max_pct:.3f}%  rms={self.clairaut_error.rms_pct:.3f}%",
            f"  Sarma açısı: {self.alpha_min_deg:.1f}°–{self.alpha_max_deg:.1f}°  "
            f"(ort {self.alpha_mean_deg:.1f}°)",
            f"  Kalkış bölgeleri: {len(self.lift_off_zones)}",
            f"  Kayma riski bölgeleri: {len(self.slip_risk_zones)}",
            f"  Kararsız bölgeler: {len(self.unstable_zones)}",
        ]
        if self.critical_issues:
            lines.append("  KRİTİK: " + "; ".join(self.critical_issues))
        if self.warnings:
            lines.append("  UYARI: " + "; ".join(self.warnings))
        return "\n".join(lines)


# ── Yardımcı işlevler ────────────────────────────────────────────────────────

def _compute_local_alpha(pts: List[WindingPoint],
                          profile: MandrelProfile) -> np.ndarray:
    """
    Her sarma noktasında lokal sarma açısını (eksen yönünden derece) hesapla.

    Merkezi fark yöntemi kullanılır; uç noktalar tek taraflı fark ile belirlenir.

    alpha = atan2(|dz|, |r · dθ|)  [eksen yönünden]
    """
    N = len(pts)
    z_arr = np.array([p.x_mm for p in pts], dtype=np.float64)
    # Kümülatif spindle açısından reel dθ'yi al (her zaman artar)
    a_cum = np.radians(np.array([p.a_deg for p in pts]))
    r_arr = np.interp(z_arr, profile.z_mm, profile.r_mm)

    alpha_arr = np.empty(N, dtype=np.float64)

    for i in range(N):
        if i == 0:
            dz = z_arr[1] - z_arr[0]
            da = a_cum[1] - a_cum[0]
            r = r_arr[0]
        elif i == N - 1:
            dz = z_arr[-1] - z_arr[-2]
            da = a_cum[-1] - a_cum[-2]
            r = r_arr[-1]
        else:
            dz = z_arr[i + 1] - z_arr[i - 1]
            da = a_cum[i + 1] - a_cum[i - 1]
            r = r_arr[i]

        ds_circ = abs(r * da)
        ds_axial = abs(dz)
        alpha_arr[i] = math.degrees(math.atan2(ds_circ, ds_axial + 1e-12))

    return alpha_arr


def _merge_zones(flags: np.ndarray, z_arr: np.ndarray,
                  severity_arr: np.ndarray,
                  desc: str) -> List[Zone]:
    """
    Boolean bayrağı dizisinden bitişik bölgeleri birleştir.

    Parametreler
    ----------
    flags        : (N,) bool  — sorunlu noktalar
    z_arr        : (N,) float — eksenel konumlar
    severity_arr : (N,) float — 0..1 şiddet değerleri
    desc         : Bölge açıklaması
    """
    zones: List[Zone] = []
    if not flags.any():
        return zones

    in_zone = False
    z_start = 0.0
    sev_accum: List[float] = []

    for i in range(len(flags)):
        if flags[i] and not in_zone:
            in_zone = True
            z_start = z_arr[i]
            sev_accum = [severity_arr[i]]
        elif flags[i] and in_zone:
            sev_accum.append(severity_arr[i])
        elif not flags[i] and in_zone:
            zones.append(Zone(
                z_start_mm=z_start,
                z_end_mm=z_arr[i - 1],
                severity=float(np.max(sev_accum)),
                description=desc,
            ))
            in_zone = False
            sev_accum = []

    if in_zone and sev_accum:
        zones.append(Zone(
            z_start_mm=z_start,
            z_end_mm=float(z_arr[-1]),
            severity=float(np.max(sev_accum)),
            description=desc,
        ))

    return zones


# ── Ana doğrulama işlevi ────────────────────────────────────────────────────

def validate_geodesic(
    path: WindingPath,
    profile: MandrelProfile,
    friction_coeff: float = 0.3,
    alpha_min_deg: float = 5.0,
    alpha_max_deg: float = 89.0,
    high_curvature_threshold_deg_mm: float = 2.0,
) -> GeodesicValidationReport:
    """
    Sarma yolunun geodezik kalitesini değerlendir.

    Parametreler
    ----------
    path : WindingPath
        Analiz edilecek sarma yolu.
    profile : MandrelProfile
        Mandrel geometrisi.
    friction_coeff : float
        Fiber-mandrel sürtünme katsayısı μ (tipik: 0.2–0.5).
    alpha_min_deg : float
        İzin verilen minimum sarma açısı.
    alpha_max_deg : float
        İzin verilen maksimum sarma açısı.
    high_curvature_threshold_deg_mm : float
        Açı değişim hızı için uyarı eşiği (°/mm).
    """
    pts = path.points
    if len(pts) < 3:
        empty_err = ClairautErrorStats(0.0, 0.0, 0.0, 0, 0)
        return GeodesicValidationReport(
            clairaut_c_ref=path.clairaut_c,
            clairaut_error=empty_err,
            lift_off_zones=[], slip_risk_zones=[],
            unstable_zones=[], high_curvature_zones=[],
            alpha_min_deg=0.0, alpha_max_deg=0.0, alpha_mean_deg=0.0,
            is_valid=False,
            critical_issues=["Yeterli yol noktası yok (< 3)"],
            warnings=[],
        )

    c_ref = path.clairaut_c
    z_arr = np.array([p.x_mm for p in pts])
    r_arr = np.interp(z_arr, profile.z_mm, profile.r_mm)

    # ── Lokal sarma açısı ──────────────────────────────────────────────────
    alpha_arr = _compute_local_alpha(pts, profile)

    # ── Clairaut hatası ────────────────────────────────────────────────────
    # c_local = r(z) * sin(alpha_local)
    alpha_rad_arr = np.radians(alpha_arr)
    c_local_arr = r_arr * np.sin(alpha_rad_arr)

    if abs(c_ref) > 1e-9:
        err_arr = np.abs(c_local_arr - c_ref) / abs(c_ref) * 100.0  # %
    else:
        err_arr = np.zeros_like(c_local_arr)

    high_err_mask = err_arr > 1.0
    clairaut_err = ClairautErrorStats(
        max_pct=float(err_arr.max()),
        mean_pct=float(err_arr.mean()),
        rms_pct=float(np.sqrt(np.mean(err_arr ** 2))),
        n_samples=len(err_arr),
        n_high_error=int(high_err_mask.sum()),
    )

    # ── Kalkış bölgeleri: r(z) < c ────────────────────────────────────────
    lift_off_flags = r_arr < c_ref
    lift_off_sev = np.where(lift_off_flags, 1.0 - np.minimum(r_arr / (c_ref + 1e-9), 1.0), 0.0)
    lift_off_zones = _merge_zones(
        lift_off_flags, z_arr, lift_off_sev,
        "Fiber kalkışı: r(z) < Clairaut sabiti"
    )

    # ── Kayma riski bölgeleri: tan(α)/μ > eşik ────────────────────────────
    # R_slip = tan(alpha) / mu; kayma: R_slip >= 1
    tan_alpha = np.tan(alpha_rad_arr)
    slip_ratio = tan_alpha / max(friction_coeff, 1e-3)
    slip_flags = slip_ratio > 1.0
    slip_sev = np.clip((slip_ratio - 1.0) / max(slip_ratio.max() - 1.0 + 1e-9, 1.0), 0.0, 1.0)
    slip_risk_zones = _merge_zones(
        slip_flags, z_arr, slip_sev,
        f"Kayma riski: tan(α)/μ > 1  (μ={friction_coeff:.2f})"
    )

    # ── Kararsız bölgeler: açı sınır dışı ─────────────────────────────────
    unstable_flags = (alpha_arr < alpha_min_deg) | (alpha_arr > alpha_max_deg)
    unstable_sev = np.zeros_like(alpha_arr)
    too_low = alpha_arr < alpha_min_deg
    too_high = alpha_arr > alpha_max_deg
    if too_low.any():
        unstable_sev[too_low] = (alpha_min_deg - alpha_arr[too_low]) / alpha_min_deg
    if too_high.any():
        unstable_sev[too_high] = (alpha_arr[too_high] - alpha_max_deg) / (90.0 - alpha_max_deg + 1e-9)
    unstable_zones = _merge_zones(
        unstable_flags, z_arr, np.clip(unstable_sev, 0.0, 1.0),
        f"Kararsız sarma açısı: dışında [{alpha_min_deg:.1f}°, {alpha_max_deg:.1f}°]"
    )

    # ── Yüksek eğrilik bölgeleri: |dα/dz| > eşik ─────────────────────────
    if len(z_arr) > 2:
        dz = np.diff(z_arr)
        dalpha = np.diff(alpha_arr)
        curv_rate = np.abs(dalpha) / np.maximum(np.abs(dz), 1e-9)  # °/mm
        curv_rate_full = np.concatenate([[curv_rate[0]], curv_rate])
        hc_flags = curv_rate_full > high_curvature_threshold_deg_mm
        hc_sev = np.clip(curv_rate_full / (high_curvature_threshold_deg_mm + 1e-9) - 1.0, 0.0, 1.0)
        high_curvature_zones = _merge_zones(
            hc_flags, z_arr, hc_sev,
            f"|dα/dz| > {high_curvature_threshold_deg_mm:.1f} °/mm"
        )
    else:
        high_curvature_zones = []

    # ── Açı istatistikleri ─────────────────────────────────────────────────
    alpha_min = float(alpha_arr.min())
    alpha_max = float(alpha_arr.max())
    alpha_mean = float(alpha_arr.mean())

    # ── Kritik sorunlar ve uyarılar ────────────────────────────────────────
    critical: List[str] = []
    warnings: List[str] = []

    if lift_off_zones:
        max_sev = max(z.severity for z in lift_off_zones)
        msg = (f"{len(lift_off_zones)} kalkış bölgesi "
               f"(maks şiddet {max_sev:.2f})")
        if max_sev > 0.1:
            critical.append(msg)
        else:
            warnings.append(msg)

    if slip_risk_zones:
        max_sev = max(z.severity for z in slip_risk_zones)
        msg = f"{len(slip_risk_zones)} kayma riski bölgesi (maks={max_sev:.2f})"
        if max_sev > 0.5:
            critical.append(msg)
        else:
            warnings.append(msg)

    if clairaut_err.max_pct > 5.0:
        critical.append(f"Clairaut hatası çok yüksek: %{clairaut_err.max_pct:.2f}")
    elif clairaut_err.max_pct > 1.0:
        warnings.append(f"Clairaut hatası: %{clairaut_err.max_pct:.2f}")

    if unstable_zones:
        warnings.append(f"{len(unstable_zones)} kararsız açı bölgesi")

    if high_curvature_zones:
        warnings.append(f"{len(high_curvature_zones)} yüksek eğrilik bölgesi")

    is_valid = len(critical) == 0

    return GeodesicValidationReport(
        clairaut_c_ref=c_ref,
        clairaut_error=clairaut_err,
        lift_off_zones=lift_off_zones,
        slip_risk_zones=slip_risk_zones,
        unstable_zones=unstable_zones,
        high_curvature_zones=high_curvature_zones,
        alpha_min_deg=alpha_min,
        alpha_max_deg=alpha_max,
        alpha_mean_deg=alpha_mean,
        is_valid=is_valid,
        critical_issues=critical,
        warnings=warnings,
    )


# ── Ek analiz yardımcıları ──────────────────────────────────────────────────

def clairaut_stability_map(
    profile: MandrelProfile,
    alpha_nominal_deg: float,
    n_z: int = 200,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Profil boyunca Clairaut stabilitesini görselleştirme verileri üret.

    Döner: (z_arr, r_arr, alpha_local_arr)  —  stabilitenin analitik profili.

    Clairaut sabiti: c = r(0) · sin(alpha_nominal)
    Lokal açı: alpha(z) = arcsin(c / r(z))   (c ≤ r(z) şartıyla)
    """
    c = profile.avg_radius_mm * math.sin(math.radians(alpha_nominal_deg))
    z_arr = np.linspace(float(profile.z_mm[0]), float(profile.z_mm[-1]), n_z)
    r_arr = np.interp(z_arr, profile.z_mm, profile.r_mm)

    alpha_arr = np.full(n_z, float('nan'))
    valid = r_arr >= c
    alpha_arr[valid] = np.degrees(np.arcsin(
        np.minimum(c / np.maximum(r_arr[valid], 1e-9), 1.0)
    ))

    return z_arr, r_arr, alpha_arr


def geodesic_deviation_metric(path: WindingPath,
                               profile: MandrelProfile) -> float:
    """
    Yol noktaları boyunca Clairaut sabitinin normalize standart sapması.

    0.0 = mükemmel geodezik, 1.0 = son derece sapkın yol.
    """
    pts = path.points
    if len(pts) < 2:
        return 1.0

    c_ref = path.clairaut_c
    if abs(c_ref) < 1e-9:
        return 0.0

    alpha_arr = _compute_local_alpha(pts, profile)
    z_arr = np.array([p.x_mm for p in pts])
    r_arr = np.interp(z_arr, profile.z_mm, profile.r_mm)
    alpha_rad = np.radians(alpha_arr)
    c_local = r_arr * np.sin(alpha_rad)

    std_norm = float(np.std(c_local)) / abs(c_ref)
    return min(1.0, std_norm)
