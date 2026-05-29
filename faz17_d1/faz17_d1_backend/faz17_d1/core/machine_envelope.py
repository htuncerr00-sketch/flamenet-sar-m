"""
core/machine_envelope.py — Makine Çalışma Zarfı Modeli
========================================================
Filament sarma makinesinin fiziksel ve operasyonel sınırlarını modeller.

Sınır hiyerarşisi
-----------------
1. Sert eksen limitleri (hard limits)  : fiziksel mekanik durdurucular
2. Yumuşak seyahat limitleri (soft)    : sert limitler içinde güvenli çalışma
3. Dinamik limitler                    : ivme, hız, RPM
4. Payout gözü çalışma alanı           : radyal erişim sınırları

Tüm konum kontrolleri ihlal listesi döndürür (boş liste = uygun).
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np


@dataclass
class MachineEnvelope:
    """
    4-eksen filament sarma makinesinin çalışma zarfı.

    X ekseni  : taşıyıcı (mm)
    A ekseni  : iş mili (sürekli dönme, açısal limit yok)
    Eye       : payout gözü radyal erişim (mm)
    """
    # ── Sert eksen limitleri (fiziksel) ──────────────────────────────────────
    x_hard_min_mm: float = -10.0
    x_hard_max_mm: float = 400.0

    # ── Yumuşak seyahat limitleri (operasyonel) ─────────────────────────────
    x_soft_min_mm: float = -5.0
    x_soft_max_mm: float = 395.0

    # ── Dinamik limitler ─────────────────────────────────────────────────────
    max_carriage_speed_mm_s: float = 83.3       # ≈ 5000 mm/dak
    max_carriage_accel_mm_s2: float = 500.0
    max_spindle_rpm: float = 300.0

    # ── Payout gözü çalışma alanı ────────────────────────────────────────────
    eye_radial_min_mm: float = 60.0             # min mandrel yüzeyi mesafesi
    eye_radial_max_mm: float = 500.0            # max radyal erişim

    def __post_init__(self) -> None:
        if self.x_soft_min_mm < self.x_hard_min_mm:
            raise ValueError("x_soft_min < x_hard_min — yumuşak limit sert limitin dışında")
        if self.x_soft_max_mm > self.x_hard_max_mm:
            raise ValueError("x_soft_max > x_hard_max — yumuşak limit sert limitin dışında")
        if self.x_soft_min_mm >= self.x_soft_max_mm:
            raise ValueError("x_soft_min >= x_soft_max — geçersiz seyahat aralığı")

    @property
    def soft_travel_mm(self) -> float:
        return self.x_soft_max_mm - self.x_soft_min_mm

    @property
    def max_spindle_deg_s(self) -> float:
        return self.max_spindle_rpm * 360.0 / 60.0

    # ── Konum kontrolleri ────────────────────────────────────────────────────

    def check_carriage_position(self, x_mm: float) -> List[str]:
        """Taşıyıcı konumu sınır kontrolü. Döner: ihlal listesi."""
        v: List[str] = []
        if x_mm < self.x_hard_min_mm or x_mm > self.x_hard_max_mm:
            v.append(
                f"SERT LİMİT İHLALİ: X={x_mm:.2f}mm dışında "
                f"[{self.x_hard_min_mm:.1f}, {self.x_hard_max_mm:.1f}]"
            )
        elif x_mm < self.x_soft_min_mm or x_mm > self.x_soft_max_mm:
            v.append(
                f"Yumuşak limit ihlali: X={x_mm:.2f}mm dışında "
                f"[{self.x_soft_min_mm:.1f}, {self.x_soft_max_mm:.1f}]"
            )
        return v

    def check_eye_position(self, eye_r_mm: float) -> List[str]:
        """Payout gözü radyal erişim kontrolü."""
        v: List[str] = []
        if eye_r_mm < self.eye_radial_min_mm:
            v.append(
                f"Göz çalışma alanı: r={eye_r_mm:.1f}mm < min "
                f"{self.eye_radial_min_mm:.1f}mm"
            )
        if eye_r_mm > self.eye_radial_max_mm:
            v.append(
                f"Göz çalışma alanı: r={eye_r_mm:.1f}mm > max "
                f"{self.eye_radial_max_mm:.1f}mm"
            )
        return v

    def check_speed(self, carriage_speed_mm_s: float,
                    spindle_rpm: float) -> List[str]:
        """Hız sınır kontrolü."""
        v: List[str] = []
        if abs(carriage_speed_mm_s) > self.max_carriage_speed_mm_s * 1.001:
            v.append(
                f"Taşıyıcı hızı: {carriage_speed_mm_s:.1f}mm/s > max "
                f"{self.max_carriage_speed_mm_s:.1f}mm/s"
            )
        if abs(spindle_rpm) > self.max_spindle_rpm * 1.001:
            v.append(
                f"İş mili devri: {spindle_rpm:.1f}RPM > max "
                f"{self.max_spindle_rpm:.1f}RPM"
            )
        return v

    def check_accel(self, carriage_accel_mm_s2: float) -> List[str]:
        """İvme sınır kontrolü."""
        v: List[str] = []
        if abs(carriage_accel_mm_s2) > self.max_carriage_accel_mm_s2 * 1.001:
            v.append(
                f"Taşıyıcı ivmesi: {carriage_accel_mm_s2:.1f}mm/s² > max "
                f"{self.max_carriage_accel_mm_s2:.1f}mm/s²"
            )
        return v


@dataclass
class EnvelopeViolation:
    """Tek bir zarf ihlali kaydı."""
    z_mm: float
    kind: str          # "hard" | "soft" | "eye" | "speed" | "accel"
    message: str
    severity: float    # 0..1 (1 = kritik / sert limit)


@dataclass
class MachineLimitReport:
    """Sarma yolu boyunca makine limiti analiz raporu."""
    n_points_checked: int
    violations: List[EnvelopeViolation]
    hard_limit_count: int
    soft_limit_count: int
    eye_workspace_count: int
    speed_count: int
    accel_count: int
    x_range_used_mm: tuple
    eye_r_range_mm: tuple
    is_within_envelope: bool

    def summary(self) -> str:
        status = "ZARF İÇİNDE ✓" if self.is_within_envelope else "ZARF DIŞINDA ✗"
        return (
            f"Makine Limiti: {status} | "
            f"{self.n_points_checked} nokta | "
            f"sert={self.hard_limit_count} yumuşak={self.soft_limit_count} "
            f"göz={self.eye_workspace_count} hız={self.speed_count} "
            f"ivme={self.accel_count} | "
            f"X kullanımı=[{self.x_range_used_mm[0]:.1f}, {self.x_range_used_mm[1]:.1f}]mm"
        )


def check_path_envelope(
    path,
    envelope: MachineEnvelope,
    profile=None,
    eye_standoff_mm: float = 150.0,
) -> MachineLimitReport:
    """
    Sarma yolu noktalarını makine zarfına karşı kontrol et.

    Parametreler
    ----------
    path           : WindingPath — kontrol edilecek yol.
    envelope       : MachineEnvelope — makine sınırları.
    profile        : MandrelProfile — göz radyal konumu hesabı için (opsiyonel).
    eye_standoff_mm: Payout gözü mandrel yüzeyi mesafesi.
    """
    pts = path.points
    violations: List[EnvelopeViolation] = []
    hard = soft = eye_c = speed_c = accel_c = 0

    x_min_used = float('inf')
    x_max_used = float('-inf')
    eye_r_min = float('inf')
    eye_r_max = float('-inf')

    for pt in pts:
        x_min_used = min(x_min_used, pt.x_mm)
        x_max_used = max(x_max_used, pt.x_mm)

        # Taşıyıcı konum
        for msg in envelope.check_carriage_position(pt.x_mm):
            is_hard = "SERT" in msg
            violations.append(EnvelopeViolation(
                z_mm=pt.x_mm, kind="hard" if is_hard else "soft",
                message=msg, severity=1.0 if is_hard else 0.5,
            ))
            if is_hard:
                hard += 1
            else:
                soft += 1

        # Göz radyal konumu
        if profile is not None:
            r_surf = profile.radius_at(pt.x_mm)
            eye_r = r_surf + eye_standoff_mm
            eye_r_min = min(eye_r_min, eye_r)
            eye_r_max = max(eye_r_max, eye_r)
            for msg in envelope.check_eye_position(eye_r):
                violations.append(EnvelopeViolation(
                    z_mm=pt.x_mm, kind="eye", message=msg, severity=0.7,
                ))
                eye_c += 1

    if eye_r_min == float('inf'):
        eye_r_min = eye_r_max = 0.0

    is_ok = (hard == 0 and eye_c == 0)  # sert + göz kritik; yumuşak uyarı

    return MachineLimitReport(
        n_points_checked=len(pts),
        violations=violations,
        hard_limit_count=hard,
        soft_limit_count=soft,
        eye_workspace_count=eye_c,
        speed_count=speed_c,
        accel_count=accel_c,
        x_range_used_mm=(x_min_used if pts else 0.0, x_max_used if pts else 0.0),
        eye_r_range_mm=(eye_r_min, eye_r_max),
        is_within_envelope=is_ok,
    )
