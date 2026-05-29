"""
core/execution_twin.py — Gelişmiş Dijital İkiz
================================================
Makine yürütme katmanının tüm alt raporlarını tek bir birleşik rapora
toplar. Bu, gerçek makine davranışını simüle eden en üst düzey
katmandır.

Alt bileşenler
--------------
    KinematicsReport      : 4-eksen ters/ileri kinematik analizi
    BandEdgeReport        : bant kenar takibi (boşluk / bindirme)
    EyeOrientationReport  : göz yönelim fizibilitesi
    ExecutionTimeline     : lag, backlash, kuantizasyon, senkronizasyon

Girdi pipeline
--------------
    WindingPath  +  FiberBand  +  MandrelProfile
        → analyze_path_kinematics()   → KinematicsReport
        → track_band_edges()           → BandEdgeReport
        → analyze_eye_orientation()    → EyeOrientationReport
        → (TwinTimeline via build_timeline / manual build)
        → simulate_execution()         → ExecutionTimeline

Çıktı: `AdvancedTwinReport`
    - is_execution_feasible  : bool  (tüm hard-stop yoksa True)
    - hard_stops             : List[str]  (fiziksel olarak imkânsız)
    - warnings               : List[str]  (risk / sınır aşımı)
    - kinematics, band_edges, eye_orient, execution sub-raporlar
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from .eye_orientation_solver import (
    EyeConstraints,
    EyeOrientationReport,
    analyze_eye_orientation,
)
from .fiber_band import FiberBand
from .fiber_contact_model import BandEdgeReport, track_band_edges
from .geometry_engine import MandrelProfile
from .machine_calibration import MachineCalibration, default_calibration
from .machine_execution import ExecutionConstraints, ExecutionTimeline, simulate_execution
from .machine_kinematics import KinematicsReport, analyze_path_kinematics
from .path_generator import WindingPath
from .trajectory_builder import TwinTimeline


# ── Gelişmiş ikiz giriş parametreleri ─────────────────────────────────────────

@dataclass
class AdvancedTwinParams:
    """
    Gelişmiş dijital ikiz simülasyonu için birleşik giriş parametreleri.

    Tüm alt analiz araçlarına ortak parametre geçişini sağlar.
    """
    # Kinematik analiz
    standoff_mm: Optional[float] = None       # None → calib.eye_base_standoff_mm
    kin_sample_every: int = 5                  # yol noktaları atlaması

    # Bant kenar takibi
    band_sample_every: int = 5
    max_gap_mm: float = 0.5                    # izin verilen maks boşluk
    max_excess_mm: float = 2.0                 # izin verilen maks aşırı bindirme

    # Göz yönelim
    eye_sample_every: int = 5
    tension_N: float = 50.0                    # nominal fiber gerilimi

    # Yürütme simülasyonu
    timeline_dt_s: float = 0.5                 # zaman ızgarası adımı

    # Hard-stop eşikleri
    max_following_error_x_mm: float = 0.5      # maks taşıyıcı takip hatası
    max_following_error_a_deg: float = 2.0     # maks iş mili takip hatası
    max_sync_error_deg: float = 5.0            # maks senkronizasyon faz hatası
    max_steer_deg: float = 60.0                # maks steering açısı
    min_bend_radius_mm: float = 15.0           # min büküm yarıçapı


# ── Gelişmiş dijital ikiz raporu ───────────────────────────────────────────────

@dataclass
class AdvancedTwinReport:
    """
    Gerçek makine yürütme simülasyonunun birleşik çıktısı.

    Tüm alt raporlar + üst düzey fizibilite kararı.
    hard_stops    : üretim tamamen imkânsız kılan koşullar
    warnings      : riski yüksek ama kurtarılabilir durumlar
    is_execution_feasible : hard_stop yoksa True
    """
    # Alt raporlar
    kinematics: KinematicsReport
    band_edges: BandEdgeReport
    eye_orient: EyeOrientationReport
    execution: ExecutionTimeline

    # Üst düzey karar
    is_execution_feasible: bool
    hard_stops: List[str]
    warnings: List[str]

    # Özet istatistikler (türetilmiş)
    peak_following_error_x_mm: float
    peak_following_error_a_deg: float
    rms_sync_error_deg: float
    n_backlash_reversals_x: int
    n_backlash_reversals_a: int
    n_feed_limited_samples: int
    max_steer_deg: float
    min_bend_radius_mm: float
    total_edge_drift_mm: float

    def summary(self) -> str:
        status = "YÜRÜTÜLEBİLİR ✓" if self.is_execution_feasible else "UYGULANAMAZ ✗"
        hs = f" | HARD_STOP={len(self.hard_stops)}" if self.hard_stops else ""
        return (
            f"GELİŞMİŞ_İKİZ [{status}]{hs} | "
            f"takip_x={self.peak_following_error_x_mm:.3f}mm "
            f"takip_a={self.peak_following_error_a_deg:.3f}° "
            f"faz_hatası_rms={self.rms_sync_error_deg:.3f}° | "
            f"backlash_x={self.n_backlash_reversals_x} "
            f"backlash_a={self.n_backlash_reversals_a} | "
            f"besleme_sınırlı={self.n_feed_limited_samples} | "
            f"steer_maks={self.max_steer_deg:.1f}° | "
            f"bend_min={self.min_bend_radius_mm:.1f}mm | "
            f"kenar_sürüklenme={self.total_edge_drift_mm:.2f}mm"
        )


# ── Yardımcı: TwinTimeline oluşturma ──────────────────────────────────────────

def _build_simple_timeline(path: WindingPath, dt_s: float = 0.5) -> TwinTimeline:
    """
    WindingPath'tan basit TwinTimeline üret.

    Gerçek winding_twin'in S-eğrisi interpolasyonu yerine doğrusal
    segment interpolasyonu kullanır; hız profili sabit ilerleme hızı.
    Bu, execution_twin'in kendi simülasyonu için yeterlidir.
    """
    pts = path.points
    if len(pts) < 2:
        z = np.zeros(1)
        return TwinTimeline(
            t_s=z, x_mm=z, a_deg=z, rpm=z,
            carriage_v_mm_s=z, spindle_v_deg_s=z,
            layer=z.astype(int), circuit=z.astype(int),
            radius_mm=z, fiber_mm=z,
            dt_s=dt_s, total_time_s=0.0,
        )

    # Zaman eksenini yol noktalarından türet
    x_raw = np.array([p.x_mm for p in pts])
    a_raw = np.array([p.a_deg for p in pts])
    feed = np.array([max(p.feed, 1e-3) for p in pts])
    layer = np.array([p.layer for p in pts], dtype=int)
    circuit = np.array([p.circuit for p in pts], dtype=int)

    # Birikmli süre: Δt_i = |Δx_i| / v_i
    dx = np.diff(x_raw, prepend=x_raw[0])
    dv = (feed[:-1] + feed[1:]) / 2.0 if len(feed) > 1 else feed[:1]
    # her Δt segmenti
    dt_seg = np.abs(np.diff(x_raw)) / dv
    t_pts = np.zeros(len(pts))
    t_pts[1:] = np.cumsum(dt_seg)

    total_t = float(t_pts[-1])
    if total_t < dt_s:
        total_t = dt_s

    t_uniform = np.arange(0.0, total_t + dt_s * 0.5, dt_s)
    n = len(t_uniform)

    x_mm = np.interp(t_uniform, t_pts, x_raw)

    # Kümülatif açı ileriye taşı (monoton olmalı)
    a_vals = a_raw.copy()
    offset = 0.0
    a_mono = np.empty(len(a_vals))
    a_mono[0] = a_vals[0]
    for k in range(1, len(a_vals)):
        delta = a_vals[k] + offset - a_mono[k - 1]
        if delta < -1e-3:
            offset += a_mono[k - 1] - a_vals[k] + 1.0
        a_mono[k] = a_vals[k] + offset

    a_deg = np.interp(t_uniform, t_pts, a_mono)
    v_x = np.gradient(x_mm, t_uniform)
    v_a = np.gradient(a_deg, t_uniform)
    rpm = np.abs(v_a) / 360.0 * 60.0

    layer_u = np.round(np.interp(t_uniform, t_pts, layer.astype(float))).astype(int)
    circuit_u = np.round(np.interp(t_uniform, t_pts, circuit.astype(float))).astype(int)

    # Fiber uzunluğu: birikmli |dx| + çevresel kat
    fiber_raw = np.cumsum(np.abs(np.diff(x_raw, prepend=x_raw[0])))
    fiber_u = np.interp(t_uniform, t_pts, fiber_raw)

    # Nominal yarıçap (tüm yolda sabit; gerçekte profile'dan türetilir)
    r_arr = np.full(n, 50.0)

    return TwinTimeline(
        t_s=t_uniform,
        x_mm=x_mm,
        a_deg=a_deg,
        rpm=rpm,
        carriage_v_mm_s=v_x,
        spindle_v_deg_s=v_a,
        layer=layer_u,
        circuit=circuit_u,
        radius_mm=r_arr,
        fiber_mm=fiber_u,
        dt_s=dt_s,
        total_time_s=float(t_uniform[-1]),
    )


# ── Ana üretici fonksiyon ──────────────────────────────────────────────────────

def run_advanced_twin(
    path: WindingPath,
    band: FiberBand,
    profile: MandrelProfile,
    calib: Optional[MachineCalibration] = None,
    exec_constraints: Optional[ExecutionConstraints] = None,
    eye_constraints: Optional[EyeConstraints] = None,
    params: Optional[AdvancedTwinParams] = None,
    timeline: Optional[TwinTimeline] = None,
) -> AdvancedTwinReport:
    """
    Gelişmiş dijital ikiz simülasyonunu çalıştır.

    Parametre
    ---------
    path            : WindingPath — CAM tarafından üretilen sarma yolu.
    band            : FiberBand — fiber bant geometrisi.
    profile         : MandrelProfile — mandrel geometrisi.
    calib           : MachineCalibration (None → default_calibration()).
    exec_constraints: ExecutionConstraints (None → varsayılan).
    eye_constraints : EyeConstraints (None → varsayılan).
    params          : AdvancedTwinParams (None → varsayılan).
    timeline        : Önceden hazırlanmış TwinTimeline (None → otomatik üretilir).

    Döner
    -----
    AdvancedTwinReport — tüm alt raporları ve fizibilite kararını içerir.
    """
    if calib is None:
        calib = default_calibration()
    if exec_constraints is None:
        exec_constraints = ExecutionConstraints()
    if eye_constraints is None:
        eye_constraints = EyeConstraints()
    if params is None:
        params = AdvancedTwinParams()

    hard_stops: List[str] = []
    warnings: List[str] = []

    # ── 1. Kinematik analizi ────────────────────────────────────────────────
    kin_report = analyze_path_kinematics(
        path, profile, calib,
        standoff_mm=params.standoff_mm,
        sample_every=params.kin_sample_every,
    )
    if not kin_report.is_valid:
        hard_stops.append(
            f"Kinematik geçersiz: {kin_report.n_invalid}/{kin_report.n_points} nokta arızalı"
        )
    warnings.extend(kin_report.warnings)

    # ── 2. Bant kenar takibi ────────────────────────────────────────────────
    band_report = track_band_edges(
        path, profile, band,
        sample_every=params.band_sample_every,
        max_gap_mm=params.max_gap_mm,
        max_excess_mm=params.max_excess_mm,
    )
    if not band_report.is_uniform:
        warnings.append("Bant kenar dağılımı tekdüze değil")
    if band_report.max_gap_mm > params.max_gap_mm * 2.0:
        hard_stops.append(
            f"Aşırı bant boşluğu: {band_report.max_gap_mm:.2f}mm "
            f"(limit {params.max_gap_mm:.2f}mm)"
        )
    warnings.extend(band_report.warnings)

    # ── 3. Göz yönelim analizi ──────────────────────────────────────────────
    eye_report = analyze_eye_orientation(
        path, profile, calib, eye_constraints,
        standoff_mm=params.standoff_mm,
        tension_N=params.tension_N,
        sample_every=params.eye_sample_every,
    )
    if not eye_report.is_feasible:
        hard_stops.append(
            f"Göz yönelimi uygulanamaz: {eye_report.n_infeasible} nokta limit aşımında"
        )
    if eye_report.min_bend_radius_mm < params.min_bend_radius_mm:
        hard_stops.append(
            f"Büküm yarıçapı çok küçük: {eye_report.min_bend_radius_mm:.1f}mm "
            f"(min {params.min_bend_radius_mm:.1f}mm)"
        )
    if eye_report.max_steer_deg > params.max_steer_deg:
        hard_stops.append(
            f"Steering açısı limit aşımı: {eye_report.max_steer_deg:.1f}° "
            f"(maks {params.max_steer_deg:.1f}°)"
        )
    warnings.extend(eye_report.warnings)

    # ── 4. Yürütme simülasyonu ──────────────────────────────────────────────
    if timeline is None:
        timeline = _build_simple_timeline(path, dt_s=params.timeline_dt_s)

    exec_tl = simulate_execution(timeline, path, profile, exec_constraints, calib)

    # Takip hatası hard-stop kontrolü
    if exec_tl.max_following_error_x_mm > params.max_following_error_x_mm:
        hard_stops.append(
            f"Taşıyıcı takip hatası aşıldı: {exec_tl.max_following_error_x_mm:.3f}mm "
            f"(limit {params.max_following_error_x_mm:.3f}mm)"
        )
    if exec_tl.max_following_error_a_deg > params.max_following_error_a_deg:
        hard_stops.append(
            f"İş mili takip hatası aşıldı: {exec_tl.max_following_error_a_deg:.3f}° "
            f"(limit {params.max_following_error_a_deg:.3f}°)"
        )

    # Senkronizasyon uyarısı
    if exec_tl.rms_sync_error_deg > params.max_sync_error_deg:
        warnings.append(
            f"Yüksek senkronizasyon faz hatası: rms={exec_tl.rms_sync_error_deg:.3f}° "
            f"(eşik {params.max_sync_error_deg:.3f}°)"
        )
    if exec_tl.n_sync_exceeded > 0:
        warnings.append(
            f"Senkronizasyon faz {exec_tl.n_sync_exceeded} örnekte "
            f">{exec_constraints.max_sync_phase_error_deg:.1f}° aştı"
        )

    # Backlash uyarıları
    if exec_tl.n_backlash_x > 5:
        warnings.append(
            f"Taşıyıcı backlash yön değişimi: {exec_tl.n_backlash_x} kez"
        )
    if exec_tl.n_backlash_a > 5:
        warnings.append(
            f"İş mili backlash yön değişimi: {exec_tl.n_backlash_a} kez"
        )

    # Besleme sınırlama uyarısı
    if exec_tl.n_feed_limited > len(timeline.t_s) * 0.30:
        warnings.append(
            f"Besleme %{exec_tl.n_feed_limited/len(timeline.t_s)*100:.0f} örnekte sınırlandı "
            f"(kubbe/eğrilik)"
        )

    is_feasible = len(hard_stops) == 0

    return AdvancedTwinReport(
        kinematics=kin_report,
        band_edges=band_report,
        eye_orient=eye_report,
        execution=exec_tl,
        is_execution_feasible=is_feasible,
        hard_stops=hard_stops,
        warnings=warnings,
        peak_following_error_x_mm=exec_tl.max_following_error_x_mm,
        peak_following_error_a_deg=exec_tl.max_following_error_a_deg,
        rms_sync_error_deg=exec_tl.rms_sync_error_deg,
        n_backlash_reversals_x=exec_tl.n_backlash_x,
        n_backlash_reversals_a=exec_tl.n_backlash_a,
        n_feed_limited_samples=exec_tl.n_feed_limited,
        max_steer_deg=eye_report.max_steer_deg,
        min_bend_radius_mm=eye_report.min_bend_radius_mm,
        total_edge_drift_mm=band_report.total_edge_drift_mm,
    )
