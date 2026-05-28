"""
core/manufacturability.py — Üretilebilirlik Doğrulama Motoru
=============================================================
Tüm fizik katmanlarını birleştiren entegre üretilebilirlik raporu.

Değerlendirilen kriterler
--------------------------
1. Geodezik kalite  (Clairaut uyumu, kayma riski, kalkış bölgeleri)
2. Fiber bant fiziği (kaplama %, boşluk %, bindirme ısı haritası)
3. Payout kinematiği (göz açısı, çarpışma zarfı)
4. Makine hareket kısıtları (saturasyon, S-eğrisi fizibilite)
5. Genel sınır dışı kontrolleri (imkânsız açılar, taşıyıcı limitleri)
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Optional

from .fiber_band import FiberBand
from .coverage_solver import CoverageMap, GapRegion, solve_coverage
from .geodesic_validator import GeodesicValidationReport, validate_geodesic
from .geometry_engine import MandrelProfile
from .industrial_motion import (
    MotionConstraints, SaturationReport,
    SynchronizedSegment, analyze_saturation, plan_industrial_motion,
)
from .payout_kinematics import (
    PayoutEyeConfig, PayoutKinematicsReport, analyze_payout_kinematics,
)
from .path_generator import WindingPath


# ── Üretilebilirlik kriterleri ────────────────────────────────────────────────

@dataclass
class ManufacturabilityThresholds:
    """Üretilebilirlik için kabul edilebilir sınırlar."""
    min_coverage_pct: float = 90.0          # Min yüzey kaplama (%)
    max_gap_pct: float = 5.0                # Maks boşluk (%)
    max_overlap_pct: float = 30.0           # Maks bindirme (% yüzey)
    max_clairaut_error_pct: float = 2.0     # Clairaut hatası (%)
    max_slip_ratio: float = 1.0             # Kayma riski (> 1 = kayar)
    max_x_saturation_pct: float = 5.0      # Maks X saturasyon (%)
    max_a_saturation_pct: float = 5.0      # Maks A saturasyon (%)
    min_payout_valid_pct: float = 95.0     # Min geçerli payout (%)
    max_lift_off_zones: int = 0             # Kalkış bölgesi sayısı (0 = yok)


# ── Bireysel kriter sonuçları ────────────────────────────────────────────────

@dataclass
class CriterionResult:
    name: str
    passed: bool
    value: float
    threshold: float
    unit: str
    description: str

    def __str__(self) -> str:
        symbol = "✓" if self.passed else "✗"
        return (f"  [{symbol}] {self.name}: {self.value:.2f}{self.unit} "
                f"(sınır: {self.threshold:.2f}{self.unit}) — {self.description}")


# ── Ana rapor ─────────────────────────────────────────────────────────────────

@dataclass
class ManufacturabilityReport:
    """
    Entegre üretilebilirlik raporu.

    is_manufacturable = True ancak ve ancak tüm kritik kriterler geçildiğinde.
    """
    # Alt-raporlar
    geodesic: GeodesicValidationReport
    coverage_map: CoverageMap
    payout: PayoutKinematicsReport
    saturation: SaturationReport

    # Kriter sonuçları
    criteria: List[CriterionResult]

    # Genel karar
    is_manufacturable: bool
    critical_failures: List[str]
    warnings: List[str]

    # Özet istatistikler
    coverage_pct: float
    gap_pct: float
    overlap_pct: float
    clairaut_error_max_pct: float
    n_lift_off_zones: int
    n_slip_zones: int

    def summary(self) -> str:
        lines = []
        status = "ÜRETİLEBİLİR ★★★" if self.is_manufacturable else "DURUN — ÜRETİLEMEZ ✗"
        lines.append(f"\n{'='*60}")
        lines.append(f" Üretilebilirlik Raporu: {status}")
        lines.append(f"{'='*60}")

        lines.append(f"\n Kaplama       : %{self.coverage_pct:.1f}")
        lines.append(f" Boşluk        : %{self.gap_pct:.1f}")
        lines.append(f" Bindirme      : %{self.overlap_pct:.1f}")
        lines.append(f" Clairaut hatası: %{self.clairaut_error_max_pct:.3f}")
        lines.append(f" Kalkış bölgesi: {self.n_lift_off_zones}")
        lines.append(f" Kayma bölgesi : {self.n_slip_zones}")

        lines.append(f"\n Kriterler:")
        for c in self.criteria:
            lines.append(str(c))

        if self.critical_failures:
            lines.append(f"\n KRİTİK HATALAR:")
            for f in self.critical_failures:
                lines.append(f"  ✗ {f}")

        if self.warnings:
            lines.append(f"\n UYARILAR:")
            for w in self.warnings:
                lines.append(f"  ! {w}")

        lines.append(f"{'='*60}\n")
        return "\n".join(lines)


# ── Ana doğrulama işlevi ─────────────────────────────────────────────────────

def validate_manufacturability(
    path: WindingPath,
    band: FiberBand,
    profile: MandrelProfile,
    eye_config: Optional[PayoutEyeConfig] = None,
    constraints: Optional[MotionConstraints] = None,
    thresholds: Optional[ManufacturabilityThresholds] = None,
    friction_coeff: float = 0.3,
    alpha_min_deg: float = 5.0,
    alpha_max_deg: float = 89.0,
    n_z: int = 100,
    n_theta: int = 360,
) -> ManufacturabilityReport:
    """
    Tam üretilebilirlik doğrulaması gerçekleştir.

    Parametreler
    ----------
    path        : Doğrulanacak sarma yolu.
    band        : Fiber bant fizik parametreleri.
    profile     : Mandrel geometrisi.
    eye_config  : Payout gözü yapılandırması (None ise varsayılan kullanılır).
    constraints : Makine hareket kısıtları (None ise varsayılan kullanılır).
    thresholds  : Kabul kriterleri (None ise varsayılan kullanılır).
    friction_coeff : Fiber-mandrel sürtünme katsayısı.
    alpha_min_deg, alpha_max_deg : Geçerli sarma açısı aralığı.
    n_z, n_theta : Kaplama haritası ızgara boyutu.
    """
    if eye_config is None:
        eye_config = PayoutEyeConfig()
    if constraints is None:
        constraints = MotionConstraints()
    if thresholds is None:
        thresholds = ManufacturabilityThresholds()

    critical: List[str] = []
    warnings: List[str] = []

    # ── 1. Geodezik doğrulama ────────────────────────────────────────────────
    geodesic = validate_geodesic(
        path, profile, friction_coeff, alpha_min_deg, alpha_max_deg
    )
    critical.extend(geodesic.critical_issues)
    warnings.extend(geodesic.warnings)

    # ── 2. Kaplama analizi ───────────────────────────────────────────────────
    cmap = solve_coverage(path, band, profile, n_z, n_theta)
    cov_pct = cmap.coverage_pct
    gap_pct = cmap.gap_pct
    olap_pct = cmap.overlap_pct

    # ── 3. Payout kinematiği ─────────────────────────────────────────────────
    payout = analyze_payout_kinematics(path, profile, eye_config, sample_every=20)
    warnings.extend(payout.warnings)

    # ── 4. Endüstriyel hareket planlaması ────────────────────────────────────
    sync_segs = plan_industrial_motion(path, constraints)
    saturation = analyze_saturation(sync_segs, constraints)
    warnings.extend(saturation.issues)

    # ── 5. Kriter değerlendirmesi ────────────────────────────────────────────
    n_payout_pts = payout.n_points_analyzed
    payout_valid_pct = (payout.n_valid / max(n_payout_pts, 1)) * 100.0

    criteria: List[CriterionResult] = [
        CriterionResult(
            name="Yüzey Kaplama",
            passed=cov_pct >= thresholds.min_coverage_pct,
            value=cov_pct, threshold=thresholds.min_coverage_pct,
            unit="%",
            description="Kaplanan hücre yüzdesi (min gerekli)",
        ),
        CriterionResult(
            name="Boşluk Oranı",
            passed=gap_pct <= thresholds.max_gap_pct,
            value=gap_pct, threshold=thresholds.max_gap_pct,
            unit="%",
            description="Kaplanmayan yüzey (maks izin verilen)",
        ),
        CriterionResult(
            name="Bindirme Oranı",
            passed=olap_pct <= thresholds.max_overlap_pct,
            value=olap_pct, threshold=thresholds.max_overlap_pct,
            unit="%",
            description="2+ devre kapsayan hücre yüzdesi",
        ),
        CriterionResult(
            name="Clairaut Hatası",
            passed=geodesic.clairaut_error.max_pct <= thresholds.max_clairaut_error_pct,
            value=geodesic.clairaut_error.max_pct,
            threshold=thresholds.max_clairaut_error_pct,
            unit="%",
            description="Geodezik Clairaut sabitinden maksimum sapma",
        ),
        CriterionResult(
            name="Kalkış Bölgeleri",
            passed=len(geodesic.lift_off_zones) <= thresholds.max_lift_off_zones,
            value=float(len(geodesic.lift_off_zones)),
            threshold=float(thresholds.max_lift_off_zones),
            unit=" bölge",
            description="r(z) < Clairaut sabiti olan kalkış bölgesi sayısı",
        ),
        CriterionResult(
            name="X Saturasyon",
            passed=saturation.x_saturation_pct <= thresholds.max_x_saturation_pct,
            value=saturation.x_saturation_pct,
            threshold=thresholds.max_x_saturation_pct,
            unit="%",
            description="X ekseni hız sınırını aşan segment yüzdesi",
        ),
        CriterionResult(
            name="A Saturasyon",
            passed=saturation.a_saturation_pct <= thresholds.max_a_saturation_pct,
            value=saturation.a_saturation_pct,
            threshold=thresholds.max_a_saturation_pct,
            unit="%",
            description="A ekseni hız sınırını aşan segment yüzdesi",
        ),
        CriterionResult(
            name="Payout Geçerliliği",
            passed=payout_valid_pct >= thresholds.min_payout_valid_pct,
            value=payout_valid_pct,
            threshold=thresholds.min_payout_valid_pct,
            unit="%",
            description="Payout açısı sınır içinde olan nokta yüzdesi",
        ),
    ]

    # Kritik olmayan kriterler için uyarı; kritik olanlar için hata ekle
    for c in criteria:
        if not c.passed:
            msg = f"{c.name}: {c.value:.2f}{c.unit} (sınır: {c.threshold:.2f}{c.unit})"
            # Kaplama ve kalkış kritik; diğerleri uyarı
            if c.name in ("Yüzey Kaplama", "Kalkış Bölgeleri", "Clairaut Hatası"):
                critical.append(msg)
            else:
                warnings.append(msg)

    is_mfg = len(critical) == 0

    return ManufacturabilityReport(
        geodesic=geodesic,
        coverage_map=cmap,
        payout=payout,
        saturation=saturation,
        criteria=criteria,
        is_manufacturable=is_mfg,
        critical_failures=critical,
        warnings=warnings,
        coverage_pct=cov_pct,
        gap_pct=gap_pct,
        overlap_pct=olap_pct,
        clairaut_error_max_pct=geodesic.clairaut_error.max_pct,
        n_lift_off_zones=len(geodesic.lift_off_zones),
        n_slip_zones=len(geodesic.slip_risk_zones),
    )


# ── Hızlı kontrol yardımcıları ───────────────────────────────────────────────

def quick_feasibility_check(
    profile: MandrelProfile,
    alpha_deg: float,
    tow_width_mm: float,
    n_layers: int,
    friction_coeff: float = 0.3,
) -> List[str]:
    """
    Tam yol hesaplamadan önce hızlı fizibilite kontrolü.

    Döner: Kritik sorunlar listesi (boşsa ilerlenebilir).
    """
    issues: List[str] = []

    # Clairaut sabiti
    r_max = profile.max_radius_mm
    r_min = float(profile.r_mm.min())
    r_avg = profile.avg_radius_mm

    alpha_rad = math.radians(alpha_deg)
    c = r_avg * math.sin(alpha_rad)

    # Kalkış kontrolü: r_min >= c gerekli
    if r_min < c * 0.99:
        issues.append(
            f"Kalkış riski: r_min={r_min:.1f}mm < Clairaut c={c:.1f}mm "
            f"(α={alpha_deg:.1f}° için)"
        )

    # Kayma kontrolü: tan(α) < μ gerekli (en azından ortalama için)
    if math.tan(alpha_rad) > friction_coeff:
        issues.append(
            f"Kayma riski: tan({alpha_deg:.1f}°)={math.tan(alpha_rad):.3f} "
            f"> μ={friction_coeff:.2f}"
        )

    # Açı fizibilite: α hoop için 88° sınırı, polar için 5° sınırı
    if alpha_deg < 5.0:
        issues.append(f"Sarma açısı çok küçük: {alpha_deg:.1f}° < 5°")
    if alpha_deg > 88.0:
        issues.append(f"Sarma açısı çok büyük: {alpha_deg:.1f}° > 88°")

    # Fitil genişliği kontrolü
    if tow_width_mm > 2.0 * math.pi * r_min:
        issues.append(
            f"Fitil genişliği ({tow_width_mm:.1f}mm) mandrel çevresinden büyük "
            f"({2*math.pi*r_min:.1f}mm @ r_min)"
        )

    # Toplam katman kalınlığı kontrolü (yarıçapın %20'si ile sınırlı)
    max_wall_mm = r_min * 0.20
    estimated_wall = 0.25 * 0.85 * n_layers  # 0.25mm * 0.85 kompaksiyon
    if estimated_wall > max_wall_mm:
        issues.append(
            f"Tahmini duvar kalınlığı ({estimated_wall:.2f}mm) çok fazla "
            f"(r_min'in %20'si = {max_wall_mm:.2f}mm)"
        )

    return issues
