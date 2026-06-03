"""
core/production_estimator.py — Üretim Süresi ve Makine Hareketi Tahmincisi
===========================================================================
Bir LayerSchedule için makine çevre koşullarında döngü süresini,
taşıyıcı geçiş sayısını ve fiber uzunluğunu tahmin eder.

Sarma süre formülleri (döngü düzeyinde, trajektori simülasyonsuz):
------------------------------------------------------------------

Devre sayısı (n_circuits):
    n_circuits = ceil(2π·r_avg / (W·(1 − overlap/100)))
    (tüm stratejiler için, path_generator ile tutarlı)

Efektif ilerleme hızı (iş mili RPM sınırlı):
    v_eff = min(v_feed, r·ω_max / tan(α))
    ω_max = spindle_rpm_max × 2π/60  [rad/s]
    (düşük α → yüksek çevresel hız → iş mili sınırı devreye girer)

Bir geçiş süresi:
    t_pass = 2·L_eff / v_eff   (ileri + geri)
    L_eff = profil yay uzunluğu + kubbe geçiş payı

Bir kat seti süresi:
    t_set = n_circuits × t_pass + kubbe_geçiş_ek

Fiber uzunluğu (silindir approximasyon):
    L_fiber = 2·L / cos(α)  per devre  [geodezik]
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from .geometry_engine import MandrelProfile
from .laminate_builder import AngleFamily, LayerSchedule
from .machine_calibration import MachineCalibration, default_calibration
from .material_database import MaterialSpec


# ── Aile bazlı tahmin ─────────────────────────────────────────────────────────

@dataclass
class FamilyEstimate:
    """Tek bir açı ailesi için üretim tahmini."""
    alpha_deg: float
    strategy: str
    n_layer_sets: int
    n_circuits_per_set: int            # bir kat seti için devre sayısı
    traverse_length_mm: float          # efektif taşıyıcı geçiş uzunluğu (ileri+geri)
    effective_feed_mm_s: float         # iş mili sınırlı efektif hız
    time_per_set_s: float              # bir kat seti için süre (s)
    total_time_s: float                # tüm kat setleri için süre (s)
    fiber_length_per_set_mm: float     # bir kat seti için fiber uzunluğu (mm)
    total_fiber_length_mm: float       # tüm setler için toplam fiber (mm)
    is_spindle_limited: bool           # iş mili RPM sınırı aktif mi?


# ── Tam döngü tahmini ─────────────────────────────────────────────────────────

@dataclass
class CycleBreakdown:
    """Tüm sarma döngüsü için üretim tahmini."""
    family_estimates: List[FamilyEstimate]
    setup_time_s: float              # başlangıç + referans alma süresi
    dome_transition_overhead_s: float # kubbe geçiş ek süresi (tüm aileler)
    total_traverse_time_s: float     # tüm geçişler
    total_time_s: float              # kurulum + geçiş + kubbe
    total_fiber_length_mm: float
    peak_carriage_feed_mm_s: float   # en yüksek taşıyıcı hızı
    peak_spindle_rpm: float          # en yüksek iş mili RPM

    @property
    def total_time_min(self) -> float:
        return self.total_time_s / 60.0

    @property
    def total_fiber_length_m(self) -> float:
        return self.total_fiber_length_mm / 1000.0

    def summary(self) -> str:
        n_fam = len(self.family_estimates)
        return (
            f"ÜretimTahmini: {self.total_time_min:.1f}dk | "
            f"{n_fam} aile | "
            f"fiber={self.total_fiber_length_m:.1f}m | "
            f"peak_hız={self.peak_carriage_feed_mm_s:.0f}mm/s | "
            f"peak_RPM={self.peak_spindle_rpm:.0f}"
        )


# ── Yardımcı fonksiyonlar ─────────────────────────────────────────────────────

def _profile_arc_length(profile: MandrelProfile) -> float:
    """Profil meridiyen yay uzunluğu (mm) — tam mandrel boyunca."""
    z = profile.z_mm
    r = profile.r_mm
    dz = np.diff(z)
    dr = np.diff(r)
    return float(np.sum(np.sqrt(dz ** 2 + dr ** 2)))


def _effective_traverse_length(
    profile: MandrelProfile,
    strategy: str,
    alpha_deg: float,
    standoff_mm: float = 150.0,
) -> float:
    """
    Efektif taşıyıcı geçiş uzunluğu (ileri + geri, mm).

    Helisel/kutupsal: tam profil uzunluğu + lead payı × 2.
    Çevre: orta bölge ile sınırlı (hedef bölge).
    """
    L = float(profile.z_mm[-1] - profile.z_mm[0])
    alpha_rad = math.radians(max(alpha_deg, 0.5))
    lead = standoff_mm * math.tan(alpha_rad)

    if strategy == "hoop":
        # Çevre sarma sadece belirli bir bölgeyi kapsar; yaklaşım: L
        return 2.0 * L
    else:
        # Helisel/kutupsal: lead payı ekle (her iki uçta)
        return 2.0 * (L + 2.0 * lead)


def _effective_feed(
    nominal_feed_mm_s: float,
    spindle_rpm_max: float,
    alpha_deg: float,
    radius_mm: float,
) -> float:
    """
    İş mili RPM sınırlı efektif taşıyıcı ilerleme hızı.

    v_circ = v_feed × tan(α) ≤ r × ω_max
    → v_eff = min(v_feed, r × ω_max / tan(α))

    α → 0° (kutupsal): iş mili neredeyse duruyor, taşıyıcı sınırsız.
    α → 90° (çevre): iş mili çok hızlı dönmesi gerekiyor → sınırlı olabilir.
    """
    alpha_rad = math.radians(max(alpha_deg, 0.5))
    omega_max = spindle_rpm_max * 2.0 * math.pi / 60.0  # rad/s
    v_circ_max = radius_mm * omega_max  # mm/s
    v_carriage_max_from_spindle = v_circ_max / math.tan(alpha_rad)
    return min(nominal_feed_mm_s, v_carriage_max_from_spindle)


def _circuits_per_set(
    radius_mm: float,
    tow_width_mm: float,
    overlap_pct: float,
) -> int:
    """
    Bir kat seti için devre sayısı.

    n = ceil(2π·r / (W·(1 − overlap/100)))
    (FiberBand.circuits_for_coverage ile tutarlı formül)
    """
    step = tow_width_mm * max(1.0 - overlap_pct / 100.0, 0.01)
    circumference = 2.0 * math.pi * radius_mm
    return max(1, math.ceil(circumference / step))


def _fiber_length_per_set(
    profile: MandrelProfile,
    alpha_deg: float,
    n_circuits: int,
) -> float:
    """
    Bir kat seti için toplam fiber uzunluğu (mm).

    Silindir yaklaşımı: L_fiber_per_circuit = 2·L / cos(α)
    (geodezik eğri uzunluğu; kubbe katkısı dahil değil — küçük hata)
    """
    L = float(profile.z_mm[-1] - profile.z_mm[0])
    alpha_rad = math.radians(max(alpha_deg, 0.5))
    fiber_per_circuit = 2.0 * L / math.cos(alpha_rad)
    return n_circuits * fiber_per_circuit


# ── Ana tahmin fonksiyonu ─────────────────────────────────────────────────────

def estimate_cycle_time(
    schedule: LayerSchedule,
    profile: MandrelProfile,
    material: MaterialSpec,
    calib: Optional[MachineCalibration] = None,
    nominal_feed_mm_s: float = 80.0,
    spindle_rpm_max: float = 120.0,
    setup_time_s: float = 120.0,
    dome_overhead_per_family_s: float = 60.0,
) -> CycleBreakdown:
    """
    Bir sarma planı için üretim döngü süresi tahmin et.

    Parametreler
    ------------
    schedule              : Sarma planı.
    profile               : Mandrel geometrisi.
    material              : Malzeme (tow boyutları için).
    calib                 : Makine kalibrasyonu (None → varsayılan).
    nominal_feed_mm_s     : Taşıyıcı nominal hızı (mm/s).
    spindle_rpm_max       : İş mili maks. devri (RPM).
    setup_time_s          : Başlangıç + referans alma süresi (s).
    dome_overhead_per_family_s : Kubbe geçiş ailesi başına ek süre (s).
    """
    if calib is None:
        calib = default_calibration()

    tow = material.tow
    r_avg = float(np.mean(profile.r_mm))
    standoff = calib.eye_base_standoff_mm

    family_ests: List[FamilyEstimate] = []
    total_traverse_s = 0.0
    total_dome_s = 0.0
    total_fiber_mm = 0.0
    peak_feed = 0.0
    peak_rpm = 0.0

    for family in schedule.families:
        if family.n_layer_sets == 0:
            continue

        alpha = family.alpha_deg
        v_eff = _effective_feed(nominal_feed_mm_s, spindle_rpm_max, alpha, r_avg)
        is_limited = v_eff < nominal_feed_mm_s * 0.99

        n_circ = _circuits_per_set(r_avg, tow.tow_width_mm, family.overlap_pct)
        traverse_mm = _effective_traverse_length(profile, family.strategy, alpha, standoff)
        t_pass = traverse_mm / v_eff if v_eff > 0 else 1e9

        # Her kat seti için: n_circuits × t_pass (her devre ayrı geçiş)
        # Pratikte devreler ardışık: t_set ≈ n_circuits × t_pass
        t_per_set = n_circ * t_pass
        t_total_fam = family.n_layer_sets * t_per_set

        fiber_per_set = _fiber_length_per_set(profile, alpha, n_circ)
        fiber_total_fam = family.n_layer_sets * fiber_per_set

        # İş mili RPM kontrolü
        alpha_rad = math.radians(max(alpha, 0.5))
        v_circ = v_eff * math.tan(alpha_rad)
        rpm_actual = v_circ / (r_avg * 2.0 * math.pi / 60.0) if r_avg > 0 else 0.0

        family_ests.append(FamilyEstimate(
            alpha_deg=alpha,
            strategy=family.strategy,
            n_layer_sets=family.n_layer_sets,
            n_circuits_per_set=n_circ,
            traverse_length_mm=traverse_mm,
            effective_feed_mm_s=v_eff,
            time_per_set_s=t_per_set,
            total_time_s=t_total_fam,
            fiber_length_per_set_mm=fiber_per_set,
            total_fiber_length_mm=fiber_total_fam,
            is_spindle_limited=is_limited,
        ))

        total_traverse_s += t_total_fam
        total_fiber_mm += fiber_total_fam
        peak_feed = max(peak_feed, v_eff)
        peak_rpm = max(peak_rpm, rpm_actual)

        if family.is_dome_traversal:
            total_dome_s += dome_overhead_per_family_s * family.n_layer_sets

    total_time = setup_time_s + total_traverse_s + total_dome_s

    return CycleBreakdown(
        family_estimates=family_ests,
        setup_time_s=setup_time_s,
        dome_transition_overhead_s=total_dome_s,
        total_traverse_time_s=total_traverse_s,
        total_time_s=total_time,
        total_fiber_length_mm=total_fiber_mm,
        peak_carriage_feed_mm_s=peak_feed,
        peak_spindle_rpm=peak_rpm,
    )


def estimate_fiber_length(
    schedule: LayerSchedule,
    profile: MandrelProfile,
    material: MaterialSpec,
) -> float:
    """Toplam fiber uzunluğu tahmini (mm), hızlı versiyon."""
    tow = material.tow
    r_avg = float(np.mean(profile.r_mm))
    total = 0.0
    for family in schedule.families:
        if family.n_layer_sets == 0:
            continue
        n_circ = _circuits_per_set(r_avg, tow.tow_width_mm, family.overlap_pct)
        fiber_per_set = _fiber_length_per_set(profile, family.alpha_deg, n_circ)
        total += family.n_layer_sets * fiber_per_set
    return total
