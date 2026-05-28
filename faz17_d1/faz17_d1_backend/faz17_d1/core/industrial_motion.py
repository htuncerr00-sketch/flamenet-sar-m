"""
core/industrial_motion.py — Endüstriyel Hareket Planlayıcısı
=============================================================
Jerk (sarsıntı) sınırlı S-eğrisi hız profilleri ile senkronize
X (taşıyıcı) ve A (iş mili) eksen hareketi planlar.

S-eğrisi 7 fazı
---------------
1. Jerk-artış   : a: 0 → a_max  (jerk = +j_max)
2. Sabit ivme   : a = a_max  (jerk = 0)
3. Jerk-azalış  : a: a_max → 0  (jerk = -j_max)
4. Sabit hız    : v = v_max
5. Jerk-artış   : a: 0 → -a_max  (jerk = -j_max)
6. Sabit yavaşlama: a = -a_max  (jerk = 0)
7. Jerk-azalış  : a: -a_max → 0  (jerk = +j_max)

Senkronizasyon kuralı
---------------------
Her segment için iki eksen de aynı sürede tamamlanmalı.
En yavaş eksen süreyi belirler; daha hızlı eksen orantısal ölçeklenir.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from .motion_planner import MotionSegment
from .path_generator import WindingPath


# ── Makine kısıtları ─────────────────────────────────────────────────────────

@dataclass
class MotionConstraints:
    """
    4-eksen filament sarma makinesinin hareket kısıtları.

    X ekseni  : taşıyıcı (mm)
    A ekseni  : iş mili (derece)
    """
    # X ekseni
    max_x_speed_mm_s: float = 83.3          # ≈ 5000 mm/dak
    max_x_accel_mm_s2: float = 500.0
    max_x_jerk_mm_s3: float = 2000.0

    # A ekseni (derece cinsinden)
    max_a_speed_deg_s: float = 1800.0       # = 300 RPM × 360°/60s
    max_a_accel_deg_s2: float = 3600.0      # 10 RPM/s × 360°/60s
    max_a_jerk_deg_s3: float = 18000.0

    # Referans yarıçap (A ekseni mm karşılığı için)
    ref_radius_mm: float = 50.0

    def a_deg_to_mm(self, deg: float) -> float:
        """Açı değişimini (°) eşdeğer çevresel uzunluğa (mm) dönüştür."""
        return abs(deg) * math.pi / 180.0 * self.ref_radius_mm

    def mm_to_a_deg(self, mm: float) -> float:
        return abs(mm) / self.ref_radius_mm * 180.0 / math.pi


# ── S-eğrisi hesapları ───────────────────────────────────────────────────────

def _accel_distance_time(v_start: float, v_end: float,
                          a_max: float, j_max: float) -> Tuple[float, float]:
    """
    Hız değişimi için S-eğrisi mesafe ve süresini hesapla.

    Parametreler
    ----------
    v_start, v_end : Başlangıç / bitiş hızları (aynı birim, >= 0)
    a_max          : Maksimum ivme
    j_max          : Maksimum jerk

    Döner: (distance, time)
    """
    dv = abs(v_end - v_start)
    if dv < 1e-9:
        return 0.0, 0.0

    # Bir jerk fazında kazanılan hız: v_j = a_max² / (2·j_max)
    t_j = a_max / j_max          # Jerk faz süresi
    v_j = 0.5 * j_max * t_j ** 2  # = a_max² / (2·j_max)

    if dv >= 2.0 * v_j:
        # 3 fazlı: jerk-artış + sabit ivme + jerk-azalış
        t1 = t_j
        t2 = (dv - 2.0 * v_j) / a_max
        t3 = t_j
        t_total = t1 + t2 + t3

        # Mesafe hesabı (başlangıç hızı v_start varsayılır)
        v_min = min(v_start, v_end)
        # Faz 1: v_min → v_min + v_j  (jerk artış)
        d1 = v_min * t1 + (1.0 / 6.0) * j_max * t1 ** 3
        v1 = v_min + v_j
        # Faz 2: sabit ivme
        d2 = v1 * t2 + 0.5 * a_max * t2 ** 2
        v2 = v1 + a_max * t2
        # Faz 3: jerk azalış
        d3 = v2 * t3 + 0.5 * a_max * t3 ** 2 - (1.0 / 6.0) * j_max * t3 ** 3
    else:
        # 2 fazlı: jerk-artış + jerk-azalış (sabit ivme yok)
        t1 = math.sqrt(dv / j_max)
        t2 = t1
        t_total = t1 + t2

        a_peak = j_max * t1  # Ulaşılan tepe ivmesi
        v_min = min(v_start, v_end)

        d1 = v_min * t1 + (1.0 / 6.0) * j_max * t1 ** 3
        v1 = v_min + 0.5 * j_max * t1 ** 2
        d3 = v1 * t2 + 0.5 * a_peak * t2 ** 2 - (1.0 / 6.0) * j_max * t2 ** 3

        d2 = 0.0
        d3_actual = d3

    return (d1 + d2 + d3), t_total


def scurve_move_time(
    distance: float,
    v_start: float,
    v_end: float,
    v_max: float,
    a_max: float,
    j_max: float,
) -> Tuple[float, float]:
    """
    Belirtilen mesafedeki S-eğrisi hareketinin süresini hesapla.

    Eğer mesafe, v_max'a ulaşmak için yeterli değilse, ulaşılabilen
    tepe hız ikili arama (binary search) ile bulunur.

    Döner: (toplam_süre, ulaşılan_tepe_hız)
    """
    if distance < 1e-9:
        return 0.0, v_start

    v_start = max(0.0, min(v_start, v_max))
    v_end = max(0.0, min(v_end, v_max))

    d_acc, t_acc = _accel_distance_time(v_start, v_max, a_max, j_max)
    d_dec, t_dec = _accel_distance_time(v_max, v_end, a_max, j_max)

    if d_acc + d_dec <= distance + 1e-9:
        # v_max'a ulaşılabilir; cruise fazı var
        d_cruise = distance - d_acc - d_dec
        t_cruise = d_cruise / max(v_max, 1e-9)
        return t_acc + t_cruise + t_dec, v_max

    # v_max'a ulaşılamıyor — ikili arama ile tepe hızı bul
    v_lo = max(v_start, v_end)
    v_hi = v_max

    for _ in range(30):
        v_mid = (v_lo + v_hi) * 0.5
        d_a, _ = _accel_distance_time(v_start, v_mid, a_max, j_max)
        d_d, _ = _accel_distance_time(v_mid, v_end, a_max, j_max)
        if d_a + d_d < distance:
            v_lo = v_mid
        else:
            v_hi = v_mid

    v_peak = v_lo
    d_a, t_a = _accel_distance_time(v_start, v_peak, a_max, j_max)
    d_d, t_d = _accel_distance_time(v_peak, v_end, a_max, j_max)
    d_cruise = max(0.0, distance - d_a - d_d)
    t_cruise = d_cruise / max(v_peak, 1e-9)

    return t_a + t_cruise + t_d, v_peak


# ── Senkronize segment ────────────────────────────────────────────────────────

@dataclass
class SynchronizedSegment:
    """
    Hem X hem de A ekseninin senkronize S-eğrisi hareketi.

    İki eksen de t_duration_s süre içinde hareket tamamlar.
    """
    x_start: float
    x_end: float
    a_start: float
    a_end: float
    feed_mm_min: float        # G-code ilerleme hızı
    segment_type: str

    t_duration_s: float       # Gerçek segment süresi
    v_x_peak_mm_s: float      # X ekseni tepe hızı
    v_a_peak_deg_s: float     # A ekseni tepe hızı
    x_is_saturated: bool      # X max hıza dayandı mı
    a_is_saturated: bool      # A max hıza dayandı mı

    def to_motion_segment(self) -> MotionSegment:
        return MotionSegment(
            x_start=self.x_start,
            x_end=self.x_end,
            a_start=self.a_start,
            a_end=self.a_end,
            feed_mm_min=self.feed_mm_min,
            segment_type=self.segment_type,
        )


# ── Ana planlayıcı ────────────────────────────────────────────────────────────

def plan_industrial_motion(
    path: WindingPath,
    constraints: MotionConstraints,
    v_start_mm_s: float = 0.0,
    v_end_mm_s: float = 0.0,
) -> List[SynchronizedSegment]:
    """
    Sarma yolunu jerk-sınırlı S-eğrisi segmentlerine dönüştür.

    Her devre:
    1. X ve A eksen mesafelerini hesapla.
    2. Her eksen için bağımsız S-eğrisi süresi hesapla.
    3. Yavaş eksen süreyi belirler; hızlı eksen orantısal ölçeklenir.
    4. Ölçeklenmiş hız kısıt dışına çıkıyorsa: saturasyon işaretle.

    Parametreler
    ----------
    v_start_mm_s, v_end_mm_s : Taşıyıcının başlangıç/bitiş hızları.
    """
    pts = path.points
    if len(pts) < 2:
        return []

    segments: List[SynchronizedSegment] = []

    # Devre başı/sonu noktaları arasında segmentleri işle
    prev_circuit = (pts[0].layer, pts[0].circuit)
    circuit_start = 0

    def _process_block(start_idx: int, end_idx: int,
                        v0: float, v1: float) -> None:
        block = pts[start_idx:end_idx]
        if len(block) < 2:
            return

        n = len(block)

        for i in range(n - 1):
            p0, p1 = block[i], block[i + 1]

            dx = abs(p1.x_mm - p0.x_mm)
            da_deg = abs(p1.a_deg - p0.a_deg)

            # ── X ekseni süresi ──────────────────────────────────────────
            if dx > 1e-9:
                # Uç noktalar için v0/v1 yalnızca devre başı/sonunda uygulanır
                v0x = v0 if i == 0 else 0.0
                v1x = v1 if i == n - 2 else 0.0
                t_x, vx_peak = scurve_move_time(
                    dx, v0x, v1x,
                    constraints.max_x_speed_mm_s,
                    constraints.max_x_accel_mm_s2,
                    constraints.max_x_jerk_mm_s3,
                )
            else:
                t_x, vx_peak = 0.0, 0.0

            # ── A ekseni süresi ──────────────────────────────────────────
            if da_deg > 1e-9:
                v0a = constraints.mm_to_a_deg(v0) if i == 0 else 0.0
                v1a = constraints.mm_to_a_deg(v1) if i == n - 2 else 0.0
                t_a, va_peak = scurve_move_time(
                    da_deg, v0a, v1a,
                    constraints.max_a_speed_deg_s,
                    constraints.max_a_accel_deg_s2,
                    constraints.max_a_jerk_deg_s3,
                )
            else:
                t_a, va_peak = 0.0, 0.0

            # ── Senkronizasyon ───────────────────────────────────────────
            t_seg = max(t_x, t_a, 1e-9)

            # Ölçeklenmiş gerçek hızlar
            vx_actual = (dx / t_seg) if t_seg > 1e-9 and dx > 0 else 0.0
            va_actual = (da_deg / t_seg) if t_seg > 1e-9 and da_deg > 0 else 0.0

            x_sat = vx_actual > constraints.max_x_speed_mm_s * 1.001
            a_sat = va_actual > constraints.max_a_speed_deg_s * 1.001

            # G-code ilerleme hızı (taşıyıcı + çevresel hız vektörü)
            r_ref = constraints.ref_radius_mm
            v_circ_mm_s = va_actual * math.pi / 180.0 * r_ref
            v_combined = math.hypot(vx_actual, v_circ_mm_s)
            feed_mm_min = min(
                v_combined * 60.0,
                constraints.max_x_speed_mm_s * 60.0,
            )
            feed_mm_min = max(feed_mm_min, 1.0)

            segments.append(SynchronizedSegment(
                x_start=p0.x_mm, x_end=p1.x_mm,
                a_start=p0.a_deg, a_end=p1.a_deg,
                feed_mm_min=feed_mm_min,
                segment_type="LINEAR",
                t_duration_s=t_seg,
                v_x_peak_mm_s=vx_actual,
                v_a_peak_deg_s=va_actual,
                x_is_saturated=x_sat,
                a_is_saturated=a_sat,
            ))

    for i, pt in enumerate(pts):
        key = (pt.layer, pt.circuit)
        is_last = (i == len(pts) - 1)
        if key != prev_circuit or is_last:
            end_idx = i + 1 if is_last else i
            _process_block(circuit_start, end_idx, v_start_mm_s, v_end_mm_s)
            circuit_start = i
            prev_circuit = key

    return segments


# ── Saturasyon analizi ───────────────────────────────────────────────────────

@dataclass
class SaturationReport:
    """Eksen saturasyon analiz raporu."""
    total_segments: int
    x_saturated_count: int
    a_saturated_count: int
    x_saturation_pct: float    # Saturasyon olan segment oranı (%)
    a_saturation_pct: float
    max_x_speed_required_mm_s: float
    max_a_speed_required_deg_s: float
    is_feasible: bool
    issues: List[str]

    def summary(self) -> str:
        status = "UYGULANABİLİR" if self.is_feasible else "UYGULANAMAz"
        return (
            f"Saturasyon Analizi: {status} | "
            f"X sat={self.x_saturation_pct:.1f}% | "
            f"A sat={self.a_saturation_pct:.1f}% | "
            f"maks_x={self.max_x_speed_required_mm_s:.1f} mm/s | "
            f"maks_a={self.max_a_speed_required_deg_s:.1f} °/s"
        )


def analyze_saturation(
    segments: List[SynchronizedSegment],
    constraints: MotionConstraints,
) -> SaturationReport:
    """Segmentler için saturasyon istatistiklerini hesapla."""
    if not segments:
        return SaturationReport(0, 0, 0, 0.0, 0.0, 0.0, 0.0, True, [])

    n = len(segments)
    x_sat = sum(1 for s in segments if s.x_is_saturated)
    a_sat = sum(1 for s in segments if s.a_is_saturated)
    max_vx = max((s.v_x_peak_mm_s for s in segments), default=0.0)
    max_va = max((s.v_a_peak_deg_s for s in segments), default=0.0)

    issues: List[str] = []
    if x_sat > 0:
        issues.append(f"X ekseni {x_sat} segmentte ({x_sat/n*100:.1f}%) saturasyonda")
    if a_sat > 0:
        issues.append(f"A ekseni {a_sat} segmentte ({a_sat/n*100:.1f}%) saturasyonda")

    is_feasible = (x_sat == 0) and (a_sat == 0)

    return SaturationReport(
        total_segments=n,
        x_saturated_count=x_sat,
        a_saturated_count=a_sat,
        x_saturation_pct=x_sat / n * 100.0,
        a_saturation_pct=a_sat / n * 100.0,
        max_x_speed_required_mm_s=max_vx,
        max_a_speed_required_deg_s=max_va,
        is_feasible=is_feasible,
        issues=issues,
    )
