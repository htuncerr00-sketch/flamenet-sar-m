"""
core/motion_planner.py — 4-Eksen Hareket Planlayıcısı
=======================================================
WindingPath noktalarını G-code segmentlerine dönüştürür.
Trapez hız profili ile hızlanma/frenleme uygular.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import List

from .path_generator import WindingPath, WindingPoint


@dataclass
class MotionSegment:
    """Tek bir G-code hareketi."""
    x_start: float    # Taşıyıcı başlangıç konumu (mm)
    x_end: float      # Taşıyıcı bitiş konumu (mm)
    a_start: float    # İş mili başlangıç açısı (derece, kümülatif)
    a_end: float      # İş mili bitiş açısı (derece, kümülatif)
    feed_mm_min: float
    segment_type: str = "LINEAR"  # "LINEAR" | "RAPID" | "DWELL"


def plan_motion(path: WindingPath,
                max_x_feed_mm_min: float = 5000.0,
                max_a_rpm: float = 300.0,
                accel_pct: float = 0.05) -> List[MotionSegment]:
    """
    WindingPoint listesini MotionSegment listesine dönüştür.

    - Eş zamanlı X + A hareketi
    - Vektör hız sınırlaması: birleşik ilerleme oranı her iki eksen için de güvenli
    - accel_pct: her geçişin başı/sonu için frenleme bölgesi oranı
    """
    if not path.points:
        return []

    pts = path.points
    segments: List[MotionSegment] = []

    # Devre sınırlarını bul (circuit değeri değiştiğinde)
    prev_circuit = pts[0].circuit
    prev_layer = pts[0].layer
    circuit_start_idx = 0

    def _flush(start_idx: int, end_idx: int):
        """start..end arasındaki noktaları segmentlere dönüştür."""
        block = pts[start_idx:end_idx]
        n = len(block)
        if n < 2:
            return
        accel_n = max(1, int(n * accel_pct))

        for i in range(n - 1):
            p0, p1 = block[i], block[i + 1]
            dx = abs(p1.x_mm - p0.x_mm)
            da_deg = abs(p1.a_deg - p0.a_deg)
            da_mm_equiv = da_deg / 360.0 * (2.0 * math.pi * 50.0)  # 50mm referans yarıçap

            # Hız sınırlaması
            base_feed_mm_s = p0.feed
            if da_mm_equiv > 1e-6:
                # A ekseni hız sınırı: max_a_rpm * 360 deg/rev / 60 s/min
                max_a_deg_s = max_a_rpm * 6.0  # deg/s
                t_a = da_deg / max_a_deg_s if max_a_deg_s > 0 else 0
                t_x = dx / max(max_x_feed_mm_min / 60.0, 1.0)
                t_min = max(t_a, t_x, dx / max(base_feed_mm_s, 1.0))
                feed = dx / max(t_min, 1e-9) if t_min > 0 and dx > 0 else base_feed_mm_s
            else:
                feed = base_feed_mm_s

            # Hızlanma/frenleme bölgeleri: hızı %30'a düşür
            if i < accel_n or i >= n - 1 - accel_n:
                feed *= 0.7

            feed_mm_min = min(feed * 60.0, max_x_feed_mm_min)
            feed_mm_min = max(feed_mm_min, 60.0)

            segments.append(MotionSegment(
                x_start=p0.x_mm,
                x_end=p1.x_mm,
                a_start=p0.a_deg,
                a_end=p1.a_deg,
                feed_mm_min=feed_mm_min,
                segment_type="LINEAR",
            ))

    for i, pt in enumerate(pts):
        if pt.circuit != prev_circuit or pt.layer != prev_layer or i == len(pts) - 1:
            _flush(circuit_start_idx, i + (1 if i == len(pts) - 1 else 0))
            circuit_start_idx = i
            prev_circuit = pt.circuit
            prev_layer = pt.layer

    return segments
