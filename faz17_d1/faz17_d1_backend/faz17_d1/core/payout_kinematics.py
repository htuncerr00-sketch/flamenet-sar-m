"""
core/payout_kinematics.py — Payout Göz Kinematiği
===================================================
Sarma kafasındaki payout gözünün gerçek geometrik modelini uygular:

- Göz ofset modeli: göz, mandrel yüzeyinden sabit radyal mesafede
- Gerçek temas noktası: gözden mandrel yüzeyine teğet hesabı
- Taşıyıcı öncülük (lead) tazminatı
- Çarpışma zarfı: göz ile mandrel arasındaki minimum boşluk

Koordinat sistemi
-----------------
Tüm koordinatlar (r, z) düzlemindedir (r: radyal, z: eksenel).
3B dönme simetrisi nedeniyle bu 2B analiz yeterlidir.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from .geometry_engine import MandrelProfile
from .path_generator import WindingPath


# ── Yapılandırma ─────────────────────────────────────────────────────────────

@dataclass
class PayoutEyeConfig:
    """
    Payout gözü makine parametreleri.

    standoff_mm  : Göz merkezi ile mandrel yüzeyi arası radyal boşluk.
    lead_mm      : Gözün temas noktasından önceki eksenel konum (ileri yönde).
                   Pozitif = taşıyıcı temas noktasının önünde.
    eye_width_mm : Göz açıklığı (fiziksel boyut); bant genişliğini kısıtlar.
    max_payout_angle_deg : İzin verilen maksimum fiber çıkış açısı (yataydan).
    """
    standoff_mm: float = 150.0          # mm
    lead_mm: float = 0.0                # mm
    eye_width_mm: float = 8.0           # mm
    max_payout_angle_deg: float = 45.0  # °

    def __post_init__(self) -> None:
        if self.standoff_mm <= 0:
            raise ValueError(f"standoff_mm > 0 olmalı: {self.standoff_mm}")
        if self.eye_width_mm <= 0:
            raise ValueError(f"eye_width_mm > 0 olmalı: {self.eye_width_mm}")


# ── Temas noktası ─────────────────────────────────────────────────────────────

@dataclass
class ContactPoint:
    """Gözden mandrel yüzeyine teğet noktası."""
    z_contact_mm: float       # Fiber-mandrel temas noktasının eksenel konumu
    r_contact_mm: float       # Temas noktasındaki mandrel yarıçapı
    z_eye_mm: float           # Göz merkezi eksenel konumu
    r_eye_mm: float           # Göz merkezi radyal konumu
    payout_angle_deg: float   # Fiberin eksen yönünden sapma açısı (°)
    tangent_length_mm: float  # Gözden temas noktasına serbest fiber uzunluğu
    is_valid: bool            # Geometrik olarak mümkün mü?
    issue: str = ""           # Geçersizse sebep


def _tangent_to_circle(eye_r: float, eye_z: float,
                        circ_r: float, circ_z: float) -> Tuple[float, float, float]:
    """
    Gözden mandrel yüzeyine azimüt düzlemi teğeti hesapla.

    Fizik: Azimüt (r-θ) düzleminde göz, mandrel ekseninden eye_r uzaklıktadır.
    Mandrel, circ_r yarıçaplı bir çemberdir. Bu düzlemdeki teğet uzunluğu
    L = sqrt(eye_r² − circ_r²) formülüyle bulunur.

    r-z eksenel kesitindeki yatay öteleme (eksenel öncülük lead_mm) teğet
    uzunluğuna kök-kare düzeltmesi olarak eklenir.

    Döner: (tan_len, contact_z, contact_r)
    """
    if eye_r <= circ_r + 1e-9:
        # Göz mandrel içinde — geçersiz
        return -1.0, eye_z, circ_r

    # Azimüt düzleminde teğet uzunluğu
    tan_az = math.sqrt(max(0.0, eye_r ** 2 - circ_r ** 2))

    # Eksenel öteleme (lead): 3B teğet uzunluğu
    dz = eye_z - circ_z
    tan_len = math.sqrt(tan_az ** 2 + dz ** 2)

    # Temas noktası: nominal mandrel yüzeyi (circ_z, circ_r)
    return tan_len, circ_z, circ_r


def compute_contact_point(
    z_nominal_mm: float,
    profile: MandrelProfile,
    eye_config: PayoutEyeConfig,
    travel_dir: float = 1.0,
) -> ContactPoint:
    """
    Verilen taşıyıcı konumu için fiber-mandrel temas noktasını hesapla.

    Parametreler
    ----------
    z_nominal_mm : Hedef temas noktasının eksenel konumu.
    profile      : Mandrel geometrisi.
    eye_config   : Payout gözü yapılandırması.
    travel_dir   : Seyahat yönü (+1 ileriye, -1 geriye).
                   Lead işaretini belirler.
    """
    r_contact = profile.radius_at(z_nominal_mm)
    r_eye = r_contact + eye_config.standoff_mm
    z_eye = z_nominal_mm + travel_dir * eye_config.lead_mm

    tan_len, z_tan, r_tan = _tangent_to_circle(
        eye_r=r_eye, eye_z=z_eye,
        circ_r=r_contact, circ_z=z_nominal_mm,
    )

    if tan_len < 0:
        return ContactPoint(
            z_contact_mm=z_nominal_mm, r_contact_mm=r_contact,
            z_eye_mm=z_eye, r_eye_mm=r_eye,
            payout_angle_deg=0.0, tangent_length_mm=0.0,
            is_valid=False,
            issue=f"Göz mandrel içinde (z={z_nominal_mm:.1f}mm, r_eye={r_eye:.1f} <= r_mandrel={r_contact:.1f})",
        )

    # Payout açısı (azimüt düzlemi): fiberin radyal yönden sapma açısı.
    # sin(phi) = R / r_eye  →  phi küçükse fiber neredeyse radyal gidiyor.
    # max_payout_angle_deg ile karşılaştırılır; phi < max → geçerli.
    sin_phi = r_contact / max(r_eye, 1e-9)
    payout_angle_deg = math.degrees(math.asin(min(1.0, sin_phi)))

    is_valid = payout_angle_deg <= eye_config.max_payout_angle_deg
    issue = "" if is_valid else (
        f"Payout açısı {payout_angle_deg:.1f}° > maks "
        f"{eye_config.max_payout_angle_deg:.1f}°"
    )

    return ContactPoint(
        z_contact_mm=z_nominal_mm,
        r_contact_mm=r_contact,
        z_eye_mm=z_eye,
        r_eye_mm=r_eye,
        payout_angle_deg=payout_angle_deg,
        tangent_length_mm=tan_len,
        is_valid=is_valid,
        issue=issue,
    )


# ── Taşıyıcı öncülük tazminatı ───────────────────────────────────────────────

def compute_carriage_lead(
    z_contact_mm: float,
    alpha_deg: float,
    profile: MandrelProfile,
    eye_config: PayoutEyeConfig,
) -> float:
    """
    Doğru fiber çıkış açısını sağlamak için gereken taşıyıcı öncülük mesafesi.

    Geometri (r-z düzlemi):
    - Temas noktası: (z_c, r_c)
    - Göz: (z_c + lead, r_c + standoff)
    - Fiber, temas noktasında sarma açısı α ile yüzeye teğet olmalı

    Birinci derece yaklaşım:
        lead ≈ standoff · tan(α)

    Parametreler
    ----------
    alpha_deg : Sarma açısı (eksen yönünden, °).
    """
    alpha_rad = math.radians(max(0.1, min(alpha_deg, 89.9)))
    return eye_config.standoff_mm * math.tan(alpha_rad)


# ── Çarpışma zarfı ───────────────────────────────────────────────────────────

@dataclass
class CollisionResult:
    """Göz-mandrel çarpışma analiz sonucu."""
    z_mm: float               # Kontrol noktasının eksenel konumu
    clearance_mm: float       # Göz ile mandrel arası boşluk (< 0 = çarpışma)
    is_collision: bool
    description: str


def check_collision_envelope(
    profile: MandrelProfile,
    eye_config: PayoutEyeConfig,
    n_points: int = 200,
    margin_mm: float = 5.0,
) -> List[CollisionResult]:
    """
    Taşıyıcı gidiş yolu boyunca göz-mandrel çarpışma zarfını kontrol et.

    Göz, mandrel yüzeyini (standoff) mesafesinde takip eder.
    Ancak profil hızlı değiştiğinde göz ile komşu mandrel geometrisi
    arasındaki boşluk azalabilir.

    Parametreler
    ----------
    margin_mm : Güvenlik payı — bu değerin altındaki boşluklar uyarı üretir.
    """
    z_arr = np.linspace(float(profile.z_mm[0]), float(profile.z_mm[-1]), n_points)
    r_arr = np.interp(z_arr, profile.z_mm, profile.r_mm)
    r_eye_arr = r_arr + eye_config.standoff_mm

    results: List[CollisionResult] = []
    lead = abs(eye_config.lead_mm)

    for i in range(n_points):
        z_eye = z_arr[i] + lead
        r_eye = r_eye_arr[i]

        # Arama penceresi: ±lead kadar
        z_lo = z_arr[i] - lead - eye_config.eye_width_mm
        z_hi = z_arr[i] + lead + eye_config.eye_width_mm
        mask = (profile.z_mm >= z_lo) & (profile.z_mm <= z_hi)
        if not mask.any():
            continue

        # Komşu mandrel profili maksimum yarıçapı
        r_nbr_max = float(profile.r_mm[mask].max())

        # Göz ile mandrel arasındaki radyal boşluk
        clearance = r_eye - (r_nbr_max + margin_mm)

        if clearance < 0:
            results.append(CollisionResult(
                z_mm=z_arr[i],
                clearance_mm=float(r_eye - r_nbr_max),
                is_collision=(r_eye < r_nbr_max),
                description=(
                    f"z={z_arr[i]:.1f}mm: r_göz={r_eye:.1f} mm, "
                    f"r_mandrel_komşu={r_nbr_max:.1f} mm, "
                    f"boşluk={r_eye - r_nbr_max:.1f} mm (güvenlik payı={margin_mm:.1f} mm)"
                ),
            ))

    return results


# ── Sarma yolu boyunca payout analizi ────────────────────────────────────────

@dataclass
class PayoutKinematicsReport:
    """Sarma yolunun tamamı için payout kinematiği raporu."""
    n_points_analyzed: int
    n_valid: int
    n_invalid: int
    max_payout_angle_deg: float
    min_tangent_length_mm: float
    max_tangent_length_mm: float
    collision_results: List[CollisionResult]
    invalid_points: List[Tuple[float, str]]   # (z_mm, sebep)
    is_valid: bool
    warnings: List[str]

    def summary(self) -> str:
        status = "GEÇERLİ" if self.is_valid else "GEÇERSİZ"
        return (
            f"Payout Kinematiği: {status} "
            f"({self.n_valid}/{self.n_points_analyzed} nokta geçerli) | "
            f"maks_payout_açısı={self.max_payout_angle_deg:.1f}° | "
            f"teğet_uzunluk=[{self.min_tangent_length_mm:.1f}, "
            f"{self.max_tangent_length_mm:.1f}] mm | "
            f"çarpışma={len(self.collision_results)}"
        )


def analyze_payout_kinematics(
    path: WindingPath,
    profile: MandrelProfile,
    eye_config: PayoutEyeConfig,
    sample_every: int = 10,
) -> PayoutKinematicsReport:
    """
    Sarma yolu boyunca payout gözü kinematik analizini gerçekleştir.

    Parametreler
    ----------
    sample_every : Yol noktaları örnekleme sıklığı (hesap hızı).
    """
    pts = path.points
    sampled = pts[::sample_every] if len(pts) > sample_every else pts

    n_valid = 0
    n_invalid = 0
    payout_angles: List[float] = []
    tangent_lengths: List[float] = []
    invalid_pts: List[Tuple[float, str]] = []

    for i, pt in enumerate(sampled):
        # Seyahat yönü: ardışık noktadan çıkar
        if i + 1 < len(sampled):
            travel_dir = 1.0 if sampled[i + 1].x_mm > pt.x_mm else -1.0
        else:
            travel_dir = 1.0

        cp = compute_contact_point(pt.x_mm, profile, eye_config, travel_dir)

        if cp.is_valid:
            n_valid += 1
        else:
            n_invalid += 1
            invalid_pts.append((pt.x_mm, cp.issue))

        payout_angles.append(cp.payout_angle_deg)
        tangent_lengths.append(cp.tangent_length_mm)

    collision_results = check_collision_envelope(profile, eye_config)

    warnings: List[str] = []
    if n_invalid > 0:
        warnings.append(f"{n_invalid} noktada payout açısı sınırı aşılıyor")
    if collision_results:
        n_coll = sum(1 for c in collision_results if c.is_collision)
        if n_coll:
            warnings.append(f"{n_coll} noktada çarpışma riski tespit edildi")
        else:
            warnings.append(f"{len(collision_results)} noktada düşük boşluk (güvenlik payı altında)")

    return PayoutKinematicsReport(
        n_points_analyzed=len(sampled),
        n_valid=n_valid,
        n_invalid=n_invalid,
        max_payout_angle_deg=max(payout_angles) if payout_angles else 0.0,
        min_tangent_length_mm=min(tangent_lengths) if tangent_lengths else 0.0,
        max_tangent_length_mm=max(tangent_lengths) if tangent_lengths else 0.0,
        collision_results=collision_results,
        invalid_points=invalid_pts,
        is_valid=n_invalid == 0 and not any(c.is_collision for c in collision_results),
        warnings=warnings,
    )
