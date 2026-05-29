"""
core/manufacturing_report.py — Birleşik Endüstriyel Üretim Karar Katmanı
=========================================================================
Tüm endüstriyel doğrulayıcıları tek bir ProductionManufacturingReport
altında toplar; üretim kararı, uyarılar ve engeller açıkça raporlanır.

Entegre edilen modüller:
  machine_limits      → yörünge gerçekleştirilebilirliği
  fiber_tension_model → gerilim profili kararlılığı
  dome_transition     → kubbe geçiş geçerliliği
  layer_stacking      → katman istifleme tutarlılığı
  coverage_solver     → kaplama riski analizi
  fiber_deposition    → yatırma haritası + toplam malzeme
  process_parameters  → malzeme tüketimi
  gcode_postprocessor → G-code güvenlik doğrulaması (isteğe bağlı)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from .coverage_solver import CoverageRiskReport, analyze_coverage_risk, solve_coverage
from .dome_transition import DomeTransitionReport, analyze_dome_transition
from .fiber_band import FiberBand
from .fiber_deposition import DepositionMap, simulate_deposition
from .fiber_tension_model import TensionProfileReport, TensionModelConfig, estimate_tension_profile
from .geometry_engine import MandrelProfile
from .layer_stacking import LayerStackReport, analyze_layer_stack
from .machine_limits import MachineLimits, MachineValidationReport, validate_trajectory_against_machine
from .path_generator import WindingPath, WindingPoint
from .process_parameters import ProcessParameters
from .trajectory_builder import TwinTimeline, TrajectorySegment, build_timeline


# ── TwinTimeline yardımcı: WindingPath → TwinTimeline ─────────────────────────

def _timeline_from_path(
    path: WindingPath,
    dt_s: float = 0.5,
    default_radius_mm: float = 50.0,
) -> TwinTimeline:
    """
    WindingPath noktalarından tekdüze dt'li TwinTimeline üret.

    Zaman kestirim: her iki ardışık nokta arasındaki taşıyıcı yer değiştirmesi
    ÷ ilerleme hızı. Bu, makine limiti doğrulaması için yeterince doğrudur.
    """
    pts = path.points
    if len(pts) < 2:
        z = np.zeros(0)
        zi = np.zeros(0, dtype=int)
        return TwinTimeline(
            t_s=z, x_mm=z, a_deg=z, rpm=z,
            carriage_v_mm_s=z, spindle_v_deg_s=z,
            layer=zi, circuit=zi, radius_mm=z, fiber_mm=z,
            dt_s=dt_s, total_time_s=0.0,
        )

    # Birikmeli zaman: |Δz_fiber| / feed
    t_raw = [0.0]
    for i in range(1, len(pts)):
        p0, p1 = pts[i - 1], pts[i]
        dz = abs(p1.z_fiber - p0.z_fiber)
        feed = max(p0.feed, 1.0)
        t_raw.append(t_raw[-1] + max(dz / feed, 1e-4))

    t_raw_arr = np.asarray(t_raw)
    x_raw = np.array([p.x_mm for p in pts])
    layer_raw = np.array([p.layer for p in pts])
    circ_raw = np.array([p.circuit for p in pts])
    zf_raw = np.array([p.z_fiber for p in pts])

    # Kümülatif iş mili açısı: geriye atlama → ofseti düzelt (çok-yol birleştirme)
    a_vals = [p.a_deg for p in pts]
    a_raw = np.empty(len(a_vals))
    a_raw[0] = a_vals[0]
    offset = 0.0
    for k in range(1, len(a_vals)):
        raw_diff = a_vals[k] + offset - a_raw[k - 1]
        if raw_diff < -1e-3:          # geri atlama → ofset güncelle
            offset += a_raw[k - 1] - a_vals[k] + 1.0
        a_raw[k] = a_vals[k] + offset

    total_T = float(t_raw_arr[-1])
    n_out = max(2, int(total_T / dt_s) + 1)
    t_out = np.linspace(0.0, total_T, n_out)
    dt_actual = float(t_out[1] - t_out[0]) if n_out > 1 else dt_s

    x_out = np.interp(t_out, t_raw_arr, x_raw)
    a_out = np.interp(t_out, t_raw_arr, a_raw)
    zf_out = np.interp(t_out, t_raw_arr, zf_raw)
    layer_out = np.interp(t_out, t_raw_arr, layer_raw.astype(float)).astype(int)
    circ_out = np.interp(t_out, t_raw_arr, circ_raw.astype(float)).astype(int)

    v_x = np.gradient(x_out, dt_actual)
    v_a = np.gradient(a_out, dt_actual)
    rpm_out = np.abs(v_a) / 360.0 * 60.0

    radius_out = np.full(n_out, default_radius_mm)

    return TwinTimeline(
        t_s=t_out,
        x_mm=x_out,
        a_deg=a_out,
        rpm=rpm_out,
        carriage_v_mm_s=v_x,
        spindle_v_deg_s=v_a,
        layer=layer_out,
        circuit=circ_out,
        radius_mm=radius_out,
        fiber_mm=zf_out,
        dt_s=dt_actual,
        total_time_s=total_T,
    )


# ── Malzeme ve Süre Tahminleri ─────────────────────────────────────────────────

@dataclass
class MaterialEstimate:
    """Malzeme tüketim tahmini."""
    fiber_length_total_mm: float
    fiber_mass_g: float
    resin_mass_g: float
    total_mass_g: float
    composite_volume_cm3: float
    band_areal_weight_g_m2: float
    n_layers: int

    def summary(self) -> str:
        return (
            f"Malzeme ({self.n_layers} katman): fiber={self.fiber_length_total_mm / 1000:.1f}m "
            f"({self.fiber_mass_g:.1f}g) + reçine={self.resin_mass_g:.1f}g "
            f"= {self.total_mass_g:.1f}g | hacim={self.composite_volume_cm3:.1f}cm³ "
            f"| FAW={self.band_areal_weight_g_m2:.0f}g/m²"
        )


@dataclass
class CycleTimeEstimate:
    """Çevrim süresi tahmini."""
    winding_time_s: float
    setup_time_s: float
    total_time_s: float
    n_circuits_total: int

    @property
    def total_time_min(self) -> float:
        return self.total_time_s / 60.0

    def summary(self) -> str:
        return (
            f"Süre: sarma={self.winding_time_s:.1f}s + kurulum={self.setup_time_s:.0f}s "
            f"= {self.total_time_s:.1f}s ({self.total_time_min:.1f}dk) | "
            f"devre={self.n_circuits_total}"
        )


# ── Ana Rapor Dataclass'ı ──────────────────────────────────────────────────────

@dataclass
class ProductionManufacturingReport:
    """
    Birleşik endüstriyel üretim karar raporu.

    Her alt rapor bağımsız çalışmıştır. `is_manufacturable` kararı,
    tüm kritik denetçilerden sıfır engel alınması gerektirir.
    Uyarılar üretimi durdurmaz ama operatör kararı ister.
    """
    # ── Alt raporlar ────────────────────────────────────────────────────────
    machine_limits: Optional[MachineValidationReport]
    tension_profile: TensionProfileReport
    dome_transition: DomeTransitionReport
    layer_stack: LayerStackReport
    coverage_risk: CoverageRiskReport
    deposition: DepositionMap

    # ── Tahminler ───────────────────────────────────────────────────────────
    material: MaterialEstimate
    cycle_time: CycleTimeEstimate

    # ── Karar katmanı ───────────────────────────────────────────────────────
    is_manufacturable: bool
    hard_stop_failures: List[str]    # üretime kesin engeller
    warnings: List[str]              # operatör dikkati gerektiren durumlar

    def verdict(self) -> str:
        if self.is_manufacturable:
            return "★★★ ÜRETİLEBİLİR ★★★"
        return "DURUN — ÜRETİLEMEZ"

    def summary(self) -> str:
        lines = [
            f"\n{'=' * 66}",
            f"  ÜRETİM RAPORU: {self.verdict()}",
            f"{'=' * 66}",
        ]
        # Alt rapor durumları
        def _ml_status() -> str:
            if self.machine_limits is None:
                return "ATLANDI"
            return "GERÇEKLEŞTİRİLEBİLİR ✓" if self.machine_limits.is_realizable else "GERÇEKLEŞTİRİLEMEZ ✗"

        lines += [
            f"  Makine limitleri : {_ml_status()}",
            f"  Fiber gerilimi   : {'KARARLI ✓' if self.tension_profile.is_stable else 'KARARSIZ ✗'}",
            f"  Kubbe geçişi     : {'GEÇİLEBİLİR ✓' if self.dome_transition.is_traversable else 'GEÇİLEMEZ ✗'}",
            f"  Katman istifleme : {'TUTARLI ✓' if self.layer_stack.is_consistent else 'TUTARSIZ ✗'}",
            f"  Kaplama riski    : {'KABUL ✓' if self.coverage_risk.is_acceptable else 'RİSKLİ ✗'}",
        ]
        # Yatırma haritası
        dep = self.deposition
        lines.append(
            f"  Yatırma haritası : kaplama={dep.coverage_pct:.1f}%  "
            f"ort_kalinlik={dep.mean_thickness_mm:.3f}mm  "
            f"toplam={dep.total_thickness_sum:.1f}mm"
        )
        # Tahminler
        lines += [
            f"  {self.material.summary()}",
            f"  {self.cycle_time.summary()}",
        ]
        # Engeller
        if self.hard_stop_failures:
            lines.append("\n  ENGELLEYİCİ HATALAR:")
            lines.extend(f"    ✗ {e}" for e in self.hard_stop_failures)
        # Uyarılar (ilk 12)
        if self.warnings:
            lines.append("\n  UYARILAR:")
            lines.extend(f"    ! {w}" for w in self.warnings[:12])
            if len(self.warnings) > 12:
                lines.append(f"    ... ve {len(self.warnings) - 12} uyarı daha")
        lines.append(f"{'=' * 66}\n")
        return "\n".join(lines)


# ── Ana Fonksiyon ──────────────────────────────────────────────────────────────

def generate_manufacturing_report(
    paths: List[WindingPath],
    band: FiberBand,
    profile: MandrelProfile,
    alpha_deg: float,
    n_layers: int,
    process: Optional[ProcessParameters] = None,
    limits: Optional[MachineLimits] = None,
    timeline: Optional[TwinTimeline] = None,
    tension_config: Optional[TensionModelConfig] = None,
    friction_coeff: float = 0.30,
    nesting_factor: float = 0.05,
    setup_time_s: float = 60.0,
    coverage_overlap_threshold: int = 3,
    max_excess_pct: float = 10.0,
    max_dry_pct: float = 5.0,
    n_z: int = 80,
    n_theta: int = 160,
) -> ProductionManufacturingReport:
    """
    Tüm endüstriyel doğrulayıcıları çalıştırıp birleşik rapor üret.

    Parametreler
    ----------
    paths             : Sarma katmanı yolları listesi.
    band              : Fiber bant tanımı.
    profile           : Mandrel geometrisi.
    alpha_deg         : Nominal sarma açısı (derece).
    n_layers          : Katman sayısı.
    process           : Proses/malzeme parametreleri (None → varsayılan).
    limits            : Makine limitleri (None → varsayılan 4-eksen makinesi).
    timeline          : Hazır TwinTimeline (None → path'ten oluşturulur).
    tension_config    : Gerilim modeli yapılandırması.
    friction_coeff    : Fiber-mandrel sürtünme katsayısı.
    nesting_factor    : Katman iç içe geçme fraksiyonu (0..1).
    setup_time_s      : Kurulum süresi payı (saniye).
    coverage_overlap_threshold : Reçine-zengin hücre eşiği (devre sayısı).
    max_excess_pct    : Kabul edilebilir maksimum reçine fazlası %.
    max_dry_pct       : Kabul edilebilir maksimum kuru fiber %.
    n_z, n_theta      : Yatırma/kaplama ızgara boyutları.
    """
    if process is None:
        process = ProcessParameters()
    if limits is None:
        limits = MachineLimits()

    hard_stops: List[str] = []
    warns: List[str] = []

    # ── 1. Makine limiti doğrulaması ─────────────────────────────────────────
    machine_rpt: Optional[MachineValidationReport] = None
    if paths:
        tl = timeline
        if tl is None:
            # Tüm yollardan noktaları birleştir, geçici yol nesnesi gerekmez
            all_pts = [pt for p in paths for pt in p.points]
            total_fiber = sum(p.total_fiber_length_mm for p in paths)
            total_time = sum(p.estimated_time_s for p in paths)
            _dummy_path = paths[0].__class__(
                points=all_pts,
                n_circuits=sum(p.n_circuits for p in paths),
                n_layers=max(p.n_layers for p in paths),
                total_fiber_length_mm=total_fiber,
                estimated_time_s=total_time,
                coverage_pct=max(p.coverage_pct for p in paths),
                clairaut_c=paths[0].clairaut_c,
                params=paths[0].params,
            )
            tl = _timeline_from_path(_dummy_path)
        if tl.n_samples >= 3:
            machine_rpt = validate_trajectory_against_machine(tl, limits)
            if not machine_rpt.is_realizable:
                hard_stops.extend(machine_rpt.critical_issues)
            warns.extend(machine_rpt.warnings)

    # ── 2. Fiber gerilim profili ──────────────────────────────────────────────
    tension_rpt = estimate_tension_profile(
        paths[0] if paths else WindingPath([], 0, 0, 0.0, 0.0, 0.0),
        profile,
        config=tension_config,
    )
    if not tension_rpt.is_stable:
        hard_stops.append("Fiber gerilimi kararsız (kapstan amplifikasyonu veya gevşek temas)")
    warns.extend(tension_rpt.warnings)

    # ── 3. Kubbe geçiş analizi ────────────────────────────────────────────────
    dome_rpt = analyze_dome_transition(profile, alpha_deg, friction_coeff=friction_coeff)
    if not dome_rpt.is_traversable:
        hard_stops.extend(dome_rpt.critical_issues)
    warns.extend(dome_rpt.warnings)

    # ── 4. Katman istifleme tutarlılığı ───────────────────────────────────────
    stack_rpt = analyze_layer_stack(
        profile, band, alpha_deg, n_layers,
        nesting_factor=nesting_factor,
        hold_circuits=True,
        hold_angle=True,
    )
    if not stack_rpt.is_consistent:
        hard_stops.append(
            f"Katman istifleme tutarsız: {stack_rpt.n_layers_with_gap} katmanda boşluk"
        )
    warns.extend(stack_rpt.warnings)

    # ── 5. Kaplama riski ──────────────────────────────────────────────────────
    first_path = paths[0] if paths else None
    cmap = solve_coverage(first_path, band, profile, n_z=n_z, n_theta=n_theta) if first_path else None
    if cmap is not None:
        coverage_rpt = analyze_coverage_risk(
            cmap,
            overlap_threshold=coverage_overlap_threshold,
            max_excess_pct=max_excess_pct,
            max_dry_pct=max_dry_pct,
        )
        if not coverage_rpt.is_acceptable:
            warns.extend(coverage_rpt.warnings)
    else:
        from .coverage_solver import CoverageRiskReport as CRR, CoverageMap as CM
        import numpy as _np
        _dummy_bins = _np.zeros(2)
        _dummy_cm = CM(_dummy_bins, _dummy_bins, _np.zeros((2, 2), dtype=_np.int32), profile)
        coverage_rpt = CRR(
            excess_resin_pct=0.0, dry_fiber_pct=0.0,
            excess_resin_zones=[], dry_fiber_zones=[],
            is_acceptable=True, warnings=["Yol yok — kaplama analizi atlandı"],
        )

    # ── 6. Fiber yatırma haritası ─────────────────────────────────────────────
    tension_N = tension_config.set_tension_N if tension_config is not None else 50.0
    dep_map = simulate_deposition(
        paths, band, profile,
        n_z=n_z, n_theta=n_theta,
        tension_N=tension_N,
    ) if paths else simulate_deposition([], band, profile, n_z=n_z, n_theta=n_theta)

    # ── 7. Malzeme tahmini ────────────────────────────────────────────────────
    total_fiber_mm = sum(p.total_fiber_length_mm for p in paths) if paths else 0.0
    material_est = MaterialEstimate(
        fiber_length_total_mm=total_fiber_mm,
        fiber_mass_g=process.fiber_mass_g(total_fiber_mm),
        resin_mass_g=process.resin_mass_g(total_fiber_mm),
        total_mass_g=process.total_mass_g(total_fiber_mm),
        composite_volume_cm3=process.composite_volume_cm3(total_fiber_mm),
        band_areal_weight_g_m2=process.band_areal_weight_g_m2(band.tow_width_mm),
        n_layers=n_layers,
    )

    # ── 8. Çevrim süresi ──────────────────────────────────────────────────────
    winding_s = sum(p.estimated_time_s for p in paths) if paths else 0.0
    total_circuits = sum(p.n_circuits for p in paths) if paths else 0
    cycle_est = CycleTimeEstimate(
        winding_time_s=winding_s,
        setup_time_s=setup_time_s,
        total_time_s=winding_s + setup_time_s,
        n_circuits_total=total_circuits,
    )

    is_mfg = len(hard_stops) == 0

    return ProductionManufacturingReport(
        machine_limits=machine_rpt,
        tension_profile=tension_rpt,
        dome_transition=dome_rpt,
        layer_stack=stack_rpt,
        coverage_risk=coverage_rpt,
        deposition=dep_map,
        material=material_est,
        cycle_time=cycle_est,
        is_manufacturable=is_mfg,
        hard_stop_failures=hard_stops,
        warnings=warns,
    )
