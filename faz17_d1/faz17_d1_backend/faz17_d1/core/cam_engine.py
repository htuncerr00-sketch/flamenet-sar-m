"""
core/cam_engine.py — CAM Motoru Facade (S2)
============================================
Sistemin **tek giriş noktası**. UI bu modülden başka hiçbir backend modülünü
doğrudan çağırmamalıdır. Facade, parametrik/STL farkını ve alt motorların
(path_generator, coverage_solver, motion_planner, gcode_postprocessor,
winding_twin) çağrı sırasını gizler.

Mimari akış:
    UI
     ↓
    cam_engine            ← BU DOSYA (yalnız orkestrasyon)
     ↓
    MandrelModel          (mandrel_model.py)
     ↓
    Path Generator        (path_generator.py)
     ↓
    Coverage Solver       (coverage_solver.py)
     ↓
    Motion Planner        (motion_planner.py)
     ↓
    Gcode Generator       (gcode_postprocessor.py)
     ↓
    TwinState             (winding_twin.py)

KESİN KURAL — HİÇBİR ALGORİTMA BURADA YENİDEN YAZILMAZ:
  - Geodesic/non-geodesic yol üretimi  → path_generator.generate_path
  - Kaplama çözümü                     → coverage_solver.solve_coverage
  - Hareket planlama                   → motion_planner.plan_motion
  - G-kod üretimi                      → gcode_postprocessor.generate_gcode
  - Digital twin                       → winding_twin.simulate_winding
  - STL zekâsı                         → stl_intelligence.analyze_stl
Bu dosya yalnızca bu çağrıları sıralar, girdi/çıktıyı düz veri sınıflarına sarar.

Determinizm: Aynı spec → aynı backend çağrıları → aynı sonuç (backend deterministik).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Union

from .geometry_engine import MandrelProfile
from .mandrel_model import MandrelModel
from .fiber_band import FiberBand
from .path_generator import (
    WindingPath, WindingPathParams, generate_path,
    preflight_check, ComplexityEstimate, ComplexityError,
)
from .coverage_solver import CoverageMap, solve_coverage
from .motion_planner import MotionSegment, plan_motion
from .gcode_postprocessor import MachineConfig, GCodeProgram, generate_gcode
from .winding_twin import TwinSimulationResult, simulate_winding
from .stl_intelligence import (
    TurnaroundCandidates, detect_turnaround_candidates, analyze_stl,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Girdi spesifikasyonları — düz veri (Qt YOK, backend nesnesi YOK)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class MandrelSpec:
    """Parametrik veya STL mandrel tanımı (UI → facade düz veri köprüsü)."""
    kind: str = "cylinder"            # "cylinder"|"cone"|"dome_cylinder_dome"|
                                      # "ellipsoidal"|"stl"
    diameter_mm: float = 100.0        # çap (yarıçap = çap/2)
    length_mm: float = 300.0          # silindir/koni uzunluğu
    cone_angle_deg: float = 10.0      # koni yarı açısı
    dome_height_mm: float = 30.0      # kubbe yüksekliği (dome_cylinder_dome)
    dome_hr_ratio: float = 0.7        # kubbe H/R oranı (ellipsoidal)
    stl_path: Optional[str] = None    # "stl" türü için dosya yolu
    analyze_stl: bool = True          # STL için intelligence katmanını çalıştır
    n_points: int = 200               # profil örnekleme nokta sayısı


@dataclass
class PathSpec:
    """Sarma yolu + kaplama parametreleri."""
    alpha_deg: float = 55.0
    n_layers: int = 4
    tow_width_mm: float = 6.0
    overlap_pct: float = 5.0
    feed_mm_s: float = 80.0
    spindle_rpm: float = 60.0
    strategy: str = "helical"         # "helical"|"hoop"|"polar"
    friction_mu: float = 0.0
    lambda_slip: float = 0.0
    # Facade davranış bayrakları
    run_preflight: bool = True        # complexity kapısı (path_generator)
    compute_coverage: bool = True     # coverage_solver çağrısı
    coverage_n_z: int = 120
    coverage_n_theta: int = 360


@dataclass
class MotionSpec:
    """4-eksen hareket planlama sınırları."""
    max_x_feed_mm_min: float = 5000.0
    max_a_rpm: float = 300.0
    accel_pct: float = 0.05


@dataclass
class TwinSpec:
    """Digital twin simülasyon parametreleri."""
    n_layers: int = 4
    dt_s: float = 1.0
    max_layers: int = 12              # twin maliyet kapağı (UI ile aynı varsayılan)
    hold_angle: bool = True


# ═══════════════════════════════════════════════════════════════════════════════
# Çıktı veri sınıfları
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PathStatistics:
    """Yol istatistikleri (WindingPath'ten türetilir)."""
    n_circuits: int
    n_layers: int
    total_fiber_length_mm: float
    estimated_time_s: float
    coverage_pct_theoretical: float   # WindingPath.coverage_pct (teorik)
    clairaut_c: float
    coverage_pct_solved: Optional[float] = None    # CoverageMap.coverage_pct (çözülmüş)
    gap_pct_solved: Optional[float] = None
    overlap_pct_solved: Optional[float] = None


@dataclass
class PathResult:
    """compute_path çıktısı — yol + profil + kaplama + istatistik + turnaround."""
    winding_path: WindingPath
    profile: MandrelProfile
    coverage: Optional[CoverageMap]
    statistics: PathStatistics
    turnaround: TurnaroundCandidates
    params: WindingPathParams
    preflight: Optional[ComplexityEstimate] = None


@dataclass
class MotionResult:
    """compute_motion çıktısı — hareket segmentleri + özet."""
    segments: List[MotionSegment]
    n_segments: int
    max_x_feed_mm_min: float
    max_a_rpm: float


@dataclass
class TwinResult:
    """compute_twin çıktısı — ham TwinSimulationResult sarmalayıcısı."""
    simulation: TwinSimulationResult
    n_layers: int

    @property
    def states(self):
        """TwinState listesi (UI animasyon köprüsü)."""
        return self.simulation.states

    @property
    def total_time_s(self) -> float:
        return self.simulation.total_time_s

    @property
    def base_radius_mm(self) -> float:
        return self.simulation.base_radius_mm

    @property
    def final_radius_mm(self) -> float:
        return self.simulation.final_radius_mm

    def summary(self) -> str:
        return self.simulation.summary()


# ═══════════════════════════════════════════════════════════════════════════════
# Yardımcılar
# ═══════════════════════════════════════════════════════════════════════════════

def _to_profile(obj: Union[MandrelModel, MandrelProfile]) -> MandrelProfile:
    """MandrelModel veya MandrelProfile → sade MandrelProfile."""
    if isinstance(obj, MandrelModel):
        return obj.as_profile()
    if isinstance(obj, MandrelProfile):
        return obj
    raise TypeError(
        "MandrelModel veya MandrelProfile bekleniyor, "
        f"{type(obj).__name__} alındı.")


def _build_params(profile: MandrelProfile, spec: PathSpec) -> WindingPathParams:
    """PathSpec → WindingPathParams (backend sözleşmesi)."""
    return WindingPathParams(
        profile=profile,
        alpha_deg=spec.alpha_deg,
        n_layers=spec.n_layers,
        tow_width_mm=spec.tow_width_mm,
        overlap_pct=spec.overlap_pct,
        feed_mm_s=spec.feed_mm_s,
        spindle_rpm=spec.spindle_rpm,
        winding_strategy=spec.strategy,
        friction_mu=spec.friction_mu,
        lambda_slip=spec.lambda_slip,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 1. build_mandrel_model — parametrik/STL farkını gizle
# ═══════════════════════════════════════════════════════════════════════════════

def build_mandrel_model(spec: MandrelSpec) -> MandrelModel:
    """
    Parametrik veya STL kaynağından MandrelModel üret.

    Parametrik türler doğrudan MandrelProfile fabrikalarını kullanır; STL ise
    (analyze_stl=True iken) tam STL Intelligence raporu üzerinden zengin model,
    aksi halde sade profil sarmalayıcı döndürür.

    HİÇBİR geometri algoritması burada hesaplanmaz — yalnız fabrika seçilir.
    """
    kind = spec.kind.lower()
    R = spec.diameter_mm / 2.0

    if kind == "cylinder":
        profile = MandrelProfile.cylinder(spec.length_mm, R, n_points=spec.n_points)
        return MandrelModel.from_parametric(profile)

    if kind == "cone":
        r_end = R + spec.length_mm * math.tan(math.radians(spec.cone_angle_deg))
        profile = MandrelProfile.cone(spec.length_mm, R, r_end, n_points=spec.n_points)
        return MandrelModel.from_parametric(profile)

    if kind == "dome_cylinder_dome":
        profile = MandrelProfile.dome_cylinder_dome(
            spec.length_mm, R, spec.dome_height_mm,
            n_points=max(spec.n_points, 300))
        return MandrelModel.from_parametric(profile)

    if kind == "ellipsoidal":
        profile = MandrelProfile.ellipsoidal_dome_cylinder_dome(
            spec.length_mm, R, dome_hr_ratio=spec.dome_hr_ratio,
            n_points=max(spec.n_points, 300))
        return MandrelModel.from_parametric(profile)

    if kind == "stl":
        if not spec.stl_path:
            raise ValueError("STL türü için stl_path zorunludur.")
        if spec.analyze_stl:
            report = analyze_stl(spec.stl_path)
            return MandrelModel.from_stl(report)
        # Zekâsız sade STL profili
        profile = MandrelProfile.from_stl(spec.stl_path, n_points=spec.n_points)
        return MandrelModel(profile=profile, source_type="stl",
                            source_path=spec.stl_path)

    raise ValueError(f"Bilinmeyen mandrel türü: {spec.kind!r}")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. compute_path — yol + kaplama + istatistik + turnaround
# ═══════════════════════════════════════════════════════════════════════════════

def compute_path(model: Union[MandrelModel, MandrelProfile],
                 spec: PathSpec) -> PathResult:
    """
    Sarma yolunu üret, kaplamayı çöz, turnaround adaylarını topla.

    Çağrı sırası (hepsi mevcut motorlar, yeniden yazım YOK):
      1. preflight_check        (opsiyonel complexity kapısı)
      2. generate_path          (geodesic/non-geodesic yol)
      3. solve_coverage         (opsiyonel 2B kaplama haritası)
      4. detect_turnaround_candidates
    """
    profile = _to_profile(model)
    params = _build_params(profile, spec)

    preflight: Optional[ComplexityEstimate] = None
    if spec.run_preflight:
        # preflight_check ComplexityError fırlatabilir — çağıran yakalar
        preflight = preflight_check(params)

    path = generate_path(params)

    coverage: Optional[CoverageMap] = None
    cov_pct = cov_gap = cov_overlap = None
    if spec.compute_coverage:
        band = FiberBand(tow_width_mm=spec.tow_width_mm,
                         overlap_pct=min(spec.overlap_pct, 90.0))
        coverage = solve_coverage(path, band, profile,
                                  n_z=spec.coverage_n_z,
                                  n_theta=spec.coverage_n_theta)
        cov_pct = coverage.coverage_pct
        cov_gap = coverage.gap_pct
        cov_overlap = coverage.overlap_pct

    turnaround = detect_turnaround_candidates(profile)

    stats = PathStatistics(
        n_circuits=path.n_circuits,
        n_layers=path.n_layers,
        total_fiber_length_mm=path.total_fiber_length_mm,
        estimated_time_s=path.estimated_time_s,
        coverage_pct_theoretical=path.coverage_pct,
        clairaut_c=path.clairaut_c,
        coverage_pct_solved=cov_pct,
        gap_pct_solved=cov_gap,
        overlap_pct_solved=cov_overlap,
    )

    return PathResult(
        winding_path=path, profile=profile, coverage=coverage,
        statistics=stats, turnaround=turnaround, params=params,
        preflight=preflight)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. compute_motion — 4-eksen hareket planı
# ═══════════════════════════════════════════════════════════════════════════════

def compute_motion(path_result: Union[PathResult, WindingPath],
                   spec: Optional[MotionSpec] = None) -> MotionResult:
    """
    WindingPath → MotionSegment listesi (motion_planner.plan_motion).

    PathResult veya doğrudan WindingPath kabul eder.
    """
    spec = spec or MotionSpec()
    path = path_result.winding_path if isinstance(path_result, PathResult) else path_result

    segments = plan_motion(
        path,
        max_x_feed_mm_min=spec.max_x_feed_mm_min,
        max_a_rpm=spec.max_a_rpm,
        accel_pct=spec.accel_pct,
    )
    return MotionResult(
        segments=segments, n_segments=len(segments),
        max_x_feed_mm_min=spec.max_x_feed_mm_min,
        max_a_rpm=spec.max_a_rpm)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. compute_gcode — makineye hazır G-kod
# ═══════════════════════════════════════════════════════════════════════════════

def compute_gcode(motion: Union[MotionResult, List[MotionSegment]],
                  path_result: Union[PathResult, WindingPath],
                  config: Optional[MachineConfig] = None) -> GCodeProgram:
    """
    MotionSegment listesi + WindingPath → GCodeProgram
    (gcode_postprocessor.generate_gcode).

    MotionResult/segment listesi ve PathResult/WindingPath kabul eder.
    """
    config = config or MachineConfig()
    segments = motion.segments if isinstance(motion, MotionResult) else motion
    path = path_result.winding_path if isinstance(path_result, PathResult) else path_result
    return generate_gcode(segments, path, config)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. compute_twin — digital twin simülasyonu
# ═══════════════════════════════════════════════════════════════════════════════

def compute_twin(model: Union[MandrelModel, MandrelProfile],
                 path_spec: PathSpec,
                 twin_spec: Optional[TwinSpec] = None) -> TwinResult:
    """
    Digital twin simülasyonunu çalıştır (winding_twin.simulate_winding).

    Katman sayısı twin_spec.max_layers ile sınırlanır (UI ile aynı maliyet
    kapağı). HİÇBİR fizik burada hesaplanmaz — simulate_winding çağrılır.
    """
    twin_spec = twin_spec or TwinSpec(n_layers=path_spec.n_layers)
    profile = _to_profile(model)
    base_params = _build_params(profile, path_spec)

    n_layers = max(1, min(int(twin_spec.n_layers), int(twin_spec.max_layers)))
    band = FiberBand(tow_width_mm=float(base_params.tow_width_mm))

    sim = simulate_winding(
        base_profile=profile,
        band=band,
        base_params=base_params,
        n_layers=n_layers,
        dt_s=twin_spec.dt_s,
        hold_angle=twin_spec.hold_angle,
    )
    return TwinResult(simulation=sim, n_layers=n_layers)


__all__ = [
    # Girdi spec'leri
    "MandrelSpec", "PathSpec", "MotionSpec", "TwinSpec",
    # Çıktı sınıfları
    "PathStatistics", "PathResult", "MotionResult", "TwinResult",
    # Facade API
    "build_mandrel_model", "compute_path", "compute_motion",
    "compute_gcode", "compute_twin",
    # Yeniden ihraç (UI tek importla erişsin)
    "MachineConfig", "GCodeProgram", "ComplexityError",
]
