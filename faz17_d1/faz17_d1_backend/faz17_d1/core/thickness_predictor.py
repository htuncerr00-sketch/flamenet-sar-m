"""
core/thickness_predictor.py — Katman Kalınlık Tahmincisi
=========================================================
Bir LayerSchedule ve mandrel geometrisinden eksenel kalınlık dağılımını
hesaplar. Silindirde homojen, konik ve kubbeli profillerde geometri
düzeltmeli kalınlık tahminleri üretir.

Kalınlık Modeli
---------------
Tek kat kalınlığı:
    t_ply = tow_thickness × compaction_factor(idx, tension, radius)

Eksenel kalınlık değişimi (dönel yüzey):
    Silindir (r=sabit):  t_total homojen
    Konik (r doğrusal):  t ~ 1 (kural karışımı, bant genişliğine göre)
    Kubbe:               r → küçük olduğunda geodezik yoğunlaşma

Clairaut geodezik: c = r·sin(α) = sabit
    α(z) = arcsin(c / r(z))   → r küçüldükçe α büyür
    → fiber kubbede daha dikleşir → axial thickness coverage değişmez,
      ama çevresel yoğunluk artar.

Pratik yaklaşım (ısıl tasarım seviyesi):
    t_total(z) = Σ_i n_i_plies × t_ply_i × coverage_factor_i(z)
    coverage_factor(z) = 1 (silindir), değişken (konik/kubbe)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from .fiber_deposition import compaction_factor as _compaction_fn
from .geometry_engine import MandrelProfile
from .laminate_builder import AngleFamily, LayerSchedule, LaminateStack, build_laminate
from .material_database import MaterialSpec


# ── Kalınlık haritası ─────────────────────────────────────────────────────────

@dataclass
class ThicknessMap:
    """
    Eksenel konuma göre kalınlık tahmini.

    z_mm          : Eksenel koordinatlar (N nokta).
    thickness_mm  : Tahmin edilen toplam kalınlık (N nokta).
    thickness_by_family: Her açı ailesi için ayrı kalınlık katkısı.
    """
    z_mm: np.ndarray
    thickness_mm: np.ndarray
    thickness_by_family: List[np.ndarray]   # her aile için ayrı dizi

    @property
    def mean_mm(self) -> float:
        return float(np.mean(self.thickness_mm))

    @property
    def min_mm(self) -> float:
        return float(np.min(self.thickness_mm))

    @property
    def max_mm(self) -> float:
        return float(np.max(self.thickness_mm))

    @property
    def uniformity(self) -> float:
        """Kalınlık tekdüzeliği: 1 − std/mean ∈ [0,1]. 1 = tam tekdüze."""
        mu = self.mean_mm
        if mu < 1e-9:
            return 1.0
        return max(0.0, 1.0 - float(np.std(self.thickness_mm)) / mu)

    def summary(self) -> str:
        return (
            f"KalınlıkHaritası: ort={self.mean_mm:.3f}mm | "
            f"min={self.min_mm:.3f}mm | maks={self.max_mm:.3f}mm | "
            f"tekdüzelik={self.uniformity:.3f}"
        )


# ── Yardımcı: Clairaut kapsama faktörü ──────────────────────────────────────

def _clairaut_coverage_factor(
    z_mm: np.ndarray,
    alpha_cyl_deg: float,
    profile: MandrelProfile,
    r_cyl_mm: float,
) -> np.ndarray:
    """
    Her z konumu için Clairaut geodezik kapsama faktörü.

    Silindir bölgesinde = 1.0.
    Kubbe bölgesinde: r küçüldükçe α büyür, fiber daha sık sarılır.

    c = r_cyl × sin(α_cyl)   (Clairaut sabiti)
    α(z) = arcsin(c / r(z))  (r < c bölgelerinde = 90°, lift-off)

    Kapsama faktörü (çevresel yoğunluğun normalleştirilmesi):
        η(z) = r_cyl / r(z) × cos(α_cyl) / cos(α(z))
    (eksenel ilerleme başına kapsamanın silindir değeriyle oranı)
    """
    alpha_rad_cyl = math.radians(max(alpha_cyl_deg, 0.5))
    c = r_cyl_mm * math.sin(alpha_rad_cyl)

    r_arr = np.interp(z_mm, profile.z_mm, profile.r_mm)
    r2mc2 = r_arr ** 2 - c ** 2

    valid = r2mc2 > 0.0
    cos_alpha_z = np.where(valid, np.sqrt(np.maximum(r2mc2, 0.0)) / np.maximum(r_arr, 1e-6), 0.0)
    cos_alpha_cyl = math.cos(alpha_rad_cyl)

    factor = np.where(
        valid,
        (r_cyl_mm / np.maximum(r_arr, 1e-6)) * cos_alpha_cyl / np.maximum(cos_alpha_z, 1e-6),
        0.0,
    )
    return np.clip(factor, 0.0, 5.0)   # fiziksel sınır


# ── Ana tahmin fonksiyonu ─────────────────────────────────────────────────────

def predict_schedule_thickness(
    schedule: LayerSchedule,
    profile: MandrelProfile,
    material: MaterialSpec,
    tension_N: float = 50.0,
    n_z: int = 100,
) -> ThicknessMap:
    """
    Bir LayerSchedule için eksenel kalınlık haritası tahmin et.

    Her açı ailesi için:
        - Silindir referans yarıçapı r_cyl = profile'daki maksimum r
        - Clairaut kapsama faktörü → eksenel konum bağlılığı
        - Kat başı kalınlık = tow_thickness × compaction_factor(idx)
        - Toplam katkı = n_plies × t_ply × coverage_factor(z)

    Parametreler
    ------------
    schedule  : Sarma planı.
    profile   : Mandrel geometrisi.
    material  : Malzeme özellikleri.
    tension_N : Fiber gerilimi.
    n_z       : Eksenel örnekleme sayısı.
    """
    z_arr = np.linspace(float(profile.z_mm[0]), float(profile.z_mm[-1]), n_z)
    r_cyl = float(np.max(profile.r_mm))

    # Tüm katların toplam kalınlığı için global kat indeksi
    global_idx = 0
    family_thicknesses: List[np.ndarray] = []
    total = np.zeros(n_z)

    for family in schedule.families:
        fam_t = np.zeros(n_z)
        n_plies = family.n_plies

        if n_plies == 0:
            family_thicknesses.append(fam_t)
            continue

        # Kapsama faktörü (Clairaut)
        cov_factor = _clairaut_coverage_factor(z_arr, family.alpha_deg, profile, r_cyl)

        for _ in range(n_plies):
            r_mid = float(np.interp(float(np.median(z_arr)), profile.z_mm, profile.r_mm))
            cf = _compaction_fn(global_idx, tension_N, r_mid)
            t_ply = material.tow.tow_thickness_mm * cf

            # Bu kat kalınlığı eksenel kapsama faktörüyle modüle edilir
            fam_t += t_ply * cov_factor
            global_idx += 1

        total += fam_t
        family_thicknesses.append(fam_t)

    return ThicknessMap(
        z_mm=z_arr,
        thickness_mm=total,
        thickness_by_family=family_thicknesses,
    )


def predict_cylinder_thickness(
    schedule: LayerSchedule,
    material: MaterialSpec,
    radius_mm: float = 50.0,
    tension_N: float = 50.0,
) -> float:
    """
    Silindir bölgesi için tekil ortalama kalınlık tahmini (mm).

    Tüm kapsama faktörleri = 1 (silindir homojen).
    Bu hızlı bir tahmintir; kesin hesap için predict_schedule_thickness kullanın.
    """
    tow = material.tow
    total = 0.0
    global_idx = 0
    for family in schedule.families:
        for _ in range(family.n_plies):
            cf = _compaction_fn(global_idx, tension_N, radius_mm)
            total += tow.tow_thickness_mm * cf
            global_idx += 1
    return total


def predict_coverage_pct(
    schedule: LayerSchedule,
    profile: MandrelProfile,
    n_z: int = 60,
) -> float:
    """
    Kapsama yüzdesi tahmini.

    Tüm z konumlarında kapsama faktörü > 0 olanların oranı.
    Pratik: her aile en az 1 kat set içeriyorsa silindir bölgesi tamamen kaplıdır.
    """
    if schedule.is_empty():
        return 0.0
    z_arr = np.linspace(float(profile.z_mm[0]), float(profile.z_mm[-1]), n_z)
    r_cyl = float(np.max(profile.r_mm))
    covered = np.zeros(n_z, dtype=bool)
    for family in schedule.families:
        if family.n_layer_sets == 0:
            continue
        cov = _clairaut_coverage_factor(z_arr, family.alpha_deg, profile, r_cyl)
        covered |= cov > 0.01
    return 100.0 * float(np.mean(covered))


def evaluate_thickness_error(
    schedule: LayerSchedule,
    material: MaterialSpec,
    target_thickness_mm: float,
    radius_mm: float = 50.0,
    tension_N: float = 50.0,
) -> float:
    """
    Hedef kalınlığa göre bağıl hata (fraksiyonel).

    |t_achieved − t_target| / t_target ∈ [0, ∞)
    0 = hedef tam tutturuldu.
    """
    t = predict_cylinder_thickness(schedule, material, radius_mm, tension_N)
    if target_thickness_mm <= 0:
        return 0.0
    return abs(t - target_thickness_mm) / target_thickness_mm
