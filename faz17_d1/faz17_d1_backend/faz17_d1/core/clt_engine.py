"""
core/clt_engine.py — Klasik Laminat Teorisi Motoru (Faz 23 ENG-3)
====================================================================

Bu modül Classical Laminate Theory (CLT) çekirdek matematiğini sağlar:
indirgenmiş katılık Q, döndürülmüş Q̄, membran A-matrisi, eğilme D-matrisi,
ve laminat-seviyesi yük→gerilme/gerinim çözümü. Filament sarma basınçlı
kabı bir membran yapı (B=0, D küçük) olarak modeller ancak tam B/D
matrisleri de hesaplanır.

================================================================================
MATEMATİKSEL MODEL
================================================================================

1. Lamina düzlemi gerilme indirgenmiş katılık (Q matrisi, lokal 1-2 ekseni):

       Q_11 = E_1 / (1 − ν_12·ν_21)
       Q_22 = E_2 / (1 − ν_12·ν_21)
       Q_12 = ν_12·E_2 / (1 − ν_12·ν_21) = ν_21·E_1 / (1 − ν_12·ν_21)
       Q_66 = G_12
       Q_16 = Q_26 = 0          (ortotropik)

   Burada ν_21 = ν_12·E_2/E_1 (simetri).

2. Açıya bağlı rotasyon (θ derece, x-y global laminat ekseni):

       m = cos θ,   n = sin θ
       Q̄_11 = Q_11·m⁴ + 2(Q_12 + 2Q_66)·m²n² + Q_22·n⁴
       Q̄_22 = Q_11·n⁴ + 2(Q_12 + 2Q_66)·m²n² + Q_22·m⁴
       Q̄_12 = (Q_11 + Q_22 − 4Q_66)·m²n² + Q_12·(m⁴ + n⁴)
       Q̄_66 = (Q_11 + Q_22 − 2Q_12 − 2Q_66)·m²n² + Q_66·(m⁴ + n⁴)
       Q̄_16 = (Q_11 − Q_12 − 2Q_66)·m³n + (Q_12 − Q_22 + 2Q_66)·m·n³
       Q̄_26 = (Q_11 − Q_12 − 2Q_66)·m·n³ + (Q_12 − Q_22 + 2Q_66)·m³·n
   (Jones 1999, Eq. 2.84)

3. Laminat membran katılık matrisi A:
       A_ij = Σ_k Q̄_ij^(k) · (z_{k+1} − z_k)         (N/mm)
   Toplam laminat kalınlığı h = z_N − z_0; z=0 orta düzlem.

4. Laminat eğilme-membran kuplaj B ve eğilme D:
       B_ij = (1/2) Σ_k Q̄_ij^(k) · (z_{k+1}² − z_k²)   (N)
       D_ij = (1/3) Σ_k Q̄_ij^(k) · (z_{k+1}³ − z_k³)   (N·mm)

   Simetrik laminatlar için B = 0 (filament sarma için pratik gereksinim).

5. Membran yük→gerinim ilişkisi (B=0, sadece membran yükleme):
       {N} = [A] · {ε⁰}
       {ε⁰} = [A]⁻¹ · {N}                              (1/mm)
   N_x, N_y, N_xy birim genişlik başına yük (N/mm).

6. Her bir lamina gerilmesi (lokal 1-2 koordinatlarına geri dönüş):
       {σ}_global = [Q̄] · {ε⁰}
       {σ}_local  = [T_σ] · {σ}_global

   Gerinim dönüşümü (engineering shear):
       [T_σ] = [[m², n², 2mn],
                [n², m², −2mn],
                [−mn, mn, m²−n²]]

================================================================================
VARSAYIMLAR
================================================================================

VARSAYIM-1: Düzlem gerilme (plane stress) — σ_3 ≈ 0. Filament sarma için
            çap/duvar oranı 20 üzeri ise makul.

VARSAYIM-2: Kirchhoff-Love kabuk teorisi: düzlem kesit düz kalır, normal
            kalır (ince laminat, kayma-deformasyonsuz).

VARSAYIM-3: Mükemmel yapışma — laminalar arasında kayma yok, hasar yok.

VARSAYIM-4: Lineer elastik — büyük deformasyon, geometrik nonlinearite yok.

VARSAYIM-5: Konstant lamina kalınlığı — ply ply içinde kalınlık sabit.
            (Genişleyen/azalan kalınlık için çoklu segment kullanın.)

VARSAYIM-6: Termal/higrotermal yük yok (gelecek genişleme).

================================================================================
SINIRLAMALAR
================================================================================

SINIR-1: Simetrik olmayan laminatlarda B ≠ 0; bu durumda saf membran
         (ε⁰ = A⁻¹·N) doğru değildir. Genel çözüm A⁻¹ + B·D etkileşimi
         gerektirir; bu modül A-only solver (filament sarma uygulamaları için
         yeterli — laminatlar genellikle simetrik balanslıdır).

SINIR-2: Eğrilik (curvature) hesabı dahil değil — düz plak varsayımı.
         Silindirik kabuk için pressure→N_x, N_y dönüşümü ENG-5 burst
         modülünde yapılır.

SINIR-3: İlk-ply-failure analizi ENG-4 (failure_criterion.py) modülünde.

================================================================================
DOĞRULAMA STRATEJİSİ
================================================================================

DV-1: Tek 0° lamina için Q̄ = Q (rotasyon kimliği).
DV-2: 90° rotasyon: Q̄_11(90°) = Q_22, Q̄_22(90°) = Q_11.
DV-3: ±45° simetrik laminat için Q̄_16 = 0 (balanced).
DV-4: Quasi-isotropic [0/+45/-45/90]_s laminat için A_11 ≈ A_22, A_16=A_26=0.
DV-5: Jones 1999 Worked Example 4.3 sayısal eşleşmesi.
DV-6: A matrisi pozitif belirli (tüm özdeğerler > 0).
DV-7: Simetrik laminat için B = 0 sayısal sıfır.

================================================================================
REFERANSLAR
================================================================================

[1] Jones, R.M., "Mechanics of Composite Materials", 2nd ed., Taylor &
    Francis, 1999, Chapter 2 (Q matrisi) ve Chapter 4 (A/B/D matrisi).
[2] Daniel, I.M. & Ishai, O., "Engineering Mechanics of Composite Materials",
    2006, Chapter 4 (laminat teorisi).
[3] Reddy, J.N., "Mechanics of Laminated Composite Plates and Shells",
    2nd ed., CRC Press, 2003.
[4] Vasiliev, V.V., "Composite Pressure Vessels", 2009, Chapter 4.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Tuple

import numpy as np

from .material_allowables import LaminaProperties


# ── Tek lamina katılık matrisi (lokal Q) ─────────────────────────────────────

def lamina_Q_matrix(lam: LaminaProperties) -> np.ndarray:
    """
    İndirgenmiş katılık Q (plane stress) — lokal 1-2 koordinatlarında.

    Geri dönüş: 3x3 ndarray (MPa cinsinden).
    """
    E1 = lam.E_1_GPa * 1000.0    # GPa → MPa
    E2 = lam.E_2_GPa * 1000.0
    G12 = lam.G_12_GPa * 1000.0
    nu12 = lam.nu_12
    nu21 = lam.nu_21
    denom = 1.0 - nu12 * nu21
    if denom <= 0:
        raise ValueError(
            f"1 - ν_12·ν_21 ≤ 0: malzeme termodinamik olarak geçersiz "
            f"(ν_12={nu12}, ν_21={nu21:.4f})"
        )
    Q = np.zeros((3, 3))
    Q[0, 0] = E1 / denom
    Q[1, 1] = E2 / denom
    Q[0, 1] = Q[1, 0] = nu12 * E2 / denom
    Q[2, 2] = G12
    return Q


def rotate_Q(Q: np.ndarray, theta_deg: float) -> np.ndarray:
    """
    Q matrisini θ (derece) açısıyla global x-y koordinatlarına döndür.

    Q̄ (3x3, MPa) — Jones 1999 Eq. 2.84 ile.
    """
    theta = math.radians(theta_deg)
    m = math.cos(theta)
    n = math.sin(theta)
    m2, n2 = m * m, n * n
    m4, n4 = m2 * m2, n2 * n2

    Q11, Q22 = Q[0, 0], Q[1, 1]
    Q12 = Q[0, 1]
    Q66 = Q[2, 2]

    Qb = np.zeros((3, 3))
    Qb[0, 0] = Q11 * m4 + 2.0 * (Q12 + 2.0 * Q66) * m2 * n2 + Q22 * n4
    Qb[1, 1] = Q11 * n4 + 2.0 * (Q12 + 2.0 * Q66) * m2 * n2 + Q22 * m4
    Qb[0, 1] = (Q11 + Q22 - 4.0 * Q66) * m2 * n2 + Q12 * (m4 + n4)
    Qb[1, 0] = Qb[0, 1]
    Qb[2, 2] = ((Q11 + Q22 - 2.0 * Q12 - 2.0 * Q66) * m2 * n2 +
                Q66 * (m4 + n4))
    Qb[0, 2] = ((Q11 - Q12 - 2.0 * Q66) * m * m2 * n +
                (Q12 - Q22 + 2.0 * Q66) * m * n * n2)
    Qb[2, 0] = Qb[0, 2]
    Qb[1, 2] = ((Q11 - Q12 - 2.0 * Q66) * m * n * n2 +
                (Q12 - Q22 + 2.0 * Q66) * m * m2 * n)
    Qb[2, 1] = Qb[1, 2]
    return Qb


# ── Ply tanımı ───────────────────────────────────────────────────────────────

@dataclass
class Ply:
    """
    Tek bir ply (katman) açıklayıcısı.

    angle_deg    : Global x-eksenine göre fiber yönü (derece)
    thickness_mm : Ply kalınlığı (mm)
    lamina       : Ply malzeme özellikleri (LaminaProperties)
    """
    angle_deg: float
    thickness_mm: float
    lamina: LaminaProperties

    def __post_init__(self) -> None:
        if self.thickness_mm <= 0:
            raise ValueError(f"ply kalınlığı > 0 olmalı: {self.thickness_mm}")


@dataclass
class LaminateStackup:
    """
    Bir sıra plyler bütünü — laminat yığını.

    plies: list of Ply, alt-üst sıra (ply[0] = en alt z_0, ply[-1] = en üst)
    """
    plies: List[Ply] = field(default_factory=list)

    def total_thickness_mm(self) -> float:
        return sum(p.thickness_mm for p in self.plies)

    def z_coords(self) -> np.ndarray:
        """
        Orta düzleme göre ply sınır z-koordinatları.

        Geri dönüş: numpy array, len = N_plies + 1.
        z_coords[0] = −h/2, z_coords[-1] = +h/2.
        """
        h = self.total_thickness_mm()
        z = -h / 2.0
        coords = [z]
        for p in self.plies:
            z += p.thickness_mm
            coords.append(z)
        return np.asarray(coords)

    def is_symmetric(self, tol_deg: float = 1e-6, tol_t: float = 1e-9) -> bool:
        """
        Orta düzleme göre simetri kontrolü.

        plies[i].angle ≡ plies[N-1-i].angle ve plies[i].thickness ≡ plies[N-1-i].
        """
        N = len(self.plies)
        for i in range(N // 2):
            a1 = self.plies[i]
            a2 = self.plies[N - 1 - i]
            if abs(a1.angle_deg - a2.angle_deg) > tol_deg:
                return False
            if abs(a1.thickness_mm - a2.thickness_mm) > tol_t:
                return False
            if a1.lamina is not a2.lamina and a1.lamina.name != a2.lamina.name:
                return False
        return True

    def is_balanced(self, tol_deg: float = 1e-6) -> bool:
        """
        Balans kontrolü: her +θ için karşılığı −θ olmalı (0 ve 90 hariç).

        Bu sadece açı listesini kontrol eder; ayrıntılı malzeme/kalınlık
        eşleşmesi gerekmez (balanslı + simetrik genelde birlikte kullanılır).
        """
        from collections import Counter
        # 0 ve 90 nötrdür; ±θ eşleşmesi gerekli
        cnt = Counter()
        for p in self.plies:
            a = p.angle_deg % 180.0  # [0, 180) içine indirge
            if a > 180.0 - tol_deg:
                a -= 180.0
            cnt[round(a, 6)] += 1
        for angle_key, n in cnt.items():
            if abs(angle_key) < tol_deg or abs(angle_key - 90.0) < tol_deg:
                continue
            # ayna eş +θ ↔ -θ → ayna anahtarı (180 - θ) ile eşleş
            mirror = round((180.0 - angle_key) % 180.0, 6)
            if cnt.get(mirror, 0) != n:
                return False
        return True


# ── ABD matrisleri ───────────────────────────────────────────────────────────

@dataclass
class LaminateABD:
    """
    Laminat A, B, D matrisleri.

    A: 3x3 (N/mm)   — membran katılık
    B: 3x3 (N)      — kuplaj
    D: 3x3 (N·mm)   — eğilme katılık
    """
    A: np.ndarray
    B: np.ndarray
    D: np.ndarray
    stackup: LaminateStackup

    def total_thickness_mm(self) -> float:
        return self.stackup.total_thickness_mm()

    def is_symmetric_numerically(self, rel_tol: float = 1e-6) -> bool:
        """B matrisi maksimum elemanı / max A elemanına oranı küçük mü?"""
        max_A = float(np.max(np.abs(self.A)))
        if max_A == 0:
            return True
        return float(np.max(np.abs(self.B))) / max_A < rel_tol


def compute_ABD(stackup: LaminateStackup) -> LaminateABD:
    """
    Bir laminat yığını için A, B, D matrislerini hesapla.

    Tüm matrisler 3x3, orta düzlem referansta (z=0 orta düzlemde).
    """
    if len(stackup.plies) == 0:
        raise ValueError("Boş laminat yığını")
    z = stackup.z_coords()
    A = np.zeros((3, 3))
    B = np.zeros((3, 3))
    D = np.zeros((3, 3))
    for k, ply in enumerate(stackup.plies):
        Q = lamina_Q_matrix(ply.lamina)
        Qb = rotate_Q(Q, ply.angle_deg)
        z_k = z[k]
        z_kp1 = z[k + 1]
        A += Qb * (z_kp1 - z_k)
        B += 0.5 * Qb * (z_kp1 ** 2 - z_k ** 2)
        D += (1.0 / 3.0) * Qb * (z_kp1 ** 3 - z_k ** 3)
    return LaminateABD(A=A, B=B, D=D, stackup=stackup)


# ── Membran yük→gerinim/gerilme çözümü ───────────────────────────────────────

@dataclass
class MembraneResponse:
    """
    Membran yük altında laminat yanıtı.

    epsilon0     : (3,) ortalama membran gerinim {ε_x, ε_y, γ_xy}
    ply_stress_global : (N, 3) her ply için global x-y koordinatlarda σ
    ply_stress_local  : (N, 3) her ply için lokal 1-2 koordinatlarda σ
    """
    epsilon0: np.ndarray
    ply_stress_global: np.ndarray
    ply_stress_local: np.ndarray
    stackup: LaminateStackup
    load_N_per_mm: np.ndarray   # (3,) uygulanan {N_x, N_y, N_xy}


def stress_transform_global_to_local(theta_deg: float) -> np.ndarray:
    """
    [T_σ]: global → lokal (engineering shear notasyonu).

    σ_local = [T_σ] · σ_global
    """
    theta = math.radians(theta_deg)
    m = math.cos(theta)
    n = math.sin(theta)
    return np.array([
        [m * m,    n * n,        2.0 * m * n],
        [n * n,    m * m,       -2.0 * m * n],
        [-m * n,   m * n,        m * m - n * n],
    ])


def solve_membrane(abd: LaminateABD,
                    N_x: float, N_y: float, N_xy: float = 0.0
                    ) -> MembraneResponse:
    """
    Membran yük altında laminat çözümü.

    Çözüm yöntemi: simetrik laminat varsayımı (B≈0), {ε⁰} = [A]⁻¹·{N}.
    Her ply için σ_global = Q̄·ε⁰, σ_local = T_σ·σ_global.
    """
    N = np.array([N_x, N_y, N_xy], dtype=float)
    if abs(np.max(np.abs(abd.A))) < 1e-15:
        raise ValueError("A matrisi sıfır — laminat tanımsız")
    eps0 = np.linalg.solve(abd.A, N)

    n_plies = len(abd.stackup.plies)
    sg = np.zeros((n_plies, 3))
    sl = np.zeros((n_plies, 3))
    for k, ply in enumerate(abd.stackup.plies):
        Q = lamina_Q_matrix(ply.lamina)
        Qb = rotate_Q(Q, ply.angle_deg)
        sigma_g = Qb @ eps0
        sg[k] = sigma_g
        Tsig = stress_transform_global_to_local(ply.angle_deg)
        sl[k] = Tsig @ sigma_g

    return MembraneResponse(
        epsilon0=eps0,
        ply_stress_global=sg,
        ply_stress_local=sl,
        stackup=abd.stackup,
        load_N_per_mm=N,
    )


# ── Laminat oluşturma yardımcıları ───────────────────────────────────────────

def make_symmetric_balanced_stackup(angles_deg: List[float],
                                     thickness_per_ply_mm: float,
                                     lamina: LaminaProperties) -> LaminateStackup:
    """
    [angles]_s yığını üret — simetrik balanslı.

    Örnek: angles=[0, 45, -45, 90] →
           [0, 45, -45, 90, 90, -45, 45, 0]  (8 ply)
    Her ply'nin kalınlığı `thickness_per_ply_mm`.
    """
    if thickness_per_ply_mm <= 0:
        raise ValueError("thickness_per_ply_mm > 0 olmalı")
    full = list(angles_deg) + list(reversed(angles_deg))
    plies = [Ply(a, thickness_per_ply_mm, lamina) for a in full]
    return LaminateStackup(plies=plies)


def make_helical_hoop_stackup(alpha_deg: float,
                                n_helical_pairs: int,
                                n_hoop: int,
                                thickness_per_ply_mm: float,
                                lamina: LaminaProperties,
                                hoop_first: bool = False) -> LaminateStackup:
    """
    Filament sarma tipik laminat: ±α helisel + 90° hoop.

    Sıra (varsayılan, hoop_first=False):
        [+α, -α] × n_pairs, sonra [90] × n_hoop, ayna ile simetri.
    """
    if n_helical_pairs < 0 or n_hoop < 0:
        raise ValueError("n_helical_pairs, n_hoop ≥ 0 olmalı")
    half: List[Ply] = []
    if hoop_first:
        for _ in range(n_hoop):
            half.append(Ply(90.0, thickness_per_ply_mm, lamina))
        for _ in range(n_helical_pairs):
            half.append(Ply(+alpha_deg, thickness_per_ply_mm, lamina))
            half.append(Ply(-alpha_deg, thickness_per_ply_mm, lamina))
    else:
        for _ in range(n_helical_pairs):
            half.append(Ply(+alpha_deg, thickness_per_ply_mm, lamina))
            half.append(Ply(-alpha_deg, thickness_per_ply_mm, lamina))
        for _ in range(n_hoop):
            half.append(Ply(90.0, thickness_per_ply_mm, lamina))
    # Ayna ile simetri (Ply nesneleri yeniden yarat — paylaşılan referans yok)
    mirror = [Ply(p.angle_deg, p.thickness_mm, p.lamina)
              for p in reversed(half)]
    return LaminateStackup(plies=half + mirror)


# ── Mühendislik sabitleri (laminat efektif) ──────────────────────────────────

def laminate_effective_constants(abd: LaminateABD) -> dict:
    """
    Laminat seviyesi efektif elastik sabitleri (sadece A'dan, ince plak).

        E_x_eff = (A_11·A_22 − A_12²) / (A_22 · h)
        E_y_eff = (A_11·A_22 − A_12²) / (A_11 · h)
        ν_xy   = A_12 / A_22
        G_xy   = A_66 / h
    (Jones 1999, Eq. 4.41)
    """
    h = abd.total_thickness_mm()
    A = abd.A
    detA22 = A[0, 0] * A[1, 1] - A[0, 1] ** 2
    if h <= 0 or A[1, 1] <= 0 or A[0, 0] <= 0:
        raise ValueError("Geçersiz A matrisi veya kalınlık")
    return {
        "E_x_MPa":  detA22 / (A[1, 1] * h),
        "E_y_MPa":  detA22 / (A[0, 0] * h),
        "nu_xy":    A[0, 1] / A[1, 1],
        "G_xy_MPa": A[2, 2] / h,
        "h_mm":     h,
    }
