"""
core/trajectory_builder.py — Tekdüze Zaman-Izgara Yörünge Üreticisi
=====================================================================
Digital twin'in zamanlama çekirdeği. Senkronize hareket segmentlerini
(SynchronizedSegment → TrajectorySegment) sabit dt'li tekdüze bir zaman
ızgarasına dönüştürür ve tüm türetilmiş kanalları hesaplar:

    x_mm(t)              taşıyıcı referans konumu
    a_deg(t)             KÜRESEL kümülatif iş mili açısı (monoton artan)
    rpm(t)               iş mili devri = |Δa/Δt| / 360 × 60
    carriage_v_mm_s(t)   taşıyıcı hızı (işaretli)
    spindle_v_deg_s(t)   iş mili açısal hızı (işaretli)
    fiber_mm(t)          birikimli fiber uzunluğu

Tasarım ilkeleri (Sprint 4B):
- Sabit dt (varsayılan 1.0 s) — yapılandırılabilir.
- Deterministik: aynı segmentler + aynı dt → bit-aynı çıktı.
- O(1) indeksleme: index_at(t) = round(t/dt), kenetlenmiş.
- Oynatma sırasında fizik yeniden hesaplaması YOK — her şey önceden örneklenir.

NOT: a_deg kümülatif açı, katman sınırlarında SIFIRLANMAMALIDIR. Çağıran
(winding_twin) her katmanın açısını bir önceki katmanın son açısıyla
kaydırarak küresel monotonluğu sağlar. Aksi halde katman geçişinde Δa
büyük negatif olur ve RPM astronomik değerlere fırlar (eski 45.819 RPM hatası).
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import List, Optional

import numpy as np


@dataclass
class TrajectorySegment:
    """
    Zaman çizelgesine yerleştirilmiş tek bir doğrusal hareket segmenti.

    Tüm açı değerleri (a_start, a_end) KÜRESEL kümülatif derece cinsindendir
    — yani çağıran katman ofsetini önceden eklemiştir.
    """
    t_start: float
    t_end: float
    x_start: float
    x_end: float
    a_start: float      # küresel kümülatif derece
    a_end: float        # küresel kümülatif derece
    layer: int
    circuit: int
    radius_mm: float


@dataclass
class TwinTimeline:
    """
    Tekdüze zaman-ızgara yörünge gösterimi.

    Tüm diziler aynı uzunluktadır (n_samples) ve t_s tekdüzedir
    (np.diff(t_s) ≈ dt_s).
    """
    t_s: np.ndarray
    x_mm: np.ndarray
    a_deg: np.ndarray            # küresel kümülatif (monoton artan)
    rpm: np.ndarray              # >= 0
    carriage_v_mm_s: np.ndarray  # işaretli
    spindle_v_deg_s: np.ndarray  # işaretli
    layer: np.ndarray            # int
    circuit: np.ndarray          # int
    radius_mm: np.ndarray
    fiber_mm: np.ndarray         # birikimli (monoton artmayan değil — artan)
    dt_s: float
    total_time_s: float

    @property
    def n_samples(self) -> int:
        return len(self.t_s)

    def index_at(self, t_s: float) -> int:
        """
        Verilen zamana karşılık gelen dizi indeksi — O(1).

        index = round(t / dt), [0, n-1] aralığına kenetlenir.
        """
        n = len(self.t_s)
        if n == 0:
            raise ValueError("Boş zaman çizelgesi")
        idx = int(round(t_s / self.dt_s))
        return max(0, min(n - 1, idx))

    @property
    def max_rpm(self) -> float:
        return float(np.max(self.rpm)) if self.n_samples else 0.0

    @property
    def peak_carriage_speed_mm_s(self) -> float:
        return float(np.max(np.abs(self.carriage_v_mm_s))) if self.n_samples else 0.0

    @property
    def total_fiber_mm(self) -> float:
        return float(self.fiber_mm[-1]) if self.n_samples else 0.0


def _empty_timeline(dt_s: float) -> TwinTimeline:
    z = np.zeros(0)
    zi = np.zeros(0, dtype=int)
    return TwinTimeline(
        t_s=z, x_mm=z, a_deg=z, rpm=z,
        carriage_v_mm_s=z, spindle_v_deg_s=z,
        layer=zi, circuit=zi, radius_mm=z, fiber_mm=z,
        dt_s=dt_s, total_time_s=0.0,
    )


def build_timeline(
    segments: List[TrajectorySegment],
    dt_s: float = 1.0,
    total_time_s: Optional[float] = None,
) -> TwinTimeline:
    """
    Segment listesini tekdüze dt'li zaman ızgarasına örnekle.

    Parametreler
    ----------
    segments     : Zaman sıralı TrajectorySegment listesi (a_deg küresel kümülatif).
    dt_s         : Örnekleme adımı (saniye). Varsayılan 1.0 s.
    total_time_s : Toplam süre (None ise son segmentin t_end'i kullanılır).

    Döner
    -----
    TwinTimeline — tekdüze örneklenmiş, türetilmiş kanallarla.

    Determinizm
    -----------
    Saf vektörize numpy; rastgelelik yok. Aynı girdi → bit-aynı çıktı.
    """
    if dt_s <= 0:
        raise ValueError("dt_s > 0 olmalı")
    if not segments:
        return _empty_timeline(dt_s)

    total = total_time_s if total_time_s is not None else segments[-1].t_end
    if total <= 0:
        return _empty_timeline(dt_s)

    n = int(math.floor(total / dt_s + 1e-9)) + 1
    t = np.arange(n, dtype=np.float64) * dt_s

    # Segment sınır dizileri
    seg_t0 = np.array([s.t_start for s in segments], dtype=np.float64)
    seg_t1 = np.array([s.t_end for s in segments], dtype=np.float64)
    seg_x0 = np.array([s.x_start for s in segments], dtype=np.float64)
    seg_x1 = np.array([s.x_end for s in segments], dtype=np.float64)
    seg_a0 = np.array([s.a_start for s in segments], dtype=np.float64)
    seg_a1 = np.array([s.a_end for s in segments], dtype=np.float64)
    seg_layer = np.array([s.layer for s in segments], dtype=np.int64)
    seg_circuit = np.array([s.circuit for s in segments], dtype=np.int64)
    seg_radius = np.array([s.radius_mm for s in segments], dtype=np.float64)

    # Her örnek zamanı için segment indeksi (deterministik, O(n log m))
    si = np.searchsorted(seg_t1, t, side="left")
    si = np.clip(si, 0, len(segments) - 1)

    dur = np.maximum(seg_t1[si] - seg_t0[si], 1e-12)
    frac = np.clip((t - seg_t0[si]) / dur, 0.0, 1.0)

    x = seg_x0[si] + frac * (seg_x1[si] - seg_x0[si])
    a = seg_a0[si] + frac * (seg_a1[si] - seg_a0[si])
    layer = seg_layer[si]
    circuit = seg_circuit[si]
    radius = seg_radius[si]

    # Hızlar — geriye fark; ilk örnek durağan (v=0)
    carriage_v = np.zeros(n)
    spindle_v = np.zeros(n)
    if n > 1:
        carriage_v[1:] = np.diff(x) / dt_s
        spindle_v[1:] = np.diff(a) / dt_s

    # RPM = |Δa/Δt| / 360 × 60  (a küresel kümülatif → Δa ≥ 0)
    rpm = np.abs(spindle_v) / 360.0 * 60.0

    # Fiber birikimi: ds = hypot(Δx, r_mid·Δa_rad)
    fiber = np.zeros(n)
    if n > 1:
        dx = np.diff(x)
        da_rad = np.radians(np.diff(a))
        r_mid = (radius[:-1] + radius[1:]) * 0.5
        ds = np.hypot(dx, r_mid * da_rad)
        fiber[1:] = np.cumsum(ds)

    return TwinTimeline(
        t_s=t,
        x_mm=x,
        a_deg=a,
        rpm=rpm,
        carriage_v_mm_s=carriage_v,
        spindle_v_deg_s=spindle_v,
        layer=layer,
        circuit=circuit,
        radius_mm=radius,
        fiber_mm=fiber,
        dt_s=dt_s,
        total_time_s=float(total),
    )
