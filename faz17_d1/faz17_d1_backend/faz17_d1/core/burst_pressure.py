"""
core/burst_pressure.py — Tahmini Patlama Basıncı (Faz 23 ENG-5)
=================================================================

Bu modül silindirik ve küresel kompozit basınçlı kapların patlama
basıncını (P_burst) iki yaklaşımla tahmin eder:

  • NETTING (fiber-sadece) — konservatif analitik üst sınır
  • CLT + Tsai-Wu (First-Ply-Failure) — daha gerçekçi sayısal alt sınır

Patlama testlerine karşı tipik korelasyon (Vasiliev 2009, Tab. 5.4):
  - Net netting: gerçek patlamanın ~%80-95'i (matris etkisi ihmali nedeniyle
    konservatif olabilir, ama tasarımda iyi bir BB üst sınırı)
  - CLT-FPF: gerçek patlamanın ~%70-90'ı (ilk ply hasar başlangıcı,
    progresif değil)

================================================================================
MATEMATİKSEL MODEL
================================================================================

1. NETTING burst (silindirik, kombine hoop+helical):
   Verilen laminat (t_helical_total + t_hoop_total) ve allowable σ_f için:
       Eksenel denge: σ_f · cos²α · t_α = P·D/4
       Çevre denge:   σ_f · sin²α · t_α + σ_f · t_h = P·D/2
   Tek bilinmeyen P olarak çözülür:
       P_axial = 4·σ_f·t_α·cos²α / D
       P_hoop  = 2·σ_f·(t_α·sin²α + t_h) / D
       P_burst = min(P_axial, P_hoop)

2. NETTING burst (küresel):
       P_burst = 4·σ_f·t / D

3. CLT + Tsai-Wu burst:
   Birim basınç P_unit altında N_x, N_y hesaplanır (membran kabuk):
       N_x_unit = P_unit·D/4     (silindirik)
       N_y_unit = P_unit·D/2
   FPF analizi yapılır; en kritik plyde min_SR (strength ratio) bulunur.
       P_burst_CLT = P_unit · min_SR
   Bu, orantılı yükleme varsayımı altında geçerlidir (basınç tek değişken).

4. Çevresel knockdown uygulanmış design burst:
       P_burst_design = P_burst · K_total
   K_total = EngineeringMaterial.environment.K_total

================================================================================
VARSAYIMLAR
================================================================================

VARSAYIM-1: İnce cidar (D/t > 20). Membran kabuk teorisi geçerli.
VARSAYIM-2: Kabuk uçları (boss, kubbe geçişleri) ihmal — sadece silindirik
            (orta) bölge analiz edilir. Boss yakın bölge ENG-7 PV
            sizing'inde ayrı incelenir.
VARSAYIM-3: Orantılı yükleme — P arttıkça N_x, N_y aynı oranda artar.
VARSAYIM-4: İlk-ply-failure = patlama yaklaşımı (CLT). Gerçekte progresif
            hasar P_burst_real > P_FPF olabilir; bu modül konservatif kalır.
VARSAYIM-5: Netting yaklaşımı matris katkısını sıfır kabul eder.
            CLT yaklaşımı matris katkısını dahil eder.
VARSAYIM-6: Boss takviyesi ihmal — kutuplarda yığılma faktörü dahil değil.

================================================================================
SINIRLAMALAR
================================================================================

SINIR-1: Sadece silindirik ve küresel geometri. Dome detaylı patlama
         analizi (boss başlatmalı) bu sürümde yok.
SINIR-2: Termal/higro etkiler dahil değil.
SINIR-3: Bend-twist kuplajı yok (simetrik laminatlarda zaten 0).
SINIR-4: Progresif hasar yok — gerçek patlama FPF + ek dayanım.

================================================================================
DOĞRULAMA STRATEJİSİ
================================================================================

DV-1: Magic angle netting → P_axial = P_hoop (denge).
DV-2: Hoop-fazla laminat → P_axial < P_hoop ⟹ eksenel kritik.
DV-3: Saf 0° (eksenel) laminat → P_hoop = 0 ⟹ hoop kritik.
DV-4: Vasiliev Worked Example 5.2:
        P=20 MPa, D=200 mm, σ_f=2000 MPa, magic angle, t=1.5 mm →
        P_burst_netting = 20 MPa (geri tutarlılık)
DV-5: CLT_burst > netting_burst (matris katkısı pozitif).
DV-6: Knockdown uygulamak P_design < P_burst sağlar.

================================================================================
REFERANSLAR
================================================================================

[1] Vasiliev, V.V., "Composite Pressure Vessels", 2009, Chapter 5
    (s.180-230).
[2] Peters, S.T., "Composite Filament Windings", 2011, Chapter 7-8.
[3] AIAA S-080-1998, "Space Systems — Metallic Pressure Vessels".
[4] ISO 11119-2:2020, "Composite gas cylinders".
[5] Roark's Formulas for Stress and Strain, 8th ed., 2012, Chapter 13.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional

from .material_allowables import EngineeringMaterial, LaminaProperties
from .clt_engine import (
    LaminateStackup, LaminateABD, compute_ABD,
    make_helical_hoop_stackup,
)
from .failure_criterion import first_ply_failure, FPFResult


# ── Sonuç veri yapıları ──────────────────────────────────────────────────────

@dataclass
class BurstResult:
    """Tek geometri × laminat patlama analizi sonucu."""
    P_burst_MPa: float                  # tahmin edilen patlama basıncı
    method: str                          # "netting" | "clt_fpf"
    geometry: str                        # "cylinder" | "sphere"
    diameter_mm: float
    laminate_thickness_mm: float
    limiting_mode: str                   # "axial" | "hoop" | "ply_failure"
    critical_ply_index: Optional[int]    # CLT: kritik ply
    knockdown_applied: float             # K_total uygulandı
    P_burst_design_MPa: float            # = P_burst · K_total
    notes: str = ""

    def summary(self) -> str:
        return (
            f"Burst[{self.method}, {self.geometry}]: "
            f"D={self.diameter_mm:.0f}mm t={self.laminate_thickness_mm:.2f}mm "
            f"P_burst={self.P_burst_MPa:.2f}MPa "
            f"(design={self.P_burst_design_MPa:.2f}MPa, "
            f"K={self.knockdown_applied:.2f}, mod='{self.limiting_mode}')"
        )


# ── Netting yaklaşımı ────────────────────────────────────────────────────────

def burst_pressure_netting_cylinder(
    diameter_mm: float,
    t_helical_mm: float,
    alpha_deg: float,
    t_hoop_mm: float,
    sigma_fiber_MPa: float,
) -> tuple[float, str]:
    """
    Silindirik kabuk için netting patlama basıncı.

    Geri dönüş: (P_burst_MPa, limiting_mode)
        limiting_mode ∈ {"axial", "hoop"}
    """
    if diameter_mm <= 0:
        raise ValueError("diameter_mm > 0")
    if t_helical_mm < 0 or t_hoop_mm < 0:
        raise ValueError("kalınlıklar ≥ 0")
    if not (0.0 < alpha_deg < 90.0):
        # Hoop-only durumu için α=90 izin ver
        if abs(alpha_deg - 90.0) > 1e-6:
            raise ValueError(f"α ∈ (0,90): {alpha_deg}")

    alpha_rad = math.radians(alpha_deg)
    sin2 = math.sin(alpha_rad) ** 2
    cos2 = math.cos(alpha_rad) ** 2

    # Eksenel denge: 4·σ_f·t_α·cos²α / D
    if t_helical_mm > 0 and cos2 > 1e-12:
        P_axial = 4.0 * sigma_fiber_MPa * t_helical_mm * cos2 / diameter_mm
    else:
        P_axial = float("inf")  # eksenel yük taşıyacak ply yok ⟹ kritik değil

    # Çevre denge: 2·σ_f·(t_α·sin²α + t_h) / D
    P_hoop = 2.0 * sigma_fiber_MPa * (t_helical_mm * sin2 + t_hoop_mm) / diameter_mm

    if P_axial < P_hoop:
        return P_axial, "axial"
    return P_hoop, "hoop"


def burst_pressure_netting_sphere(
    diameter_mm: float,
    thickness_mm: float,
    sigma_fiber_MPa: float,
) -> float:
    """Küresel kabuk netting burst: P = 4·σ_f·t / D."""
    if diameter_mm <= 0 or thickness_mm <= 0 or sigma_fiber_MPa <= 0:
        raise ValueError("D, t, σ_f > 0 olmalı")
    return 4.0 * sigma_fiber_MPa * thickness_mm / diameter_mm


# ── CLT + FPF yaklaşımı ──────────────────────────────────────────────────────

def burst_pressure_clt_cylinder(
    diameter_mm: float,
    stackup: LaminateStackup,
    criterion: str = "tsai_wu",
    P_unit_MPa: float = 1.0,
) -> tuple[float, FPFResult]:
    """
    CLT + FPF kullanarak silindirik kabuk patlama basıncı.

    Membran yük (birim basınç için):
        N_x = P·D/4    (eksenel)
        N_y = P·D/2    (çevre/hoop)
    FPF analizi P_unit altında min_SR'yi bulur; orantılı yükleme:
        P_burst = P_unit · min_SR

    Geri dönüş: (P_burst_MPa, FPFResult)
    """
    if diameter_mm <= 0:
        raise ValueError("diameter_mm > 0")
    if P_unit_MPa <= 0:
        raise ValueError("P_unit_MPa > 0")
    abd = compute_ABD(stackup)
    N_x = P_unit_MPa * diameter_mm / 4.0
    N_y = P_unit_MPa * diameter_mm / 2.0
    res = first_ply_failure(abd, N_x, N_y, 0.0, criterion=criterion)
    P_burst = P_unit_MPa * res.min_SR
    return P_burst, res


def burst_pressure_clt_sphere(
    diameter_mm: float,
    stackup: LaminateStackup,
    criterion: str = "tsai_wu",
    P_unit_MPa: float = 1.0,
) -> tuple[float, FPFResult]:
    """
    Küresel kabuk CLT + FPF burst.

    Membran yük (isotropik, σ_meridian = σ_hoop = PD/(4t)):
        N_x = N_y = P·D/4
    """
    if diameter_mm <= 0 or P_unit_MPa <= 0:
        raise ValueError("D, P_unit > 0")
    abd = compute_ABD(stackup)
    N_eq = P_unit_MPa * diameter_mm / 4.0
    res = first_ply_failure(abd, N_eq, N_eq, 0.0, criterion=criterion)
    return P_unit_MPa * res.min_SR, res


# ── Üst seviye orkestra fonksiyonu ───────────────────────────────────────────

def estimate_burst_cylinder(
    diameter_mm: float,
    material: EngineeringMaterial,
    alpha_deg: float,
    n_helical_pairs: int,
    n_hoop: int,
    thickness_per_ply_mm: Optional[float] = None,
    method: str = "clt_fpf",
    basis: str = "B",
) -> BurstResult:
    """
    Silindirik filament sarma basınçlı kap için patlama basıncı tahmini.

    Argümanlar:
        diameter_mm          : İç çap.
        material             : EngineeringMaterial (allowables + ortam).
        alpha_deg            : Helisel sarma açısı.
        n_helical_pairs      : ±α çift sayısı (yarı laminat).
        n_hoop               : 90° hoop ply sayısı (yarı laminat).
        thickness_per_ply_mm : Ply başına kalınlık. None ⟹ lamina nominal.
        method               : "netting" | "clt_fpf"
        basis                : Allowable basis "A" | "B" | "MEAN"

    Geri dönüş: BurstResult
    """
    if thickness_per_ply_mm is None:
        thickness_per_ply_mm = material.lamina.nominal_ply_thickness_mm

    # Tam yığını oluştur (simetrik balanslı)
    stackup = make_helical_hoop_stackup(
        alpha_deg=alpha_deg,
        n_helical_pairs=n_helical_pairs,
        n_hoop=n_hoop,
        thickness_per_ply_mm=thickness_per_ply_mm,
        lamina=material.lamina,
    )
    h_total = stackup.total_thickness_mm()
    t_helical = 2 * n_helical_pairs * thickness_per_ply_mm * 2  # ±α çift × 2 (ayna)
    t_hoop = n_hoop * thickness_per_ply_mm * 2                  # ayna

    K = material.environment.K_total

    if method == "netting":
        sigma_f = material.design_X_t(basis=basis) / K  # netting design strength
        # sigma_f zaten K_total ile çarpılmış idi → ham allowable elde et
        # design_X_t(basis) = X_basis · K  →  X_basis = design / K
        # Burada netting yaklaşımı için ham (knockdown'sız) σ_f kullan;
        # K'yi sonra P_design'da uygula.
        P_burst, mode = burst_pressure_netting_cylinder(
            diameter_mm=diameter_mm,
            t_helical_mm=t_helical, alpha_deg=alpha_deg,
            t_hoop_mm=t_hoop,
            sigma_fiber_MPa=sigma_f,
        )
        return BurstResult(
            P_burst_MPa=P_burst,
            method="netting",
            geometry="cylinder",
            diameter_mm=diameter_mm,
            laminate_thickness_mm=h_total,
            limiting_mode=mode,
            critical_ply_index=None,
            knockdown_applied=K,
            P_burst_design_MPa=P_burst * K,
            notes=f"alpha={alpha_deg:.2f}° n_hel={n_helical_pairs} n_hoop={n_hoop}",
        )
    elif method == "clt_fpf":
        P_burst, fpf = burst_pressure_clt_cylinder(
            diameter_mm=diameter_mm, stackup=stackup,
            criterion="tsai_wu", P_unit_MPa=1.0,
        )
        # Basis düzeltmesi: A-basis için Tsai-Wu dayanım azaltma yapmalı.
        # En basit yaklaşım: P_burst ölçek faktörü = (X_basis/X_mean)
        # Tüm dayanımlar birlikte orantısal ölçeklendiğinde Tsai-Wu kuadratik
        # form da orantısal olur — yaklaşık.
        scale_basis = _basis_scale_factor(material.lamina, basis)
        P_burst_scaled = P_burst * scale_basis
        return BurstResult(
            P_burst_MPa=P_burst_scaled,
            method="clt_fpf",
            geometry="cylinder",
            diameter_mm=diameter_mm,
            laminate_thickness_mm=h_total,
            limiting_mode="ply_failure",
            critical_ply_index=fpf.critical_ply_index,
            knockdown_applied=K,
            P_burst_design_MPa=P_burst_scaled * K,
            notes=(
                f"alpha={alpha_deg:.2f}° n_hel={n_helical_pairs} "
                f"n_hoop={n_hoop} crit_ply={fpf.critical_ply_index} "
                f"min_SR={fpf.min_SR:.3f}"
            ),
        )
    else:
        raise ValueError(f"method ∈ {{'netting','clt_fpf'}}: {method!r}")


def estimate_burst_sphere(
    diameter_mm: float,
    material: EngineeringMaterial,
    thickness_mm: float,
    basis: str = "B",
) -> BurstResult:
    """
    Küresel kabuk için netting burst pressure (basit).

    Quasi-iso ya da çoklu açılı kütüphane çözüm için CLT_sphere kullanılabilir
    (gelecek genişleme).
    """
    sigma_f_design = material.design_X_t(basis=basis)
    P_burst = burst_pressure_netting_sphere(
        diameter_mm=diameter_mm,
        thickness_mm=thickness_mm,
        sigma_fiber_MPa=sigma_f_design,
    )
    # sigma_f_design zaten knockdown ile çarpılmış → bu P_burst = P_design
    K = material.environment.K_total
    # Geriye uyumluluk için P_burst'ü hammal'a çevir:
    P_burst_raw = P_burst / K if K > 1e-9 else P_burst
    return BurstResult(
        P_burst_MPa=P_burst_raw,
        method="netting",
        geometry="sphere",
        diameter_mm=diameter_mm,
        laminate_thickness_mm=thickness_mm,
        limiting_mode="isotropic",
        critical_ply_index=None,
        knockdown_applied=K,
        P_burst_design_MPa=P_burst_raw * K,
        notes=f"t={thickness_mm:.2f}mm σ_f_basis={basis}",
    )


# ── Yardımcı: basis ölçek faktörü ────────────────────────────────────────────

def _basis_scale_factor(lam: LaminaProperties, basis: str) -> float:
    """
    Tsai-Wu dayanımları için A/B-basis ölçek faktörü.

    Yaklaşım: X_basis/X_mean ortalama ölçek faktörü olarak alınır
    (tüm bileşenler aynı CV ile ölçeklendiğinden, kuadratik form orantılıdır).
    """
    basis = basis.upper()
    cv = lam.coefficient_of_variation
    if basis == "MEAN":
        return 1.0
    if basis == "B":
        return max(0.0, 1.0 - 1.282 * cv)
    if basis == "A":
        return max(0.0, 1.0 - 2.326 * cv)
    raise ValueError(f"basis ∈ {{'A','B','MEAN'}}: {basis!r}")
