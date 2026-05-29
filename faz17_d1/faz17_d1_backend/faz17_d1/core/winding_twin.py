"""
core/winding_twin.py — Tam Sarma Digital Twin Simülasyonu
===========================================================
Filament sarma sürecinin zaman-adımlı dijital ikizini simüle eder:
iş mili dönüşü, taşıyıcı hareketi, payout gözü konumu, yatırılan fiber
şeridi, katman birikimi ve zamana göre sarma ilerlemesi.

Bu telemetri sim'i (digital_twin.py) DEĞİLDİR; gerçek CAM süreç ikizidir.

Akış (Sprint 4B mimarisi — DIGITAL_TWIN_ARCHITECTURE.md)
--------------------------------------------------------
1. Katman katman yol üret (büyüyen yarıçap)            → layer_buildup
2. Her katman için S-eğrisi zamanlı hareket planla      → industrial_motion
3. Küresel zaman çizelgesi segmentleri (açı ofsetli)    → TrajectorySegment
4. Tekdüze dt ızgarasına örnekle (varsayılan dt=1.0 s)  → trajectory_builder
5. Birinci-derece taşıyıcı gecikmesi: lag = v·τ         → (bounded, stable)
6. Fiber yatırma haritası biriktir                      → fiber_deposition

Sprint 4B düzeltmeleri
----------------------
- RPM artık küresel kümülatif açıdan türetilir; katman sınırı sıçraması yok
  (eski 45.819 RPM hatası giderildi).
- Kararsız PD göz-tepki simülasyonu (simulate_eye_response) çalışma yolundan
  ÇIKARILDI; yerine sınırlı birinci-derece gecikme modeli kullanılır.
- Varsayılan dt 0.05 s → 1.0 s (durum sayısı ~20× azaldı).
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field, replace
from typing import List, Optional, Tuple

import numpy as np

from .fiber_band import FiberBand
from .fiber_deposition import DepositionMap, simulate_deposition
from .fiber_tension import FiberTensionModel
from .geometry_engine import MandrelProfile
from .industrial_motion import (
    MotionConstraints, SynchronizedSegment, plan_industrial_motion,
)
from .layer_buildup import LayeredPathResult, generate_layered_paths
from .machine_envelope import MachineEnvelope
from .path_generator import WindingPath, WindingPathParams
from .payout_dynamics import (
    PayoutDynamicsConfig, compute_carriage_lead_safe,
)
from .trajectory_builder import (
    TrajectorySegment, TwinTimeline, build_timeline,
)


@dataclass
class TwinState:
    """Tek bir zaman adımındaki makine durumu."""
    t_s: float
    spindle_angle_deg: float       # Kümülatif iş mili açısı
    spindle_rpm: float             # Anlık devir
    carriage_x_mm: float           # İstenen taşıyıcı konumu
    carriage_x_actual_mm: float    # Dinamik (gerçek) taşıyıcı konumu
    carriage_v_mm_s: float         # Taşıyıcı hızı
    eye_x_mm: float                # Payout gözü eksenel konumu
    eye_r_mm: float                # Payout gözü radyal konumu
    contact_z_mm: float            # Fiber-mandrel temas noktası
    contact_r_mm: float            # Temas noktasındaki yarıçap
    current_layer: int
    current_circuit: int
    fiber_deposited_mm: float      # Birikimli yatırılan fiber
    current_radius_mm: float       # O anki yüzey yarıçapı (katman büyümesi dahil)
    lag_error_mm: float            # Göz gecikme hatası
    progress_pct: float            # Toplam ilerleme [0,100]


@dataclass
class TwinSimulationResult:
    """Digital twin simülasyon çıktısı."""
    states: List[TwinState]
    dt_s: float
    total_time_s: float
    n_layers: int
    layered_paths: LayeredPathResult
    final_deposition: DepositionMap
    base_radius_mm: float
    final_radius_mm: float
    total_fiber_length_mm: float
    max_lag_error_mm: float
    max_spindle_rpm: float
    layer_time_ranges_s: List[Tuple[float, float]]   # Katman başına (t0, t1)

    def state_at(self, t: float) -> TwinState:
        """Verilen zamana en yakın durumu döndür."""
        if not self.states:
            raise ValueError("Durum yok")
        idx = int(round(t / self.dt_s))
        idx = max(0, min(len(self.states) - 1, idx))
        return self.states[idx]

    def summary(self) -> str:
        return (
            f"WindingTwin: {self.n_layers} katman | "
            f"{len(self.states)} durum @ dt={self.dt_s*1000:.0f}ms | "
            f"süre={self.total_time_s:.1f}s | "
            f"r: {self.base_radius_mm:.1f}→{self.final_radius_mm:.1f}mm | "
            f"fiber={self.total_fiber_length_mm/1000:.1f}m | "
            f"maks_gecikme={self.max_lag_error_mm:.3f}mm | "
            f"maks_RPM={self.max_spindle_rpm:.0f}"
        )


# ── İç: küresel zaman çizelgesi segmentleri ──────────────────────────────────

def _build_trajectory_segments(
    layered: LayeredPathResult,
    constraints: MotionConstraints,
) -> Tuple[List[TrajectorySegment], float, List[Tuple[float, float]]]:
    """
    Tüm katmanların S-eğrisi segmentlerini küresel zaman çizelgesinde birleştir.

    KRİTİK: Her katmanın WindingPath'i kendi a_deg'ini 0'dan başlatır. Katmanlar
    art arda eklenirken kümülatif iş mili açısı, bir önceki katmanın son açısı
    kadar (a_offset) kaydırılır. Bu, küresel açının monoton artmasını sağlar ve
    katman sınırındaki sahte Δa sıçramasını (eski 45.819 RPM hatası) önler.
    """
    segs_out: List[TrajectorySegment] = []
    t_cursor = 0.0
    a_offset = 0.0
    layer_ranges: List[Tuple[float, float]] = []

    for layer_idx, path in enumerate(layered.paths):
        prof = layered.profiles[layer_idx]
        r_layer = prof.avg_radius_mm
        layer_t0 = t_cursor

        sync = plan_industrial_motion(path, constraints)
        layer_final_a = 0.0
        for s in sync:
            segs_out.append(TrajectorySegment(
                t_start=t_cursor,
                t_end=t_cursor + s.t_duration_s,
                x_start=s.x_start,
                x_end=s.x_end,
                a_start=a_offset + s.a_start,
                a_end=a_offset + s.a_end,
                layer=layer_idx,
                circuit=0,
                radius_mm=r_layer,
            ))
            t_cursor += s.t_duration_s
            layer_final_a = max(layer_final_a, s.a_end)

        # Bir sonraki katman bu katmanın son kümülatif açısından devam eder
        a_offset += layer_final_a
        layer_ranges.append((layer_t0, t_cursor))

    return segs_out, t_cursor, layer_ranges


def simulate_winding(
    base_profile: MandrelProfile,
    band: FiberBand,
    base_params: WindingPathParams,
    n_layers: int,
    machine: Optional[MachineEnvelope] = None,
    tension: Optional[FiberTensionModel] = None,
    payout: Optional[PayoutDynamicsConfig] = None,
    dt_s: float = 1.0,
    hold_angle: bool = True,
    deposition_grid: Tuple[int, int] = (100, 240),
) -> TwinSimulationResult:
    """
    Tam sarma digital twin simülasyonunu çalıştır.

    Parametreler
    ----------
    base_profile : Çıplak mandrel geometrisi.
    band         : Fiber bant fiziği.
    base_params  : Temel sarma yolu parametreleri.
    n_layers     : Yatırılacak katman sayısı.
    machine      : Makine zarfı (None ise varsayılan).
    tension      : Fiber gerilim modeli (None ise varsayılan).
    payout       : Payout göz dinamiği (None ise varsayılan).
    dt_s         : Örnekleme zaman adımı (saniye). Varsayılan 1.0 s (Sprint 4B).
    hold_angle   : Sarma açısı sabit mi (True) yoksa Clairaut c mi (False).
    deposition_grid : (n_z, n_theta) yatırma haritası çözünürlüğü.

    Belirleyicilik
    --------------
    Aynı girdiler + aynı dt_s → bit-aynı durum dizisi (rastgelelik yok).
    """
    if dt_s <= 0:
        raise ValueError("dt_s > 0 olmalı")
    if machine is None:
        machine = MachineEnvelope()
    if tension is None:
        tension = FiberTensionModel()
    if payout is None:
        payout = PayoutDynamicsConfig()

    # Makine zarfından hareket kısıtlarını türet
    constraints = MotionConstraints(
        max_x_speed_mm_s=machine.max_carriage_speed_mm_s,
        max_x_accel_mm_s2=machine.max_carriage_accel_mm_s2,
        max_a_speed_deg_s=machine.max_spindle_deg_s,
        ref_radius_mm=base_profile.avg_radius_mm,
    )

    # 1. Katman katman yollar
    layered = generate_layered_paths(
        base_profile, band, base_params, n_layers, hold_angle=hold_angle
    )

    # 2-3. Küresel zaman çizelgesi segmentleri (açı ofsetli, monoton)
    segs, total_time, layer_ranges = _build_trajectory_segments(layered, constraints)

    if not segs or total_time <= 0:
        # Boş simülasyon
        dep = simulate_deposition(layered.paths, band, base_profile,
                                  deposition_grid[0], deposition_grid[1])
        return TwinSimulationResult(
            states=[], dt_s=dt_s, total_time_s=0.0, n_layers=n_layers,
            layered_paths=layered, final_deposition=dep,
            base_radius_mm=base_profile.avg_radius_mm,
            final_radius_mm=layered.final_radius_mm,
            total_fiber_length_mm=layered.total_fiber_length_mm,
            max_lag_error_mm=0.0, max_spindle_rpm=0.0,
            layer_time_ranges_s=layer_ranges,
        )

    # 4. Tekdüze dt ızgarasına örnekle (trajectory_builder — vektörize, deterministik)
    timeline: TwinTimeline = build_timeline(segs, dt_s=dt_s, total_time_s=total_time)
    n_samples = timeline.n_samples

    # 5. Birinci-derece taşıyıcı gecikmesi: lag = v·τ (sınırlı, kararlı, salınımsız)
    #    Kararsız PD izleyici (simulate_eye_response) ARTIK KULLANILMAZ.
    tau = payout.eye_lag_time_const_s
    lag_arr = timeline.carriage_v_mm_s * tau           # işaretli, |lag| ≤ v_max·τ
    x_actual_arr = timeline.x_mm - lag_arr             # gerçek konum referansın gerisinde
    max_lag = float(np.max(np.abs(lag_arr))) if n_samples else 0.0
    max_rpm = timeline.max_rpm

    # Statik payout lead (nominal açı — eksenel öncülük için yeterli)
    lead = compute_carriage_lead_safe(base_params.alpha_deg, payout.standoff_mm)

    states: List[TwinState] = []
    for i in range(n_samples):
        v = float(timeline.carriage_v_mm_s[i])
        travel_dir = math.copysign(1.0, v) if abs(v) > 1e-9 else 1.0
        eye_x = float(timeline.x_mm[i]) + travel_dir * lead
        eye_r = float(timeline.radius_mm[i]) + payout.standoff_mm
        progress = (float(timeline.t_s[i]) / total_time * 100.0) if total_time > 0 else 0.0

        states.append(TwinState(
            t_s=float(timeline.t_s[i]),
            spindle_angle_deg=float(timeline.a_deg[i]),
            spindle_rpm=float(timeline.rpm[i]),
            carriage_x_mm=float(timeline.x_mm[i]),
            carriage_x_actual_mm=float(x_actual_arr[i]),
            carriage_v_mm_s=v,
            eye_x_mm=eye_x,
            eye_r_mm=eye_r,
            contact_z_mm=float(timeline.x_mm[i]),
            contact_r_mm=float(timeline.radius_mm[i]),
            current_layer=int(timeline.layer[i]),
            current_circuit=int(timeline.circuit[i]),
            fiber_deposited_mm=float(timeline.fiber_mm[i]),
            current_radius_mm=float(timeline.radius_mm[i]),
            lag_error_mm=float(lag_arr[i]),
            progress_pct=progress,
        ))

    # 6. Yatırma haritası
    dep = simulate_deposition(layered.paths, band, base_profile,
                              deposition_grid[0], deposition_grid[1])

    return TwinSimulationResult(
        states=states,
        dt_s=dt_s,
        total_time_s=total_time,
        n_layers=n_layers,
        layered_paths=layered,
        final_deposition=dep,
        base_radius_mm=base_profile.avg_radius_mm,
        final_radius_mm=layered.final_radius_mm,
        total_fiber_length_mm=layered.total_fiber_length_mm,
        max_lag_error_mm=max_lag,
        max_spindle_rpm=max_rpm,
        layer_time_ranges_s=layer_ranges,
    )
