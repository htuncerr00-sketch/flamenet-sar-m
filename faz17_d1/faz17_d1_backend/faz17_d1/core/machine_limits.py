"""
core/machine_limits.py — Endüstriyel Makine Limiti Modeli + Yörünge Doğrulama
==============================================================================
Faz: ENDÜSTRİYEL ÜRETİM DOĞRULUĞU

Bu modül, `machine_envelope.py`'nin (statik konum zarfı) üzerine, ZAMAN-DOMENİ
yörünge doğrulaması ekler. Deterministik `TwinTimeline` (trajectory_builder)
girdisini alır ve fiziksel olarak gerçekleştirilebilir olmayan hareketleri
TESPİT EDER ve REDDEDER.

Analitik temel
--------------
Bir yörünge yalnızca tüm eksen türevleri makine kapasitesi içindeyse
gerçekleştirilebilir:

    |v_x(t)|     ≤ v_x,max           (hız / besleme)
    |a_x(t)|     ≤ a_x,max           (ivme)
    |j_x(t)|     ≤ j_x,max           (jerk)
    |ω_a(t)|     ≤ ω_a,max           (iş mili RPM doygunluğu)
    |α_a(t)|     ≤ α_a,max           (iş mili açısal ivme)
    |j_a(t)|     ≤ j_a,max           (iş mili açısal jerk)
    x_soft_min ≤ x(t) ≤ x_soft_max   (aşırı seyahat / overtravel)
    a(t) monoton (≥ tolerans)        (rotary desync = iş mili geri sıçraması)
    eye_r,min ≤ r_eye(t) ≤ eye_r,max (payout gözü seyahat limiti)

Türevler, zaman ızgarasının sonlu farkıyla (deterministik) hesaplanır:
    a_x[i] = (v_x[i] − v_x[i−1]) / dt
    j_x[i] = (a_x[i] − a_x[i−1]) / dt

Mühendislik varsayımları
------------------------
- TwinTimeline tekdüze dt'lidir (trajectory_builder garantisi).
- İş mili açısı a_deg KÜRESEL kümülatiftir ve normal çalışmada monoton artar;
  azalış (geri sıçrama) bir senkronizasyon hatası / plan kusurudur.
- Besleme hızı (feedrate) ≈ taşıyıcı hız büyüklüğü (G-code F kelimesi taşıyıcı
  eksenine bağlanır); birleşik vektör hız ayrıca raporlanır.
- Türev sınır kontrolleri %0.1 sayısal tolerans payı kullanır (sonlu fark
  gürültüsü için), `machine_envelope` ile tutarlı.

Başarısızlık modu analizi
-------------------------
- Doygun iş mili + hareketli taşıyıcı → sarma açısı korunamaz (desync).
- Aşırı jerk → tahrik titreşimi, fiber gerilim dalgalanması, adım kaybı.
- Overtravel → mekanik çarpışma / sert limit tetikleme.
- Ulaşılamayan besleme → kontrolör beslemeyi kırpar, açı kayar.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from .machine_envelope import MachineEnvelope
from .trajectory_builder import TwinTimeline


# Türev sınır kontrolleri için sayısal tolerans (sonlu fark gürültü payı)
_TOL = 1.001


@dataclass
class MachineLimits:
    """
    4-eksen filament sarma makinesinin tam dinamik limit kümesi.

    X ekseni : taşıyıcı (doğrusal, mm)
    A ekseni : iş mili (dönel, derece — sürekli)
    Eye      : payout gözü radyal seyahat (mm)
    """
    # ── Taşıyıcı (X) dinamik limitleri ───────────────────────────────────────
    max_carriage_velocity_mm_s: float = 83.3      # ≈ 5000 mm/dak
    max_carriage_accel_mm_s2: float = 500.0
    max_carriage_jerk_mm_s3: float = 5000.0

    # ── İş mili (A) dinamik limitleri ────────────────────────────────────────
    max_spindle_rpm: float = 300.0
    max_spindle_accel_deg_s2: float = 3600.0      # ≈ 10 RPM/s
    max_spindle_jerk_deg_s3: float = 36000.0

    # ── Eksen yumuşak limitleri (overtravel) ─────────────────────────────────
    x_soft_min_mm: float = -5.0
    x_soft_max_mm: float = 395.0

    # ── Homing ofsetleri (makine sıfırı ↔ iş parçası sıfırı) ─────────────────
    x_home_offset_mm: float = 0.0                 # x_makine = x_yol + ofset
    a_home_offset_deg: float = 0.0

    # ── Rotary wrap limiti ───────────────────────────────────────────────────
    # Kablo/besleme yönetimi nedeniyle izin verilen maksimum kümülatif dönme.
    # ≤ 0 → sınırsız sürekli dönme (slip-ring / sürekli besleme).
    max_rotary_wrap_deg: float = 0.0

    # ── Payout gözü seyahat limiti (radyal) ──────────────────────────────────
    eye_travel_min_mm: float = 60.0
    eye_travel_max_mm: float = 500.0

    def __post_init__(self) -> None:
        if self.max_carriage_velocity_mm_s <= 0:
            raise ValueError("max_carriage_velocity_mm_s > 0 olmalı")
        if self.max_spindle_rpm <= 0:
            raise ValueError("max_spindle_rpm > 0 olmalı")
        if self.x_soft_min_mm >= self.x_soft_max_mm:
            raise ValueError("x_soft_min >= x_soft_max — geçersiz seyahat aralığı")
        if self.eye_travel_min_mm >= self.eye_travel_max_mm:
            raise ValueError("eye_travel_min >= eye_travel_max — geçersiz göz aralığı")

    # ── Türetilmiş birim dönüşümleri ─────────────────────────────────────────

    @property
    def max_spindle_deg_s(self) -> float:
        return self.max_spindle_rpm * 360.0 / 60.0

    @property
    def max_spindle_accel_rpm_s(self) -> float:
        return self.max_spindle_accel_deg_s2 / 360.0 * 60.0

    @classmethod
    def from_envelope(cls, env: MachineEnvelope, **overrides) -> "MachineLimits":
        """
        Mevcut `MachineEnvelope`'tan dinamik limit kümesi türet.

        Jerk ve açısal ivme zarf modelinde bulunmadığından makul varsayılanlar
        kullanılır; **overrides ile geçersiz kılınabilir.
        """
        base = dict(
            max_carriage_velocity_mm_s=env.max_carriage_speed_mm_s,
            max_carriage_accel_mm_s2=env.max_carriage_accel_mm_s2,
            max_spindle_rpm=env.max_spindle_rpm,
            x_soft_min_mm=env.x_soft_min_mm,
            x_soft_max_mm=env.x_soft_max_mm,
            eye_travel_min_mm=env.eye_radial_min_mm,
            eye_travel_max_mm=env.eye_radial_max_mm,
        )
        base.update(overrides)
        return cls(**base)

    def summary(self) -> str:
        wrap = ("sürekli" if self.max_rotary_wrap_deg <= 0
                else f"{self.max_rotary_wrap_deg:.0f}°")
        return (
            f"MachineLimits: v_x≤{self.max_carriage_velocity_mm_s:.1f}mm/s "
            f"a_x≤{self.max_carriage_accel_mm_s2:.0f} j_x≤{self.max_carriage_jerk_mm_s3:.0f} | "
            f"RPM≤{self.max_spindle_rpm:.0f} α_a≤{self.max_spindle_accel_deg_s2:.0f}°/s² | "
            f"X∈[{self.x_soft_min_mm:.0f},{self.x_soft_max_mm:.0f}]mm | "
            f"wrap={wrap} | göz∈[{self.eye_travel_min_mm:.0f},{self.eye_travel_max_mm:.0f}]mm"
        )


# ── İhlal kayıtları ──────────────────────────────────────────────────────────

# İhlal türleri (sabit anahtarlar)
KIND_FEEDRATE = "feedrate"          # ulaşılamayan besleme (taşıyıcı hızı)
KIND_SPINDLE_SAT = "spindle_sat"    # iş mili RPM doygunluğu
KIND_ACCEL = "accel"                # ivme ihlali (X veya A)
KIND_JERK = "jerk"                  # jerk ihlali (X veya A)
KIND_OVERTRAVEL = "overtravel"      # X yumuşak limit dışı
KIND_ROTARY_DESYNC = "rotary_desync"  # iş mili geri sıçraması / wrap aşımı
KIND_EYE_TRAVEL = "eye_travel"      # payout gözü radyal seyahat dışı


@dataclass
class TrajectoryViolation:
    """Yörünge boyunca tek bir makine limiti ihlali."""
    t_s: float
    kind: str
    axis: str           # "X" | "A" | "eye"
    value: float        # ölçülen değer
    limit: float        # aşılan limit
    severity: float     # 0..1 (limit aşım oranı, kırpılmış)
    message: str


@dataclass
class MachineValidationReport:
    """`validate_trajectory_against_machine` çıktısı."""
    n_samples: int
    dt_s: float
    violations: List[TrajectoryViolation]

    # Tür başına sayımlar
    feedrate_count: int
    spindle_sat_count: int
    accel_count: int
    jerk_count: int
    overtravel_count: int
    rotary_desync_count: int
    eye_travel_count: int

    # Tepe değerler (teşhis)
    peak_carriage_velocity_mm_s: float
    peak_carriage_accel_mm_s2: float
    peak_carriage_jerk_mm_s3: float
    peak_spindle_rpm: float
    peak_spindle_accel_deg_s2: float
    peak_spindle_jerk_deg_s3: float
    x_range_mm: Tuple[float, float]

    is_realizable: bool
    critical_issues: List[str]
    warnings: List[str]

    def verdict(self) -> str:
        return "★★★ GERÇEKLEŞTİRİLEBİLİR ★★★" if self.is_realizable else "DURUN — GERÇEKLEŞTİRİLEMEZ"

    def summary(self) -> str:
        return (
            f"MakineDoğrulama: {self.verdict()} | {self.n_samples} örnek @ dt={self.dt_s*1000:.0f}ms | "
            f"besleme={self.feedrate_count} RPM_doygun={self.spindle_sat_count} "
            f"ivme={self.accel_count} jerk={self.jerk_count} "
            f"overtravel={self.overtravel_count} desync={self.rotary_desync_count} "
            f"göz={self.eye_travel_count} | "
            f"tepe: v_x={self.peak_carriage_velocity_mm_s:.1f}mm/s "
            f"RPM={self.peak_spindle_rpm:.1f}"
        )


def _finite_diff(arr: np.ndarray, dt: float) -> np.ndarray:
    """Geriye sonlu fark türevi; ilk örnek 0 (durağan başlangıç)."""
    d = np.zeros_like(arr)
    if len(arr) > 1:
        d[1:] = np.diff(arr) / dt
    return d


def validate_trajectory_against_machine(
    timeline: TwinTimeline,
    limits: MachineLimits,
    eye_r_mm: Optional[np.ndarray] = None,
    rotary_backstep_tol_deg: float = 1e-6,
) -> MachineValidationReport:
    """
    Deterministik yörüngeyi makine dinamik limitlerine karşı doğrula.

    Parametreler
    ----------
    timeline : Tekdüze dt'li TwinTimeline (trajectory_builder/winding_twin çıktısı).
    limits   : MachineLimits — tam dinamik limit kümesi.
    eye_r_mm : (N,) payout gözü radyal konumu (None ise göz seyahati kontrol edilmez).
    rotary_backstep_tol_deg : İş mili geri sıçraması için tolerans (desync eşiği).

    Döner
    -----
    MachineValidationReport — tespit edilen tüm ihlaller + gerçekleştirilebilirlik kararı.

    Tespit edilenler
    ----------------
    - ulaşılamayan besleme (|v_x| > v_x,max)
    - iş mili doygunluğu (RPM > RPM_max)
    - ivme ihlali (X ve A)
    - jerk ihlali (X ve A)
    - overtravel (X yumuşak limit + homing ofseti)
    - rotary desync (iş mili açısı geri gider veya wrap limiti aşılır)
    - payout gözü seyahat dışı
    """
    n = timeline.n_samples
    dt = timeline.dt_s
    violations: List[TrajectoryViolation] = []

    if n == 0:
        return MachineValidationReport(
            n_samples=0, dt_s=dt, violations=[],
            feedrate_count=0, spindle_sat_count=0, accel_count=0, jerk_count=0,
            overtravel_count=0, rotary_desync_count=0, eye_travel_count=0,
            peak_carriage_velocity_mm_s=0.0, peak_carriage_accel_mm_s2=0.0,
            peak_carriage_jerk_mm_s3=0.0, peak_spindle_rpm=0.0,
            peak_spindle_accel_deg_s2=0.0, peak_spindle_jerk_deg_s3=0.0,
            x_range_mm=(0.0, 0.0), is_realizable=True,
            critical_issues=[], warnings=[],
        )

    t = timeline.t_s
    v_x = timeline.carriage_v_mm_s
    a_x = _finite_diff(v_x, dt)
    j_x = _finite_diff(a_x, dt)

    omega_a = timeline.spindle_v_deg_s
    alpha_a = _finite_diff(omega_a, dt)
    j_a = _finite_diff(alpha_a, dt)

    rpm = timeline.rpm
    x_machine = timeline.x_mm + limits.x_home_offset_mm
    a_deg = timeline.a_deg

    def _add(i, kind, axis, value, limit, msg):
        sev = min(1.0, max(0.0, (abs(value) - abs(limit)) / max(abs(limit), 1e-9)))
        violations.append(TrajectoryViolation(
            t_s=float(t[i]), kind=kind, axis=axis,
            value=float(value), limit=float(limit), severity=sev, message=msg,
        ))

    counts = {KIND_FEEDRATE: 0, KIND_SPINDLE_SAT: 0, KIND_ACCEL: 0, KIND_JERK: 0,
              KIND_OVERTRAVEL: 0, KIND_ROTARY_DESYNC: 0, KIND_EYE_TRAVEL: 0}

    for i in range(n):
        # ── Ulaşılamayan besleme (taşıyıcı hızı) ──────────────────────────────
        if abs(v_x[i]) > limits.max_carriage_velocity_mm_s * _TOL:
            _add(i, KIND_FEEDRATE, "X", v_x[i], limits.max_carriage_velocity_mm_s,
                 f"t={t[i]:.2f}s: taşıyıcı hızı {v_x[i]:.1f}mm/s > "
                 f"{limits.max_carriage_velocity_mm_s:.1f}mm/s (ulaşılamayan besleme)")
            counts[KIND_FEEDRATE] += 1

        # ── İş mili doygunluğu ────────────────────────────────────────────────
        if rpm[i] > limits.max_spindle_rpm * _TOL:
            _add(i, KIND_SPINDLE_SAT, "A", rpm[i], limits.max_spindle_rpm,
                 f"t={t[i]:.2f}s: iş mili {rpm[i]:.1f}RPM > {limits.max_spindle_rpm:.1f}RPM (doygunluk)")
            counts[KIND_SPINDLE_SAT] += 1

        # ── İvme ihlalleri ────────────────────────────────────────────────────
        if abs(a_x[i]) > limits.max_carriage_accel_mm_s2 * _TOL:
            _add(i, KIND_ACCEL, "X", a_x[i], limits.max_carriage_accel_mm_s2,
                 f"t={t[i]:.2f}s: taşıyıcı ivmesi {a_x[i]:.0f}mm/s² > "
                 f"{limits.max_carriage_accel_mm_s2:.0f}mm/s²")
            counts[KIND_ACCEL] += 1
        if abs(alpha_a[i]) > limits.max_spindle_accel_deg_s2 * _TOL:
            _add(i, KIND_ACCEL, "A", alpha_a[i], limits.max_spindle_accel_deg_s2,
                 f"t={t[i]:.2f}s: iş mili ivmesi {alpha_a[i]:.0f}°/s² > "
                 f"{limits.max_spindle_accel_deg_s2:.0f}°/s²")
            counts[KIND_ACCEL] += 1

        # ── Jerk ihlalleri ────────────────────────────────────────────────────
        if abs(j_x[i]) > limits.max_carriage_jerk_mm_s3 * _TOL:
            _add(i, KIND_JERK, "X", j_x[i], limits.max_carriage_jerk_mm_s3,
                 f"t={t[i]:.2f}s: taşıyıcı jerk {j_x[i]:.0f}mm/s³ > "
                 f"{limits.max_carriage_jerk_mm_s3:.0f}mm/s³")
            counts[KIND_JERK] += 1
        if abs(j_a[i]) > limits.max_spindle_jerk_deg_s3 * _TOL:
            _add(i, KIND_JERK, "A", j_a[i], limits.max_spindle_jerk_deg_s3,
                 f"t={t[i]:.2f}s: iş mili jerk {j_a[i]:.0f}°/s³ > "
                 f"{limits.max_spindle_jerk_deg_s3:.0f}°/s³")
            counts[KIND_JERK] += 1

        # ── Overtravel (X yumuşak limit, homing ofseti dahil) ────────────────
        if x_machine[i] < limits.x_soft_min_mm or x_machine[i] > limits.x_soft_max_mm:
            _add(i, KIND_OVERTRAVEL, "X", x_machine[i], limits.x_soft_max_mm,
                 f"t={t[i]:.2f}s: X_makine={x_machine[i]:.2f}mm dışında "
                 f"[{limits.x_soft_min_mm:.1f}, {limits.x_soft_max_mm:.1f}]")
            counts[KIND_OVERTRAVEL] += 1

        # ── Payout gözü seyahat limiti ────────────────────────────────────────
        if eye_r_mm is not None:
            er = float(eye_r_mm[i])
            if er < limits.eye_travel_min_mm or er > limits.eye_travel_max_mm:
                _add(i, KIND_EYE_TRAVEL, "eye", er, limits.eye_travel_max_mm,
                     f"t={t[i]:.2f}s: göz r={er:.1f}mm dışında "
                     f"[{limits.eye_travel_min_mm:.1f}, {limits.eye_travel_max_mm:.1f}]")
                counts[KIND_EYE_TRAVEL] += 1

    # ── Rotary desync: iş mili açısı geri sıçraması ──────────────────────────
    if n > 1:
        da = np.diff(a_deg)
        backstep_idx = np.where(da < -rotary_backstep_tol_deg)[0]
        for bi in backstep_idx:
            i = int(bi) + 1
            _add(i, KIND_ROTARY_DESYNC, "A", da[bi], 0.0,
                 f"t={t[i]:.2f}s: iş mili açısı geri gitti (Δa={da[bi]:.3f}°) — "
                 f"senkronizasyon kaybı")
            counts[KIND_ROTARY_DESYNC] += 1

    # ── Rotary wrap limiti ───────────────────────────────────────────────────
    total_wrap = float(a_deg[-1] - a_deg[0]) if n else 0.0
    if limits.max_rotary_wrap_deg > 0 and abs(total_wrap) > limits.max_rotary_wrap_deg:
        _add(n - 1, KIND_ROTARY_DESYNC, "A", total_wrap, limits.max_rotary_wrap_deg,
             f"kümülatif dönme {total_wrap:.0f}° > wrap limiti "
             f"{limits.max_rotary_wrap_deg:.0f}° (kablo yönetimi)")
        counts[KIND_ROTARY_DESYNC] += 1

    # ── Tepe değerler ─────────────────────────────────────────────────────────
    peak_vx = float(np.max(np.abs(v_x)))
    peak_ax = float(np.max(np.abs(a_x)))
    peak_jx = float(np.max(np.abs(j_x)))
    peak_rpm = float(np.max(rpm))
    peak_aa = float(np.max(np.abs(alpha_a)))
    peak_ja = float(np.max(np.abs(j_a)))
    x_lo = float(np.min(x_machine))
    x_hi = float(np.max(x_machine))

    # ── Karar ─────────────────────────────────────────────────────────────────
    critical: List[str] = []
    warnings: List[str] = []
    if counts[KIND_FEEDRATE]:
        critical.append(f"{counts[KIND_FEEDRATE]} örnekte ulaşılamayan besleme")
    if counts[KIND_SPINDLE_SAT]:
        critical.append(f"{counts[KIND_SPINDLE_SAT]} örnekte iş mili doygunluğu")
    if counts[KIND_ACCEL]:
        critical.append(f"{counts[KIND_ACCEL]} ivme ihlali")
    if counts[KIND_OVERTRAVEL]:
        critical.append(f"{counts[KIND_OVERTRAVEL]} overtravel ihlali")
    if counts[KIND_ROTARY_DESYNC]:
        critical.append(f"{counts[KIND_ROTARY_DESYNC]} rotary desync")
    if counts[KIND_EYE_TRAVEL]:
        critical.append(f"{counts[KIND_EYE_TRAVEL]} göz seyahat ihlali")
    if counts[KIND_JERK]:
        warnings.append(f"{counts[KIND_JERK]} jerk ihlali (titreşim/gerilim dalgalanması riski)")

    is_realizable = len(critical) == 0

    return MachineValidationReport(
        n_samples=n, dt_s=dt, violations=violations,
        feedrate_count=counts[KIND_FEEDRATE],
        spindle_sat_count=counts[KIND_SPINDLE_SAT],
        accel_count=counts[KIND_ACCEL],
        jerk_count=counts[KIND_JERK],
        overtravel_count=counts[KIND_OVERTRAVEL],
        rotary_desync_count=counts[KIND_ROTARY_DESYNC],
        eye_travel_count=counts[KIND_EYE_TRAVEL],
        peak_carriage_velocity_mm_s=peak_vx,
        peak_carriage_accel_mm_s2=peak_ax,
        peak_carriage_jerk_mm_s3=peak_jx,
        peak_spindle_rpm=peak_rpm,
        peak_spindle_accel_deg_s2=peak_aa,
        peak_spindle_jerk_deg_s3=peak_ja,
        x_range_mm=(x_lo, x_hi),
        is_realizable=is_realizable,
        critical_issues=critical,
        warnings=warnings,
    )
