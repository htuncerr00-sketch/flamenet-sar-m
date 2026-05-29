"""
core/winding_twin.py — Tam Sarma Digital Twin Simülasyonu
===========================================================
Filament sarma sürecinin zaman-adımlı dijital ikizini simüle eder:
iş mili dönüşü, taşıyıcı hareketi, payout gözü konumu, yatırılan fiber
şeridi, katman birikimi ve zamana göre sarma ilerlemesi.

Bu telemetri sim'i (digital_twin.py) DEĞİLDİR; gerçek CAM süreç ikizidir.

Akış
----
1. Katman katman yol üret (büyüyen yarıçap)        → layer_buildup
2. Her katman için S-eğrisi zamanlı hareket planla  → industrial_motion
3. Küresel zaman çizelgesi oluştur (segmentleri birleştir)
4. dt aralıklarla örnekle → TwinState çerçeveleri
5. Göz dinamiğini uygula (atalet/gecikme)           → payout_dynamics
6. Fiber yatırma haritası biriktir                  → fiber_deposition
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
    simulate_eye_response,
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


# ── İç: zaman çizelgesi segmenti ─────────────────────────────────────────────

@dataclass
class _TimedSegment:
    seg: SynchronizedSegment
    layer: int
    circuit: int
    t_start: float
    t_end: float
    radius_mm: float    # Bu segmentin yüzey yarıçapı


def _build_timeline(
    layered: LayeredPathResult,
    constraints: MotionConstraints,
) -> Tuple[List[_TimedSegment], float, List[Tuple[float, float]]]:
    """Tüm katmanların segmentlerini küresel zaman çizelgesinde birleştir."""
    timed: List[_TimedSegment] = []
    t_cursor = 0.0
    layer_ranges: List[Tuple[float, float]] = []

    for layer_idx, path in enumerate(layered.paths):
        prof = layered.profiles[layer_idx]
        r_layer = prof.avg_radius_mm
        layer_t0 = t_cursor

        segs = plan_industrial_motion(path, constraints)
        for s in segs:
            # Segment devresini noktadan türet (a_start'a en yakın)
            circuit = 0
            timed.append(_TimedSegment(
                seg=s, layer=layer_idx, circuit=circuit,
                t_start=t_cursor, t_end=t_cursor + s.t_duration_s,
                radius_mm=r_layer,
            ))
            t_cursor += s.t_duration_s

        layer_ranges.append((layer_t0, t_cursor))

    return timed, t_cursor, layer_ranges


def simulate_winding(
    base_profile: MandrelProfile,
    band: FiberBand,
    base_params: WindingPathParams,
    n_layers: int,
    machine: Optional[MachineEnvelope] = None,
    tension: Optional[FiberTensionModel] = None,
    payout: Optional[PayoutDynamicsConfig] = None,
    dt_s: float = 0.05,
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
    dt_s         : Örnekleme zaman adımı (saniye).
    hold_angle   : Sarma açısı sabit mi (True) yoksa Clairaut c mi (False).
    deposition_grid : (n_z, n_theta) yatırma haritası çözünürlüğü.
    """
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

    # 2-3. Küresel zaman çizelgesi
    timed, total_time, layer_ranges = _build_timeline(layered, constraints)

    if not timed or total_time <= 0:
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

    # 4. dt aralıklarla örnekle
    n_samples = int(math.floor(total_time / dt_s)) + 1
    times = np.arange(n_samples) * dt_s

    seg_ends = np.array([ts.t_end for ts in timed])

    x_des = np.zeros(n_samples)
    a_des = np.zeros(n_samples)
    layer_arr = np.zeros(n_samples, dtype=int)
    circuit_arr = np.zeros(n_samples, dtype=int)
    radius_arr = np.zeros(n_samples)

    for i, t in enumerate(times):
        si = int(np.searchsorted(seg_ends, t, side='left'))
        si = min(si, len(timed) - 1)
        ts = timed[si]
        dur = ts.t_end - ts.t_start
        f = (t - ts.t_start) / dur if dur > 1e-12 else 0.0
        f = min(max(f, 0.0), 1.0)
        x_des[i] = ts.seg.x_start + f * (ts.seg.x_end - ts.seg.x_start)
        a_des[i] = ts.seg.a_start + f * (ts.seg.a_end - ts.seg.a_start)
        layer_arr[i] = ts.layer
        circuit_arr[i] = ts.circuit
        radius_arr[i] = ts.radius_mm

    # 5. Göz dinamiği (atalet/gecikme) — istenen taşıyıcı yörüngesi üzerinde
    eye_resp = simulate_eye_response(times, x_des, payout)

    # Hız ve RPM
    v_arr = np.zeros(n_samples)
    rpm_arr = np.zeros(n_samples)
    if n_samples > 1:
        v_arr[1:] = np.diff(x_des) / dt_s
        da = np.diff(a_des)
        rpm_arr[1:] = (da / dt_s) / 360.0 * 60.0  # deg/s → RPM

    # Fiber birikimi: ds = hypot(dx, r·da_rad)
    fiber_cum = np.zeros(n_samples)
    if n_samples > 1:
        dx = np.diff(x_des)
        da_rad = np.radians(np.diff(a_des))
        r_mid = (radius_arr[:-1] + radius_arr[1:]) * 0.5
        ds = np.sqrt(dx ** 2 + (r_mid * da_rad) ** 2)
        fiber_cum[1:] = np.cumsum(ds)

    # Göz konumu: eksenel lead + radyal standoff
    states: List[TwinState] = []
    max_rpm = 0.0
    for i in range(n_samples):
        alpha_local = base_params.alpha_deg  # nominal; lead için yeterli
        lead = compute_carriage_lead_safe(alpha_local, payout.standoff_mm)
        travel_dir = math.copysign(1.0, v_arr[i]) if abs(v_arr[i]) > 1e-9 else 1.0
        eye_x = x_des[i] + travel_dir * lead
        eye_r = radius_arr[i] + payout.standoff_mm

        max_rpm = max(max_rpm, abs(rpm_arr[i]))

        states.append(TwinState(
            t_s=float(times[i]),
            spindle_angle_deg=float(a_des[i]),
            spindle_rpm=float(rpm_arr[i]),
            carriage_x_mm=float(x_des[i]),
            carriage_x_actual_mm=float(eye_resp.x_actual_mm[i]),
            carriage_v_mm_s=float(v_arr[i]),
            eye_x_mm=float(eye_x),
            eye_r_mm=float(eye_r),
            contact_z_mm=float(x_des[i]),
            contact_r_mm=float(radius_arr[i]),
            current_layer=int(layer_arr[i]),
            current_circuit=int(circuit_arr[i]),
            fiber_deposited_mm=float(fiber_cum[i]),
            current_radius_mm=float(radius_arr[i]),
            lag_error_mm=float(eye_resp.lag_error_mm[i]),
            progress_pct=float(times[i] / total_time * 100.0),
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
        max_lag_error_mm=eye_resp.max_lag_error_mm,
        max_spindle_rpm=max_rpm,
        layer_time_ranges_s=layer_ranges,
    )
