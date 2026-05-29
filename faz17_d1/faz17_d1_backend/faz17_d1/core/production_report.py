"""
core/production_report.py — Endüstriyel Üretim Raporu
======================================================
Tüm doğrulama ve tahmin katmanlarını tek bir üretim raporunda birleştirir:

- Üretilebilirlik raporu      (manufacturability)
- Kaplama raporu              (fiber_deposition / coverage)
- Çarpışma raporu             (payout_kinematics çarpışma zarfı)
- Makine limiti raporu        (machine_envelope)
- Tahmini üretim süresi       (industrial_motion zamanlaması)
- Malzeme tüketim tahmini     (process_parameters)
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Optional

from .fiber_band import FiberBand
from .fiber_deposition import DepositionMap, simulate_deposition
from .geometry_engine import MandrelProfile
from .industrial_motion import (
    MotionConstraints, SaturationReport, analyze_saturation, plan_industrial_motion,
)
from .machine_envelope import MachineEnvelope, MachineLimitReport, check_path_envelope
from .manufacturability import ManufacturabilityReport, validate_manufacturability
from .path_generator import WindingPath
from .payout_kinematics import (
    PayoutEyeConfig, PayoutKinematicsReport, analyze_payout_kinematics,
)
from .process_parameters import ProcessParameters


@dataclass
class MaterialEstimate:
    """Malzeme tüketim tahmini."""
    fiber_length_mm: float
    fiber_mass_g: float
    resin_mass_g: float
    total_mass_g: float
    composite_volume_cm3: float
    band_areal_weight_g_m2: float

    def summary(self) -> str:
        return (
            f"Malzeme: fiber={self.fiber_length_mm/1000:.1f}m "
            f"({self.fiber_mass_g:.1f}g) + reçine={self.resin_mass_g:.1f}g "
            f"= {self.total_mass_g:.1f}g | hacim={self.composite_volume_cm3:.1f}cm³ "
            f"| FAW={self.band_areal_weight_g_m2:.0f}g/m²"
        )


@dataclass
class TimeEstimate:
    """Üretim süresi tahmini."""
    motion_time_s: float           # Net hareket süresi (S-eğrisi zamanlama)
    setup_time_s: float            # Kurulum/ön ısıtma payı
    total_time_s: float

    @property
    def total_time_min(self) -> float:
        return self.total_time_s / 60.0

    def summary(self) -> str:
        return (
            f"Süre: hareket={self.motion_time_s:.1f}s + kurulum={self.setup_time_s:.0f}s "
            f"= {self.total_time_s:.1f}s ({self.total_time_min:.1f}dk)"
        )


@dataclass
class ProductionReport:
    """Birleşik endüstriyel üretim raporu."""
    manufacturability: ManufacturabilityReport
    deposition: DepositionMap
    payout: PayoutKinematicsReport
    machine_limits: MachineLimitReport
    saturation: SaturationReport
    material: MaterialEstimate
    time_estimate: TimeEstimate

    is_production_ready: bool
    blocking_issues: List[str]
    warnings: List[str]

    def verdict(self) -> str:
        """Üretim kararı: READY veya STOP."""
        if self.is_production_ready:
            return "★★★ ÜRETİME HAZIR ★★★"
        return "DURUN — ÜRETİLEMEZ"

    def summary(self) -> str:
        lines = [
            f"\n{'='*64}",
            f" ÜRETİM RAPORU: {self.verdict()}",
            f"{'='*64}",
            f" {self.material.summary()}",
            f" {self.time_estimate.summary()}",
            f" Kaplama        : %{self.deposition.coverage_pct:.1f} "
            f"(kaplanmamış %{self.deposition.uncovered_pct:.1f})",
            f" Kalınlık       : ort={self.deposition.mean_thickness_mm:.3f}mm "
            f"maks={self.deposition.max_thickness_mm:.3f}mm "
            f"tekdüzelik={self.deposition.thickness_uniformity():.2f}",
            f" {self.machine_limits.summary()}",
            f" {self.payout.summary()}",
            f" {self.saturation.summary()}",
        ]
        if self.blocking_issues:
            lines.append("\n ENGELLEYİCİ SORUNLAR:")
            lines.extend(f"  ✗ {b}" for b in self.blocking_issues)
        if self.warnings:
            lines.append("\n UYARILAR:")
            lines.extend(f"  ! {w}" for w in self.warnings[:10])
        lines.append(f"{'='*64}\n")
        return "\n".join(lines)


def generate_production_report(
    path: WindingPath,
    band: FiberBand,
    profile: MandrelProfile,
    process: Optional[ProcessParameters] = None,
    machine: Optional[MachineEnvelope] = None,
    eye_config: Optional[PayoutEyeConfig] = None,
    setup_time_s: float = 60.0,
    friction_coeff: float = 0.3,
    n_z: int = 100,
    n_theta: int = 240,
) -> ProductionReport:
    """
    Bir sarma yolu için tam üretim raporu üret.

    Parametreler
    ----------
    path     : Değerlendirilecek sarma yolu.
    band     : Fiber bant fiziği.
    profile  : Mandrel geometrisi.
    process  : Proses/malzeme parametreleri (None ise varsayılan).
    machine  : Makine zarfı (None ise varsayılan).
    eye_config : Payout gözü geometrisi (None ise varsayılan).
    setup_time_s : Kurulum süresi payı.
    """
    if process is None:
        process = ProcessParameters()
    if machine is None:
        machine = MachineEnvelope()
    if eye_config is None:
        eye_config = PayoutEyeConfig(standoff_mm=150.0)

    blocking: List[str] = []
    warnings: List[str] = []

    # ── Üretilebilirlik ──────────────────────────────────────────────────────
    constraints = MotionConstraints(
        max_x_speed_mm_s=machine.max_carriage_speed_mm_s,
        max_x_accel_mm_s2=machine.max_carriage_accel_mm_s2,
        max_a_speed_deg_s=machine.max_spindle_deg_s,
        ref_radius_mm=profile.avg_radius_mm,
    )
    mfg = validate_manufacturability(
        path, band, profile, eye_config, constraints,
        friction_coeff=friction_coeff, n_z=n_z, n_theta=n_theta,
    )
    blocking.extend(mfg.critical_failures)
    warnings.extend(mfg.warnings)

    # ── Yatırma haritası ─────────────────────────────────────────────────────
    deposition = simulate_deposition([path], band, profile, n_z, n_theta)

    # ── Payout / çarpışma ────────────────────────────────────────────────────
    payout = analyze_payout_kinematics(path, profile, eye_config, sample_every=20)
    n_coll = sum(1 for c in payout.collision_results if c.is_collision)
    if n_coll > 0:
        blocking.append(f"{n_coll} noktada payout-mandrel çarpışması")

    # ── Makine limitleri ─────────────────────────────────────────────────────
    machine_rpt = check_path_envelope(
        path, machine, profile, eye_standoff_mm=eye_config.standoff_mm
    )
    if machine_rpt.hard_limit_count > 0:
        blocking.append(f"{machine_rpt.hard_limit_count} sert eksen limiti ihlali")
    if machine_rpt.eye_workspace_count > 0:
        blocking.append(f"{machine_rpt.eye_workspace_count} göz çalışma alanı ihlali")
    if machine_rpt.soft_limit_count > 0:
        warnings.append(f"{machine_rpt.soft_limit_count} yumuşak limit ihlali")

    # ── Hareket zamanlaması / saturasyon ─────────────────────────────────────
    sync_segs = plan_industrial_motion(path, constraints)
    saturation = analyze_saturation(sync_segs, constraints)
    motion_time = sum(s.t_duration_s for s in sync_segs)
    if not saturation.is_feasible:
        warnings.append("Eksen saturasyonu: planlanan hız makine limitini aşıyor")

    # ── Malzeme tahmini ──────────────────────────────────────────────────────
    fiber_len = path.total_fiber_length_mm
    material = MaterialEstimate(
        fiber_length_mm=fiber_len,
        fiber_mass_g=process.fiber_mass_g(fiber_len),
        resin_mass_g=process.resin_mass_g(fiber_len),
        total_mass_g=process.total_mass_g(fiber_len),
        composite_volume_cm3=process.composite_volume_cm3(fiber_len),
        band_areal_weight_g_m2=process.band_areal_weight_g_m2(band.tow_width_mm),
    )

    # ── Süre tahmini ─────────────────────────────────────────────────────────
    time_est = TimeEstimate(
        motion_time_s=motion_time,
        setup_time_s=setup_time_s,
        total_time_s=motion_time + setup_time_s,
    )

    is_ready = len(blocking) == 0

    return ProductionReport(
        manufacturability=mfg,
        deposition=deposition,
        payout=payout,
        machine_limits=machine_rpt,
        saturation=saturation,
        material=material,
        time_estimate=time_est,
        is_production_ready=is_ready,
        blocking_issues=blocking,
        warnings=warnings,
    )
