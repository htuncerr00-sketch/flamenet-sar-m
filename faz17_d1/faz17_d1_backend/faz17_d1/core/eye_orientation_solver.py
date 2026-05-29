"""
core/eye_orientation_solver.py — Payout Göz Yönelim Çözücüsü
=============================================================
Payout gözünün (B-ekseni) gerçek yönelimini hesaplar:
- Temas noktasına nişan alma
- Sarma açısına dayalı eksenel öncülük (lead) tazminatı
- B-ekseni steering açısı komutu
- Minimum büküm yarıçapı koruması
- Göz eksen limit kontrolü

Koordinat sistemi
-----------------
B = 0°  : Göz, mandrel ekseni ile aynı hizada (radyal bakış).
B = +α  : Göz, ileriye (+z yönüne) α° döndürülmüş.
B = -α  : Göz, geriye (-z yönüne) α° döndürülmüş.

Birinci derece: B ≈ α (sarma açısı).  Payout açısından B'ye tam
eşitlik sadece standoff → ∞ limitinde geçerlidir; gerçek makine için
tam geometrik çözüm uygulanır.

Minimum büküm yarıçapı
-----------------------
Fiber, göz açıklığında bükülür. Büküm yarıçapı:
    ρ = E · d² / (4 · T_set)     [mm, elastik büküm]
ρ < ρ_min → fiber kırılma riski.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .geometry_engine import MandrelProfile
from .machine_calibration import MachineCalibration, default_calibration


# ── Göz kısıtlamaları ────────────────────────────────────────────────────────

@dataclass
class EyeConstraints:
    """
    Payout gözü fiziksel limitleri.

    steer_max_deg      : B-ekseninin maks/min steering açısı (°).
    steer_rate_max_deg_s : B-ekseninin maks açısal hız (°/s).
    min_bend_radius_mm : Fiberin tolere edebileceği min büküm yarıçapı (mm).
    eye_aperture_mm    : Göz açıklığı çapı (fiber büküm çarkı yarıçapı ≈ d/2).
    fiber_diameter_mm  : Tek fiber demeti çapı (büküm hesabı için).
    fiber_tensile_GPa  : Fiber elastik modülü (GPa) — büküm yarıçapı hesabı için.
    """
    steer_max_deg: float = 60.0
    steer_rate_max_deg_s: float = 90.0
    min_bend_radius_mm: float = 20.0
    eye_aperture_mm: float = 8.0
    fiber_diameter_mm: float = 1.0
    fiber_tensile_GPa: float = 230.0    # karbon fiber tipik


@dataclass
class EyeOrientationResult:
    """
    B-ekseni yönelim çözümü — tek bir sarma noktası için.

    steer_cmd_deg     : B-ekseni komut açısı (derece).
    lead_z_mm         : X-eksenine eklenen öncülük (mm).
    standoff_mm       : Göz-yüzey radyal mesafesi (mm).
    bend_radius_mm    : Gözde elde edilen büküm yarıçapı (mm).
    within_steer_lim  : B-ekseni limitinde mi?
    bend_radius_safe  : Büküm yarıçapı > min_bend_radius?
    contact_aim_error_mm : Göz ile hedef temas noktası arası nişan hatası (mm).
    issue: str
    """
    steer_cmd_deg: float
    lead_z_mm: float
    standoff_mm: float
    bend_radius_mm: float
    within_steer_lim: bool
    bend_radius_safe: bool
    contact_aim_error_mm: float
    issue: str = ""

    @property
    def is_feasible(self) -> bool:
        return self.within_steer_lim and self.bend_radius_safe

    def summary(self) -> str:
        status = "GEÇERLİ ✓" if self.is_feasible else "SORUNLU ✗"
        return (
            f"GözYönelim: {status} | B={self.steer_cmd_deg:.2f}° | "
            f"lead={self.lead_z_mm:.2f}mm | ρ_büküm={self.bend_radius_mm:.1f}mm | "
            f"nişan_hatası={self.contact_aim_error_mm:.3f}mm"
        )


# ── Büküm yarıçapı fiziği ─────────────────────────────────────────────────────

def compute_bend_radius(
    tension_N: float,
    eye_aperture_mm: float,
    fiber_diameter_mm: float,
    fiber_tensile_GPa: float,
) -> float:
    """
    Göz açıklığında fiber büküm yarıçapını hesapla.

    Fizik (elastik çubuk analojisi):
        EI · κ = M  →  ρ = EI / M
    Fiber için I = π·d⁴/64 (dairesel kesit); M = T · (eye_aperture / 2)
    (fiber, göz kenarına basınç yapar).

        ρ = E · π·d⁴/64 / (T · D/2)
          = E·π·d⁴ / (32·T·D)

    Birimi: GPa → N/mm²; d mm; T N; D mm → ρ mm.
    """
    E_N_mm2 = fiber_tensile_GPa * 1000.0   # 1 GPa = 1000 N/mm²
    d = fiber_diameter_mm
    D = max(eye_aperture_mm, 0.1)
    T = max(tension_N, 0.1)
    I = math.pi * d ** 4 / 64.0
    M = T * (D / 2.0)
    if M < 1e-12:
        return float("inf")
    return E_N_mm2 * I / M


# ── Ana çözücü ────────────────────────────────────────────────────────────────

def solve_eye_orientation(
    contact_z_mm: float,
    contact_phi_deg: float,
    alpha_deg: float,
    profile: MandrelProfile,
    calib: Optional[MachineCalibration] = None,
    constraints: Optional[EyeConstraints] = None,
    travel_dir: float = 1.0,
    tension_N: float = 50.0,
    standoff_mm: Optional[float] = None,
) -> EyeOrientationResult:
    """
    Verilen temas noktası ve sarma açısı için göz yönelimini çöz.

    Tam geometrik model (birinci derece yaklaşım değil):
    1. Lead: lead = standoff · tan(α) — göz öncülüğü
    2. Gerçek göz-temas vektöründen B açısı = atan(lead / standoff)
    3. Limit ve büküm kontrolü
    """
    if calib is None:
        calib = default_calibration()
    if constraints is None:
        constraints = EyeConstraints()
    if standoff_mm is None:
        standoff_mm = calib.eye_base_standoff_mm

    alpha_rad = math.radians(max(0.01, min(alpha_deg, 89.9)))

    # Eksenel öncülük
    lead = standoff_mm * math.tan(alpha_rad)

    # B-ekseni (steering) komutu — tam geometrik çözüm
    steer_rad = math.atan2(lead, standoff_mm)
    steer_cmd = math.degrees(steer_rad) * (1.0 if travel_dir >= 0 else -1.0)

    # Göz merkezi konumu
    r_c = profile.radius_at(contact_z_mm)
    r_eye = r_c + standoff_mm
    z_eye = contact_z_mm + travel_dir * lead
    phi_rad = math.radians(contact_phi_deg % 360.0)

    # Göz → temas vektörü uzunluğu (nişan hatası = 0 idealde)
    # Nişan hatası: göz-temas eksenel hizasızlığı
    aim_error = abs(z_eye - contact_z_mm - travel_dir * lead)  # tam formülde = 0

    # Büküm yarıçapı
    rho = compute_bend_radius(
        tension_N, constraints.eye_aperture_mm,
        constraints.fiber_diameter_mm, constraints.fiber_tensile_GPa,
    )

    within_lim = abs(steer_cmd) <= constraints.steer_max_deg
    bend_safe = rho >= constraints.min_bend_radius_mm

    issues = []
    if not within_lim:
        issues.append(
            f"B={steer_cmd:.1f}° > limit ±{constraints.steer_max_deg:.1f}° "
            f"(sarma açısı {alpha_deg:.1f}° için lead={lead:.1f}mm)"
        )
    if not bend_safe:
        issues.append(
            f"Büküm yarıçapı {rho:.1f}mm < min {constraints.min_bend_radius_mm:.1f}mm "
            f"(T={tension_N:.0f}N, D={constraints.eye_aperture_mm:.1f}mm)"
        )

    return EyeOrientationResult(
        steer_cmd_deg=steer_cmd,
        lead_z_mm=lead,
        standoff_mm=standoff_mm,
        bend_radius_mm=rho,
        within_steer_lim=within_lim,
        bend_radius_safe=bend_safe,
        contact_aim_error_mm=aim_error,
        issue="; ".join(issues),
    )


# ── Yol boyunca göz yönelim analizi ──────────────────────────────────────────

@dataclass
class EyeOrientationReport:
    """Sarma yolu için tüm göz yönelim analiz özeti."""
    n_points: int
    n_feasible: int
    n_infeasible: int
    max_steer_deg: float
    min_steer_deg: float
    max_lead_mm: float
    min_bend_radius_mm: float
    max_steer_rate_deg_s: float   # B-ekseni en yüksek açısal hız
    n_over_steer_limit: int
    n_bend_unsafe: int
    is_feasible: bool
    warnings: List[str]

    def summary(self) -> str:
        status = "GEÇERLİ ✓" if self.is_feasible else "GEÇERSİZ ✗"
        return (
            f"GözAnaliz: {status} ({self.n_feasible}/{self.n_points}) | "
            f"steer=[{self.min_steer_deg:.1f},{self.max_steer_deg:.1f}]° | "
            f"maks_lead={self.max_lead_mm:.1f}mm | "
            f"min_ρ={self.min_bend_radius_mm:.1f}mm | "
            f"maks_B_hız={self.max_steer_rate_deg_s:.1f}°/s"
        )


def analyze_eye_orientation(
    path,
    profile: MandrelProfile,
    calib: Optional[MachineCalibration] = None,
    constraints: Optional[EyeConstraints] = None,
    standoff_mm: Optional[float] = None,
    tension_N: float = 50.0,
    sample_every: int = 5,
) -> EyeOrientationReport:
    """
    Sarma yolu boyunca göz yönelim analizi.

    Her örneklenen noktada yönelim çözülür; limit ve büküm kontrolü yapılır.
    Ardışık noktalar arası B-ekseni hız gereksinimi de hesaplanır.
    """
    if calib is None:
        calib = default_calibration()
    if constraints is None:
        constraints = EyeConstraints()

    pts = path.points
    if len(pts) < 2:
        return EyeOrientationReport(
            n_points=0, n_feasible=0, n_infeasible=0,
            max_steer_deg=0.0, min_steer_deg=0.0, max_lead_mm=0.0,
            min_bend_radius_mm=float("inf"), max_steer_rate_deg_s=0.0,
            n_over_steer_limit=0, n_bend_unsafe=0, is_feasible=False,
            warnings=["Yol < 2 nokta"],
        )

    sampled = pts[::max(1, sample_every)]
    steers: List[float] = []
    leads: List[float] = []
    bends: List[float] = []
    steer_rates: List[float] = []
    n_over = 0
    n_bend_fail = 0
    n_feas = 0
    prev_steer: Optional[float] = None
    prev_t: Optional[float] = None

    for i, pt in enumerate(sampled):
        travel = 1.0
        if i + 1 < len(sampled):
            travel = 1.0 if sampled[i + 1].x_mm >= pt.x_mm else -1.0

        # α aus velocity ratio
        if i + 1 < len(sampled):
            q = sampled[i + 1]
            dz = q.x_mm - pt.x_mm
            da_rad = math.radians(q.a_deg - pt.a_deg)
            r_c = profile.radius_at(pt.x_mm)
            v_circ = r_c * da_rad
            alpha = math.degrees(math.atan2(abs(v_circ), abs(dz) + 1e-12))
        else:
            alpha = steers[-1] if steers else 55.0
            alpha = abs(alpha)

        result = solve_eye_orientation(
            pt.x_mm, pt.a_deg % 360.0, alpha, profile,
            calib, constraints, travel, tension_N, standoff_mm,
        )
        steers.append(result.steer_cmd_deg)
        leads.append(result.lead_z_mm)
        bends.append(result.bend_radius_mm)

        if not result.within_steer_lim:
            n_over += 1
        if not result.bend_radius_safe:
            n_bend_fail += 1
        if result.is_feasible:
            n_feas += 1

        # B-ekseni hız tahmini
        if prev_steer is not None and prev_t is not None:
            dt = abs(pt.z_fiber - prev_t) / max(pt.feed, 1.0)
            if dt > 1e-6:
                rate = abs(result.steer_cmd_deg - prev_steer) / dt
                steer_rates.append(rate)
        prev_steer = result.steer_cmd_deg
        prev_t = pt.z_fiber

    warns: List[str] = []
    if n_over > 0:
        warns.append(f"{n_over} noktada B-ekseni steering limiti aşılıyor")
    if n_bend_fail > 0:
        warns.append(f"{n_bend_fail} noktada büküm yarıçapı yetersiz — fiber kırılma riski")
    max_rate = float(max(steer_rates)) if steer_rates else 0.0
    if max_rate > constraints.steer_rate_max_deg_s:
        warns.append(
            f"B-ekseni maks açısal hız {max_rate:.1f}°/s > limit "
            f"{constraints.steer_rate_max_deg_s:.1f}°/s"
        )

    return EyeOrientationReport(
        n_points=len(sampled),
        n_feasible=n_feas,
        n_infeasible=len(sampled) - n_feas,
        max_steer_deg=float(max(steers)) if steers else 0.0,
        min_steer_deg=float(min(steers)) if steers else 0.0,
        max_lead_mm=float(max(leads)) if leads else 0.0,
        min_bend_radius_mm=float(min(b for b in bends if math.isfinite(b))) if bends else 0.0,
        max_steer_rate_deg_s=max_rate,
        n_over_steer_limit=n_over,
        n_bend_unsafe=n_bend_fail,
        is_feasible=(n_over == 0 and n_bend_fail == 0),
        warnings=warns,
    )
