"""
core/failure_criterion.py — Lamina Hasar Kriterleri (Faz 23 ENG-4)
======================================================================

Bu modül lamina seviyesi hasar değerlendirmesi için yaygın kriterleri
uygular: Tsai-Wu (quadratic), Tsai-Hill, Maximum Stress, Maximum Strain
ve Hashin (basitleştirilmiş). Ayrıca First-Ply-Failure (FPF) analizi için
laminat çözümü ile birleştirilmiş bir orkestra fonksiyonu sağlar.

================================================================================
MATEMATİKSEL MODEL
================================================================================

1. Tsai-Wu kuadratik hasar indeksi (Failure Index, FI):

       FI = F_1·σ_1 + F_2·σ_2
          + F_11·σ_1² + F_22·σ_2² + F_66·τ_12²
          + 2·F_12·σ_1·σ_2

   Burada F_ij sabitleri material_allowables.LaminaProperties üzerinden
   gelir. FI < 1 → güvenli, FI = 1 → hasar başlangıcı, FI > 1 → hasarlı.

2. Strength Ratio (SR) — Tsai-Wu kuadratik denklemi:

       (F_11·σ_1² + F_22·σ_2² + F_66·τ_12² + 2F_12·σ_1·σ_2)·SR²
     + (F_1·σ_1 + F_2·σ_2)·SR − 1 = 0

   SR = mevcut yükün hasara kadar büyütülebileceği katsayı.
   Bu, doğrusal-orantılı yükleme (proportional loading) varsayar.
   SR > 1 → güvenli; SR = 1 → tam dayanım; SR < 1 → yetersiz.

   (Tsai 1992 "Theory of Composites Design", Eq. 2.18)

3. Maximum Stress kriteri (her bileşen bağımsız):

       FI_max_stress = max(
           σ_1 / X_t  if σ_1 > 0 else |σ_1| / X_c,
           σ_2 / Y_t  if σ_2 > 0 else |σ_2| / Y_c,
           |τ_12| / S
       )

   Etkileşim yok — konservatif değil, ama basit.

4. Tsai-Hill kriteri (interaktif ama F_12 yok):

       FI_TH = (σ_1/X)² + (σ_2/Y)² + (τ_12/S)² − (σ_1·σ_2)/X²

   X = X_t veya X_c (σ_1 işaretine göre), benzer Y.
   (Jones 1999, Eq. 2.110)

5. Hashin kriteri (fiber/matris ayırımı):
   Sadece çekme yönünde basit form (basitleştirilmiş):
       Fiber: (σ_1/X_t)² + (τ_12/S)² = 1
       Matris: (σ_2/Y_t)² + (τ_12/S)² = 1
   (Hashin 1980, Eq. 2-3)

6. First-Ply-Failure (FPF):
   Verilen membran yük {N} altında her ply'nin FI'si hesaplanır;
   max(FI) > 1 olan ilk ply hasarlı kabul edilir. Strength Ratio
   yaklaşımında, SR = min(SR_k) tüm plyler arasında — bu, mevcut
   yükün FPF'ye kadar kaç kat artırılabileceğini söyler.

================================================================================
VARSAYIMLAR
================================================================================

VARSAYIM-1: Doğrusal-orantılı yükleme — tüm yük bileşenleri eşit oranda
            büyür. SR teorisi bu varsayıma dayanır.

VARSAYIM-2: Lamina lineer elastik kalır (FPF öncesi). Progresif hasar yok.

VARSAYIM-3: Her ply bağımsız değerlendirilir; ara yüzey hasar (delaminasyon)
            modellenmemiştir.

VARSAYIM-4: Tsai-Wu F_12 etkileşim katsayısı normalize değer f_12_norm =
            -0.5 (LaminaProperties varsayılanı) — gerçek değer biaksiyal
            test gerektirir.

VARSAYIM-5: Düzlem gerilme (plane stress); σ_3 ve τ_13/τ_23 ihmal edilir.

================================================================================
SINIRLAMALAR
================================================================================

SINIR-1: Sadece FPF — Last-Ply-Failure veya progresif hasar yok.
SINIR-2: Hasar mekanizması ayırımı (matris çatlak, fiber kopması,
         delaminasyon) sadece Hashin'de kısmi.
SINIR-3: Termal/higrotermal residüel gerilmeler dahil değil.
SINIR-4: Yorulma ömrü — knockdown faktörü olarak material_allowables'da
         (statik dayanım azaltılır), ayrı S-N modeli yok.

================================================================================
DOĞRULAMA STRATEJİSİ
================================================================================

DV-1: Saf uniaksiyel σ_1 = X_t, σ_2 = τ_12 = 0 → Tsai-Wu FI = 1.0
DV-2: Saf σ_2 = Y_t → FI ≈ 1.0
DV-3: σ_1 = X_t/2 → max-stress FI = 0.5
DV-4: Strength Ratio SR · uniaksiyel(X_t) yüklemesi → FI=1
DV-5: Tsai-Wu pozitif belirli (F_11·F_22 > F_12²) doğrulanmış
DV-6: Daniel & Ishai 2006 Worked Example 6.1 sayısal eşleşmesi:
        T300/5208, σ_1=400, σ_2=−100, τ_12=30 → FI_TW ≈ 0.55

================================================================================
REFERANSLAR
================================================================================

[1] Tsai, S.W. & Wu, E.M., "A General Theory of Strength for Anisotropic
    Materials", J. Comp. Mat., Vol. 5, 1971, pp. 58-80.
[2] Tsai, S.W. & Hahn, H.T., "Introduction to Composite Materials", 1980,
    Chapter 7.
[3] Jones, R.M., "Mechanics of Composite Materials", 1999, Chapter 2.10.
[4] Daniel, I.M. & Ishai, O., "Engineering Mechanics of Composite Materials",
    2006, Chapter 6.
[5] Hashin, Z., "Failure Criteria for Unidirectional Fiber Composites",
    J. Applied Mech., Vol. 47, 1980, pp. 329-334.
[6] Tsai, S.W., "Theory of Composites Design", Think Composites, 1992.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from .material_allowables import LaminaProperties
from .clt_engine import (
    Ply, LaminateStackup, LaminateABD, MembraneResponse,
    compute_ABD, solve_membrane,
)


# ── Yardımcı: tek nokta hasar indeksleri ─────────────────────────────────────

def tsai_wu_FI(lam: LaminaProperties,
                sigma_1_MPa: float, sigma_2_MPa: float,
                tau_12_MPa: float) -> float:
    """
    Tsai-Wu hasar indeksi (lokal koordinatlarda).

    FI = F_1·σ_1 + F_2·σ_2
       + F_11·σ_1² + F_22·σ_2² + F_66·τ_12²
       + 2·F_12·σ_1·σ_2
    """
    F1 = lam.F_1()
    F2 = lam.F_2()
    F11 = lam.F_11()
    F22 = lam.F_22()
    F66 = lam.F_66()
    F12 = lam.F_12()
    s1 = sigma_1_MPa
    s2 = sigma_2_MPa
    t12 = tau_12_MPa
    return (F1 * s1 + F2 * s2
            + F11 * s1 * s1 + F22 * s2 * s2 + F66 * t12 * t12
            + 2.0 * F12 * s1 * s2)


def tsai_wu_strength_ratio(lam: LaminaProperties,
                            sigma_1_MPa: float, sigma_2_MPa: float,
                            tau_12_MPa: float) -> float:
    """
    Tsai-Wu Strength Ratio (SR) — orantılı yükleme altında dayanım katsayısı.

    Kuadratik denklem:  a·SR² + b·SR − 1 = 0
        a = F_11·σ_1² + F_22·σ_2² + F_66·τ_12² + 2·F_12·σ_1·σ_2
        b = F_1·σ_1 + F_2·σ_2

    Pozitif kök:
        SR = (−b + √(b² + 4a)) / (2a)

    Eğer a → 0 (saf doğrusal Tsai-Wu, etkileşim yok ki nadir):
        SR = 1/b

    SR > 1 → güvenli; SR = mevcut yük × SR ile hasar başlar.
    """
    F1 = lam.F_1()
    F2 = lam.F_2()
    F11 = lam.F_11()
    F22 = lam.F_22()
    F66 = lam.F_66()
    F12 = lam.F_12()
    s1, s2, t12 = sigma_1_MPa, sigma_2_MPa, tau_12_MPa
    a = F11 * s1 * s1 + F22 * s2 * s2 + F66 * t12 * t12 + 2.0 * F12 * s1 * s2
    b = F1 * s1 + F2 * s2
    if abs(a) < 1e-30:
        if abs(b) < 1e-30:
            return float("inf")  # sıfır yük → sonsuz SR
        return 1.0 / b
    disc = b * b + 4.0 * a
    if disc < 0:
        return 0.0  # fiziksel olmayan; konservatif: sıfır SR
    sqrt_disc = math.sqrt(disc)
    sr_pos = (-b + sqrt_disc) / (2.0 * a)
    sr_neg = (-b - sqrt_disc) / (2.0 * a)
    # Pozitif kökü seç
    if sr_pos > 0:
        return sr_pos
    if sr_neg > 0:
        return sr_neg
    return 0.0


def max_stress_FI(lam: LaminaProperties,
                   sigma_1_MPa: float, sigma_2_MPa: float,
                   tau_12_MPa: float) -> float:
    """
    Maximum Stress hasar indeksi — etkileşimsiz, basit.

    FI = max(R_1, R_2, R_12)
        R_1 = σ_1/X_t veya |σ_1|/X_c (işarete göre)
        R_2 = σ_2/Y_t veya |σ_2|/Y_c
        R_12 = |τ_12|/S
    """
    if sigma_1_MPa >= 0:
        R1 = sigma_1_MPa / lam.X_t_MPa
    else:
        R1 = abs(sigma_1_MPa) / lam.X_c_MPa
    if sigma_2_MPa >= 0:
        R2 = sigma_2_MPa / lam.Y_t_MPa
    else:
        R2 = abs(sigma_2_MPa) / lam.Y_c_MPa
    R12 = abs(tau_12_MPa) / lam.S_MPa
    return max(R1, R2, R12)


def tsai_hill_FI(lam: LaminaProperties,
                  sigma_1_MPa: float, sigma_2_MPa: float,
                  tau_12_MPa: float) -> float:
    """
    Tsai-Hill kriteri — etkileşimli ama tek-yön (çekme/basma ayrı):

        FI = (σ_1/X)² + (σ_2/Y)² + (τ_12/S)² − (σ_1·σ_2)/X²
    """
    X = lam.X_t_MPa if sigma_1_MPa >= 0 else lam.X_c_MPa
    Y = lam.Y_t_MPa if sigma_2_MPa >= 0 else lam.Y_c_MPa
    return ((sigma_1_MPa / X) ** 2
            + (sigma_2_MPa / Y) ** 2
            + (tau_12_MPa / lam.S_MPa) ** 2
            - (sigma_1_MPa * sigma_2_MPa) / (X * X))


def hashin_fiber_FI(lam: LaminaProperties,
                     sigma_1_MPa: float, tau_12_MPa: float) -> float:
    """Hashin fiber-dominated hasar (sadece çekme yönü basit form)."""
    if sigma_1_MPa >= 0:
        return (sigma_1_MPa / lam.X_t_MPa) ** 2 + (tau_12_MPa / lam.S_MPa) ** 2
    else:
        return (abs(sigma_1_MPa) / lam.X_c_MPa) ** 2  # basitleştirilmiş


def hashin_matrix_FI(lam: LaminaProperties,
                      sigma_2_MPa: float, tau_12_MPa: float) -> float:
    """Hashin matrix-dominated hasar (sadece çekme yönü basit form)."""
    if sigma_2_MPa >= 0:
        return (sigma_2_MPa / lam.Y_t_MPa) ** 2 + (tau_12_MPa / lam.S_MPa) ** 2
    else:
        return ((sigma_2_MPa / (2 * lam.S_MPa)) ** 2
                + ((lam.Y_c_MPa / (2 * lam.S_MPa)) ** 2 - 1.0)
                * abs(sigma_2_MPa) / lam.Y_c_MPa
                + (tau_12_MPa / lam.S_MPa) ** 2)


# ── First-Ply-Failure analizi ────────────────────────────────────────────────

@dataclass
class FPFResult:
    """
    First-Ply-Failure analizi çıktısı.

    Tüm değerler "verilen N yüküne göre".
    """
    failure_indices: List[float]    # her ply için Tsai-Wu FI
    strength_ratios: List[float]    # her ply için Tsai-Wu SR
    max_FI: float                   # max(FI)
    min_SR: float                   # min(SR), kritik ply için
    critical_ply_index: int         # min_SR'nin geldiği ply (0-tabanlı)
    is_safe: bool                   # max_FI < 1 ⟺ min_SR > 1
    criterion: str = "tsai_wu"


def first_ply_failure(abd: LaminateABD,
                       N_x: float, N_y: float, N_xy: float = 0.0,
                       criterion: str = "tsai_wu") -> FPFResult:
    """
    Membran yük altında First-Ply-Failure analizi.

    Her ply için lokal σ_1, σ_2, τ_12 hesaplanır ve seçilen kriterle
    FI değerlendirilir. SR sadece Tsai-Wu için döner (diğerleri için 1/FI
    yaklaşımı kullanılır).

    criterion ∈ {"tsai_wu", "max_stress", "tsai_hill"}
    """
    resp = solve_membrane(abd, N_x, N_y, N_xy)
    return _evaluate_fpf(resp, criterion)


def _evaluate_fpf(resp: MembraneResponse, criterion: str) -> FPFResult:
    plies = resp.stackup.plies
    FIs: List[float] = []
    SRs: List[float] = []
    for k, ply in enumerate(plies):
        s1, s2, t12 = resp.ply_stress_local[k]
        if criterion == "tsai_wu":
            FI = tsai_wu_FI(ply.lamina, s1, s2, t12)
            SR = tsai_wu_strength_ratio(ply.lamina, s1, s2, t12)
        elif criterion == "max_stress":
            FI = max_stress_FI(ply.lamina, s1, s2, t12)
            SR = 1.0 / FI if FI > 1e-30 else float("inf")
        elif criterion == "tsai_hill":
            FI = tsai_hill_FI(ply.lamina, s1, s2, t12)
            SR = 1.0 / math.sqrt(FI) if FI > 1e-30 else float("inf")
        else:
            raise ValueError(
                f"criterion ∈ {{'tsai_wu','max_stress','tsai_hill'}}: "
                f"{criterion!r}"
            )
        FIs.append(FI)
        SRs.append(SR)

    max_FI = max(FIs) if FIs else 0.0
    min_SR_value = float("inf")
    crit_ply = 0
    for i, sr in enumerate(SRs):
        if sr < min_SR_value:
            min_SR_value = sr
            crit_ply = i

    return FPFResult(
        failure_indices=FIs,
        strength_ratios=SRs,
        max_FI=max_FI,
        min_SR=min_SR_value,
        critical_ply_index=crit_ply,
        is_safe=(max_FI < 1.0),
        criterion=criterion,
    )


# ── Pratik: yük katsayısı bulucu ────────────────────────────────────────────

def find_failure_load(abd: LaminateABD,
                      N_x_ref: float, N_y_ref: float, N_xy_ref: float = 0.0,
                      criterion: str = "tsai_wu") -> float:
    """
    Verilen yük vektörünü kaç kat büyütünce ilk ply hasarı olur?

    Geri dönüş: yük çarpanı k (k·N → FPF). Tsai-Wu için bu doğrudan SR'ye
    eşittir; max-stress ve Tsai-Hill için sayısal arama kullanılır.

    Orantılı yükleme varsayar.
    """
    res = first_ply_failure(abd, N_x_ref, N_y_ref, N_xy_ref, criterion)
    return res.min_SR
