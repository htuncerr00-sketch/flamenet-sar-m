"""
core/recipe_optimizer.py — Otomatik Sarma Reçete Optimize Edici
================================================================
Kullanıcı hedeflerinden (mandrel geometrisi + hedef et kalınlığı +
fiber + makine kısıtlamaları) optimal sarma reçetesini otomatik olarak
belirler.

Matematiksel Model
------------------
Tasarım değişkenleri:
    {(α_i, n_i)} — açı aileleri ve kat sayıları (tamsayı)

Kalınlık kısıtı:
    Σ_i n_i × t_ply_i ≈ T_hedef   (∓ tolerans)

Geodezik kısıt:
    α_i ≥ arcsin(r_min / r_cyl)  için tüm i (geodezik sarma)
    (non-geodezik mod: kısıt gevşetilir, uyarı üretilir)

İş mili kısıtı:
    v_çevresel(α) ≤ r × ω_max

Amaç fonksiyonları (normalize [0,1], düşük = iyi):
    f_zaman     = döngü_süresi / süre_bütçesi
    f_kalınlık  = |T_elde − T_hedef| / T_hedef
    f_maliyet   = toplam_maliyet / maliyet_bütçesi
    f_tekdüzelik = 1 − kapsama_tekdüzeliği_skoru
    f_üret      = 1 − üretilebilirlik_skoru

Birleşik skor = Σ_k w_k × f_k   (minimize)

Arama Stratejisi
----------------
1. Uygulanabilir açıların üretilmesi (geodezik + makine kısıtları)
2. Aile alt kümelerinin seçimi (1-4 aile kombinasyonu)
3. Kat sayıları için tamsayı ızgara araması
   N_maks = ceil(T* / t_min) + PAY (tipik: ≤ 20 kat/aile)
4. Kalınlık filtresi: T* × 0.85 ≤ T_elde ≤ T* × 1.20
5. Skor hesabı ve Pareto sıralaması
6. En iyi k çözüm döndürme

Arama uzayı boyutu (3 aile, N=15): 15³ = 3375 — < 1 ms.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple

import numpy as np

from .cost_estimator import CostBreakdown, ProductionRates, estimate_cost
from .geometry_engine import MandrelProfile
from .laminate_builder import AngleFamily, LayerSchedule, build_laminate
from .machine_calibration import MachineCalibration, default_calibration
from .material_database import MaterialSpec, carbon_t700_standard_epoxy
from .production_estimator import CycleBreakdown, estimate_cycle_time
from .thickness_predictor import (
    evaluate_thickness_error,
    predict_coverage_pct,
    predict_cylinder_thickness,
)


# ══════════════════════════════════════════════════════════════════════════════
# Girdi ve Kısıt Tanımları
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class RecipeConstraints:
    """
    Optimizasyon kısıtları.

    target_thickness_mm   : Hedef et kalınlığı (mm) — ana kısıt.
    thickness_tol_pct     : Kalınlık toleransı (% — varsayılan ±15%).
    max_cycle_time_s      : Maks. izin verilen üretim süresi (s).
    max_cost_usd          : Maks. izin verilen maliyet (USD; 0 = kısıtsız).
    min_coverage_pct      : Min. yüzey kapsama oranı (%).
    alpha_min_deg         : Min. makine sarma açısı (makine limiti).
    alpha_max_deg         : Maks. makine sarma açısı.
    spindle_rpm_max       : Maks. iş mili devri (RPM).
    feed_mm_s             : Taşıyıcı nominal ilerleme hızı (mm/s).
    allow_non_geodesic    : Geodezik olmayan açılara izin ver (varsayılan False).
    max_families          : Aile sayısı üst sınırı (1-4).
    max_layer_sets_per_family : Aile başına maks. kat seti sayısı.
    """
    target_thickness_mm: float
    thickness_tol_pct: float = 15.0
    max_cycle_time_s: float = 7200.0    # 2 saat
    max_cost_usd: float = 0.0           # 0 = kısıtsız
    min_coverage_pct: float = 95.0
    alpha_min_deg: float = 5.0
    alpha_max_deg: float = 88.5
    spindle_rpm_max: float = 120.0
    feed_mm_s: float = 80.0
    allow_non_geodesic: bool = False
    max_families: int = 3
    max_layer_sets_per_family: int = 12

    def __post_init__(self) -> None:
        if self.target_thickness_mm <= 0:
            raise ValueError(f"target_thickness_mm > 0: {self.target_thickness_mm}")
        if not (0 < self.thickness_tol_pct <= 50):
            raise ValueError(f"thickness_tol_pct ∈ (0,50]: {self.thickness_tol_pct}")
        if self.max_families < 1 or self.max_families > 4:
            raise ValueError(f"max_families ∈ [1,4]: {self.max_families}")


@dataclass
class RecipeObjective:
    """
    Çok-amaçlı optimizasyon ağırlıkları.

    Tüm ağırlıklar normalize edilir (toplamları 1'e ölçeklenir).
    w = 0 → ilgili amaç göz ardı edilir.
    """
    w_time: float = 0.30         # döngü süresi minimizasyonu
    w_thickness: float = 0.40    # kalınlık hedefine yakınlık
    w_coverage: float = 0.15     # kapsama tekdüzeliği
    w_cost: float = 0.10         # maliyet minimizasyonu
    w_manufacturability: float = 0.05  # üretilebilirlik

    def normalized(self) -> "RecipeObjective":
        total = self.w_time + self.w_thickness + self.w_coverage + self.w_cost + self.w_manufacturability
        if total < 1e-9:
            return RecipeObjective()
        f = 1.0 / total
        return RecipeObjective(
            w_time=self.w_time * f,
            w_thickness=self.w_thickness * f,
            w_coverage=self.w_coverage * f,
            w_cost=self.w_cost * f,
            w_manufacturability=self.w_manufacturability * f,
        )


@dataclass
class RecipeInput:
    """
    Optimize edici girdi parametreleri.

    profile      : Mandrel geometrisi.
    material     : Fiber + reçine + tow özellikleri.
    constraints  : Kısıtlar (kalınlık hedefi dahil).
    objective    : Amaç ağırlıkları.
    calib        : Makine kalibrasyonu (None → varsayılan).
    rates        : Üretim ücretleri (None → varsayılan).
    overlap_pct  : Tüm aileler için örtüşme yüzdesi.
    """
    profile: MandrelProfile
    material: MaterialSpec
    constraints: RecipeConstraints
    objective: RecipeObjective = field(default_factory=RecipeObjective)
    calib: Optional[MachineCalibration] = None
    rates: Optional[ProductionRates] = None
    overlap_pct: float = 5.0


# ══════════════════════════════════════════════════════════════════════════════
# Çıktı Tanımları
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class FeasibilityAnalysis:
    """Bir açı için uygulanabilirlik analizi."""
    alpha_deg: float
    strategy: str
    geodesic_min_deg: float
    is_geodesic_feasible: bool
    is_machine_feasible: bool          # açı makine limitlerinde mi?
    is_spindle_feasible: bool          # RPM limitinde mi?
    effective_feed_mm_s: float
    is_feasible: bool
    issues: List[str] = field(default_factory=list)


@dataclass
class RecipeScore:
    """Bir reçetenin çok-amaçlı skor bileşenleri."""
    f_time: float          # normalize zaman skoru [0,1]
    f_thickness: float     # normalize kalınlık hatası [0,1]
    f_coverage: float      # normalize kapsama skoru [0,1]
    f_cost: float          # normalize maliyet skoru [0,1]
    f_manufacturability: float  # normalize üretilebilirlik [0,1]
    combined: float        # ağırlıklı birleşim (düşük = iyi)


@dataclass
class OptimizedRecipe:
    """
    Optimize edilmiş sarma reçetesi — tam çıktı.

    schedule     : Önerilen açı ailesi + kat sayısı planı.
    score        : Çok-amaçlı skor kırılımı.
    cycle        : Üretim süresi tahmini.
    cost         : Maliyet tahmini.
    achieved_thickness_mm : Öngörülen et kalınlığı.
    coverage_pct : Öngörülen yüzey kapsama oranı.
    manufacturability_score : Üretilebilirlik skoru [0,1].
    warnings     : Risk / dikkat mesajları.
    """
    schedule: LayerSchedule
    score: RecipeScore
    cycle: CycleBreakdown
    cost: CostBreakdown
    achieved_thickness_mm: float
    coverage_pct: float
    manufacturability_score: float
    warnings: List[str]
    rank: int = 0

    def summary(self) -> str:
        return (
            f"[#{self.rank}] {self.schedule.angle_summary} | "
            f"t={self.achieved_thickness_mm:.3f}mm | "
            f"süre={self.cycle.total_time_min:.1f}dk | "
            f"maliyet={self.cost.total_cost_usd:.2f}USD | "
            f"kapsama={self.coverage_pct:.1f}% | "
            f"skor={self.score.combined:.4f}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Yardımcı Hesaplamalar
# ══════════════════════════════════════════════════════════════════════════════

def _geodesic_min_angle(profile: MandrelProfile) -> float:
    """
    Geodezik sarma için minimum açı (derece).

    Clairaut: c = r·sin(α) = sabit.
    Kubbe dönüş noktasında: c = r_min (α=90°).
    Silindir bölgesinde: r_cyl·sin(α_min) = r_min → α_min = arcsin(r_min/r_cyl).

    Saf silindir profili (r_min ≈ r_cyl): her α geodezik olarak sarılabilir
    (helisin silindire aç-kapat gösterimi düz çizgidir) → kısıt yok → 0°.
    Kubbeli profil: r_min << r_cyl → α_min > 0°.
    """
    r_cyl = float(np.max(profile.r_mm))
    r_positive = profile.r_mm[profile.r_mm > 1.0]  # çok küçük değerleri dışla
    if len(r_positive) == 0 or r_cyl < 1e-3:
        return 0.0
    r_min = float(np.min(r_positive))
    ratio = r_min / r_cyl
    ratio = min(ratio, 1.0)
    # Saf silindir: r_min ≈ r_cyl → her açı geodezik → kısıt yok
    if ratio > 0.99:
        return 0.0
    return math.degrees(math.asin(ratio))


def _effective_feed_at_alpha(
    alpha_deg: float,
    nominal_feed: float,
    spindle_rpm_max: float,
    r_avg: float,
) -> float:
    """İş mili RPM sınırlı efektif taşıyıcı hızı."""
    alpha_rad = math.radians(max(alpha_deg, 0.5))
    omega_max = spindle_rpm_max * 2.0 * math.pi / 60.0
    v_circ_max = r_avg * omega_max
    v_from_spindle = v_circ_max / math.tan(alpha_rad)
    return min(nominal_feed, v_from_spindle)


def _classify_strategy(alpha_deg: float) -> str:
    if alpha_deg <= 20.0:
        return "polar"
    elif alpha_deg >= 75.0:
        return "hoop"
    return "helical"


def _per_ply_thickness(
    material: MaterialSpec,
    layer_idx: int = 0,
    tension_N: float = 50.0,
    radius_mm: float = 50.0,
) -> float:
    """Tek bir katın sıkıştırılmış kalınlığı."""
    from .fiber_deposition import compaction_factor
    cf = compaction_factor(layer_idx, tension_N, radius_mm)
    return material.tow.tow_thickness_mm * cf


def _check_feasibility(
    alpha_deg: float,
    constraints: RecipeConstraints,
    geodesic_min: float,
    r_avg: float,
) -> FeasibilityAnalysis:
    """Bir açı için uygulanabilirlik analizi."""
    strategy = _classify_strategy(alpha_deg)
    issues: List[str] = []

    geo_ok = alpha_deg >= geodesic_min
    if not geo_ok:
        if not constraints.allow_non_geodesic:
            issues.append(f"α={alpha_deg:.0f}° < geodezik_min={geodesic_min:.1f}°")

    machine_ok = constraints.alpha_min_deg <= alpha_deg <= constraints.alpha_max_deg
    if not machine_ok:
        issues.append(f"α={alpha_deg:.0f}° makine limitlari dışında [{constraints.alpha_min_deg:.0f}°, {constraints.alpha_max_deg:.0f}°]")

    v_eff = _effective_feed_at_alpha(alpha_deg, constraints.feed_mm_s,
                                      constraints.spindle_rpm_max, r_avg)
    spindle_ok = v_eff >= constraints.feed_mm_s * 0.10  # %10'un altına düşerse sorun
    if not spindle_ok:
        issues.append(f"α={alpha_deg:.0f}° iş mili sınırı: v_eff={v_eff:.1f}mm/s")

    geo_eff = geo_ok or constraints.allow_non_geodesic
    is_feasible = geo_eff and machine_ok and spindle_ok

    return FeasibilityAnalysis(
        alpha_deg=alpha_deg,
        strategy=strategy,
        geodesic_min_deg=geodesic_min,
        is_geodesic_feasible=geo_ok,
        is_machine_feasible=machine_ok,
        is_spindle_feasible=spindle_ok,
        effective_feed_mm_s=v_eff,
        is_feasible=is_feasible,
        issues=issues,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Aile Üretimi
# ══════════════════════════════════════════════════════════════════════════════

_CANDIDATE_ANGLES = [5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0, 45.0,
                     50.0, 55.0, 60.0, 65.0, 70.0, 75.0, 80.0, 85.0, 88.0]


def generate_feasible_families(
    inp: RecipeInput,
) -> List[FeasibilityAnalysis]:
    """
    Makine ve geodezik kısıtlara göre uygulanabilir açı ailelerini üret.

    Döndürülen liste uygulanabilir açıların analizini içerir.
    """
    r_avg = float(np.mean(inp.profile.r_mm))
    geodesic_min = _geodesic_min_angle(inp.profile)
    c = inp.constraints

    feasible = []
    for alpha in _CANDIDATE_ANGLES:
        if alpha < c.alpha_min_deg - 0.5 or alpha > c.alpha_max_deg + 0.5:
            continue
        fa = _check_feasibility(alpha, c, geodesic_min, r_avg)
        if fa.is_feasible:
            feasible.append(fa)
    return feasible


# ══════════════════════════════════════════════════════════════════════════════
# Kat Sayısı Araması
# ══════════════════════════════════════════════════════════════════════════════

def _layer_count_bounds(
    inp: RecipeInput,
    n_families: int,
    t_ply_avg: float,
) -> range:
    """
    Her aile için aranacak kat sayısı aralığı.

    N_max = ceil(T* / t_ply_min) + PAY (simetrik dağılım için)
    En az 1 kat seti, en fazla constraints.max_layer_sets_per_family.
    """
    t_target = inp.constraints.target_thickness_mm
    if t_ply_avg < 1e-6:
        return range(0, 2)
    n_max_theory = math.ceil(t_target / (t_ply_avg * max(n_families, 1))) + 4
    n_max = min(n_max_theory, inp.constraints.max_layer_sets_per_family)
    return range(0, n_max + 1)


def _enumerate_layer_counts(
    families: List[FeasibilityAnalysis],
    inp: RecipeInput,
) -> Iterator[Tuple[int, ...]]:
    """
    Seçilen aile setinin olası tamsayı kat sayısı kombinasyonlarını ver.

    Filtre: en az bir ailenin kat sayısı > 0.
    """
    if not families:
        return

    r_avg = float(np.mean(inp.profile.r_mm))
    tension = 50.0
    t_plies = [_per_ply_thickness(inp.material, i, tension, r_avg)
               for i, _ in enumerate(families)]
    t_avg = max(np.mean(t_plies) if t_plies else 0.01, 0.001)

    bounds = _layer_count_bounds(inp, len(families), t_avg)

    for combo in itertools.product(bounds, repeat=len(families)):
        if sum(combo) == 0:
            continue
        yield combo


# ══════════════════════════════════════════════════════════════════════════════
# Skor Hesabı
# ══════════════════════════════════════════════════════════════════════════════

def _manufacturability_score(
    schedule: LayerSchedule,
    inp: RecipeInput,
    geodesic_min: float,
    coverage: float,
) -> Tuple[float, List[str]]:
    """
    Üretilebilirlik skoru [0,1] ve uyarı listesi.

    1.0 = tamamen üretilebilir.
    """
    score = 1.0
    warnings: List[str] = []
    c = inp.constraints

    for family in schedule.families:
        # Geodezik kontrol
        if family.alpha_deg < geodesic_min - 0.5 and not c.allow_non_geodesic:
            score -= 0.3
            warnings.append(f"α={family.alpha_deg:.0f}° geodezik minimumun altında ({geodesic_min:.1f}°)")

        # Makine limit kontrol
        if family.alpha_deg < c.alpha_min_deg - 0.5:
            score -= 0.2
            warnings.append(f"α={family.alpha_deg:.0f}° makine min açısının altında")
        if family.alpha_deg > c.alpha_max_deg + 0.5:
            score -= 0.2
            warnings.append(f"α={family.alpha_deg:.0f}° makine maks açısının üstünde")

    # Kapsama kontrolü
    if coverage < c.min_coverage_pct:
        deficit = (c.min_coverage_pct - coverage) / 100.0
        score -= 0.4 * deficit
        warnings.append(f"Kapsama {coverage:.1f}% < hedef {c.min_coverage_pct:.0f}%")

    return max(0.0, score), warnings


def _compute_score(
    schedule: LayerSchedule,
    inp: RecipeInput,
    cycle: CycleBreakdown,
    cost_bd: CostBreakdown,
    achieved_t: float,
    coverage: float,
    geodesic_min: float,
    obj: RecipeObjective,
) -> Tuple[RecipeScore, float, List[str]]:
    """Normalize çok-amaçlı skor hesapla."""
    c = inp.constraints
    T = c.target_thickness_mm

    # ── Kalınlık skoru ────────────────────────────────────────────────────
    f_t = min(1.0, abs(achieved_t - T) / max(T, 1e-6))

    # ── Zaman skoru ───────────────────────────────────────────────────────
    f_time = min(1.0, cycle.total_time_s / max(c.max_cycle_time_s, 1.0))

    # ── Kapsama skoru ─────────────────────────────────────────────────────
    f_cov = max(0.0, 1.0 - coverage / 100.0)

    # ── Maliyet skoru ─────────────────────────────────────────────────────
    if c.max_cost_usd > 0:
        f_cost = min(1.0, cost_bd.total_cost_usd / c.max_cost_usd)
    else:
        # Kısıtsız: göreceli skor (referans: basit tek-aileli tahmini bütçe)
        ref_cost = cost_bd.total_cost_usd * 2.0 if cost_bd.total_cost_usd > 0 else 1.0
        f_cost = min(1.0, cost_bd.total_cost_usd / ref_cost)

    # ── Üretilebilirlik ───────────────────────────────────────────────────
    mfg_score, warnings = _manufacturability_score(schedule, inp, geodesic_min, coverage)
    f_mfg = 1.0 - mfg_score

    combined = (obj.w_thickness * f_t + obj.w_time * f_time +
                obj.w_coverage * f_cov + obj.w_cost * f_cost +
                obj.w_manufacturability * f_mfg)

    return (RecipeScore(
                f_time=f_time,
                f_thickness=f_t,
                f_coverage=f_cov,
                f_cost=f_cost,
                f_manufacturability=f_mfg,
                combined=combined,
            ), mfg_score, warnings)


# ══════════════════════════════════════════════════════════════════════════════
# Ana Optimize Edici
# ══════════════════════════════════════════════════════════════════════════════

def optimize_recipe(
    inp: RecipeInput,
    top_k: int = 5,
) -> List[OptimizedRecipe]:
    """
    Otomatik sarma reçetesi optimizasyonu.

    Parametreler
    ------------
    inp    : RecipeInput — mandrel, malzeme, kısıtlar, amaçlar.
    top_k  : Döndürülecek en iyi reçete sayısı.

    Döner
    -----
    OptimizedRecipe listesi, birleşik skora göre sıralı (en iyi = ilk).
    Hiç uygulanabilir reçete bulunamazsa boş liste döner.
    """
    if inp.calib is None:
        inp = RecipeInput(
            profile=inp.profile,
            material=inp.material,
            constraints=inp.constraints,
            objective=inp.objective,
            calib=default_calibration(),
            rates=inp.rates,
            overlap_pct=inp.overlap_pct,
        )

    obj = inp.objective.normalized()
    c = inp.constraints
    profile = inp.profile
    material = inp.material
    r_avg = float(np.mean(profile.r_mm))
    geodesic_min = _geodesic_min_angle(profile)
    T_target = c.target_thickness_mm
    tol = c.thickness_tol_pct / 100.0

    # ── 1. Uygulanabilir açıları üret ──────────────────────────────────
    feasible_families = generate_feasible_families(inp)
    if not feasible_families:
        return []

    # ── 2. Aile alt kümelerini oluştur (1..max_families) ───────────────
    candidates: List[OptimizedRecipe] = []

    max_fam = min(c.max_families, len(feasible_families))
    for n_fam in range(1, max_fam + 1):
        for family_subset in itertools.combinations(feasible_families, n_fam):
            # ── 3. Kat sayısı ızgara araması ──────────────────────────
            for combo in _enumerate_layer_counts(list(family_subset), inp):
                # Tüm sıfırsa atla (zaten _enumerate'da filtrelendi ama ek güvenlik)
                if all(n == 0 for n in combo):
                    continue

                # LayerSchedule oluştur
                fam_objs = [
                    AngleFamily(
                        alpha_deg=fa.alpha_deg,
                        n_layer_sets=n,
                        strategy=fa.strategy,
                        symmetric=(fa.strategy != "hoop"),
                        overlap_pct=inp.overlap_pct,
                    )
                    for fa, n in zip(family_subset, combo)
                    if n > 0
                ]
                if not fam_objs:
                    continue

                sched = LayerSchedule(families=fam_objs)

                # ── Kalınlık kontrolü ─────────────────────────────────
                achieved_t = predict_cylinder_thickness(sched, material, r_avg)
                err_frac = abs(achieved_t - T_target) / max(T_target, 1e-6)
                if err_frac > tol * 1.5:  # biraz daha geniş (arama uzayı)
                    continue

                # ── Döngü süresi ──────────────────────────────────────
                cycle = estimate_cycle_time(
                    sched, profile, material, inp.calib,
                    nominal_feed_mm_s=c.feed_mm_s,
                    spindle_rpm_max=c.spindle_rpm_max,
                )

                if cycle.total_time_s > c.max_cycle_time_s * 1.5:
                    continue

                # ── Maliyet ───────────────────────────────────────────
                cost_bd = estimate_cost(sched, profile, material, cycle, inp.rates)

                # ── Kapsama ───────────────────────────────────────────
                coverage = predict_coverage_pct(sched, profile)

                # ── Skor ──────────────────────────────────────────────
                score, mfg_score, warns = _compute_score(
                    sched, inp, cycle, cost_bd, achieved_t,
                    coverage, geodesic_min, obj,
                )

                # Kalınlık tolerans filtresi (kesin)
                if err_frac > tol:
                    continue

                candidates.append(OptimizedRecipe(
                    schedule=sched,
                    score=score,
                    cycle=cycle,
                    cost=cost_bd,
                    achieved_thickness_mm=achieved_t,
                    coverage_pct=coverage,
                    manufacturability_score=mfg_score,
                    warnings=warns,
                ))

    # ── 4. Sıralama ve top-k seçimi ────────────────────────────────────
    candidates.sort(key=lambda r: r.score.combined)

    # Yinelenen planları filtrele (aynı açı ailesi + kat sayısı)
    seen: set = set()
    unique: List[OptimizedRecipe] = []
    for rec in candidates:
        key = tuple(sorted((f.alpha_deg, f.n_layer_sets, f.strategy) for f in rec.schedule.families))
        if key not in seen:
            seen.add(key)
            unique.append(rec)

    for i, rec in enumerate(unique[:top_k]):
        rec.rank = i + 1

    return unique[:top_k]


# ══════════════════════════════════════════════════════════════════════════════
# Hızlı API
# ══════════════════════════════════════════════════════════════════════════════

def quick_optimize(
    profile: MandrelProfile,
    target_thickness_mm: float,
    material: Optional[MaterialSpec] = None,
    feed_mm_s: float = 80.0,
    spindle_rpm_max: float = 120.0,
    max_cycle_time_s: float = 7200.0,
    max_families: int = 3,
    top_k: int = 3,
) -> List[OptimizedRecipe]:
    """
    Minimum parametre ile hızlı reçete optimizasyonu.

    Varsayılan malzeme: T700S/Epoksi.
    """
    if material is None:
        material = carbon_t700_standard_epoxy()

    constraints = RecipeConstraints(
        target_thickness_mm=target_thickness_mm,
        feed_mm_s=feed_mm_s,
        spindle_rpm_max=spindle_rpm_max,
        max_cycle_time_s=max_cycle_time_s,
        max_families=max_families,
    )
    inp = RecipeInput(profile=profile, material=material, constraints=constraints)
    return optimize_recipe(inp, top_k=top_k)
