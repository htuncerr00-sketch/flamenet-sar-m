"""
core/pressure_vessel_sizing.py — Basınçlı Kap Boyutlandırma Orkestratörü (Faz 23 ENG-7)
=========================================================================================

Bu modül Faz 23 Mühendislik Katmanının üst seviye orkestra fonksiyonudur.
Kullanıcı tasarım girdilerinden (basınç, çap, uzunluk, güvenlik kodu,
malzeme) tam bir basınçlı kap önerisi üretir:

    GİRDİ:
        - Çalışma basıncı (MPa)
        - İç çap (mm)
        - Uzunluk (mm)
        - Güvenlik yönetmeliği (ASME, ISO, AIAA, vb.)
        - Mühendislik malzeme (EngineeringMaterial)

    ÇIKTI:
        - Önerilen laminat şeması (helisel + hoop ply sayısı, kalınlık)
        - Helisel sarma açısı
        - Tahmini patlama basıncı (CLT + netting karşılaştırması)
        - Gerçek SF ve MoS (yönetmelik karşı)
        - Tahmini kütle
        - Tahmini fiber tüketimi (kg)
        - Üretilebilirlik skoru (0-100)

================================================================================
ALGORİTMA
================================================================================

1. Yönetmelikten gerekli SF'yi al → P_burst_required = SF · P_op

2. Başlangıç tahmini: Netting analizi ile t_helical, t_hoop
   (magic angle yakın bir açı seçilir, t_min hesaplanır)

3. Ply sayılarını yuvarla:
       n_helical_pairs ≥ t_helical_needed / (2·t_ply)   (±α çift, simetrik)
       n_hoop ≥ t_hoop_needed / (2·t_ply)

4. CLT-FPF ile gerçek burst hesapla; yeterli değilse ply sayısını artır
   (iteratif: max 10 iterasyon)

5. Sonuçları paketle:
   - mass = stackup_thickness × π · D · L · ρ_composite + iki dome (yaklaşık)
   - fiber_length = stackup'tan band geometrisi × kapsama
   - üretilebilirlik = açı/oran/kalınlık skorları kompozit

================================================================================
VARSAYIMLAR
================================================================================

VARSAYIM-1: Sadece silindirik orta bölge sızdırmaz dizayn — dome detaylı
            analiz Faz 22 Madde 2'de eklenecek.

VARSAYIM-2: Eşit ply kalınlığı — gerçek üretimde varyasyon mevcut.

VARSAYIM-3: Tüm CLT-FPF Tsai-Wu kriterine göre. Diğer kriterler isteğe
            bağlı (criterion parametre).

VARSAYIM-4: Üretilebilirlik skoru:
              - +α açısı yerel geodezik kısıtla uyumlu mu (basit sınır)
              - hoop/helical oranı 0.2-2.0 aralığında mı
              - toplam ply sayısı > 4 mü (mekanik istikrar)
              - D/t > 20 mi (ince cidar geçerli)

VARSAYIM-5: Optimum SF'yi sağlayan minimum ply kombinasyonu aranır
            (kütle/maliyet minimize edilir).

================================================================================
SINIRLAMALAR
================================================================================

SINIR-1: Tek malzeme — hybrid laminatlar (örn. carbon + glass) henüz yok.
SINIR-2: Boss/polar opening boyutlandırma ayrı (Faz 22 M2).
SINIR-3: Buckling (dış basınç) bu sürümde yok.
SINIR-4: Termal cycling sadece environmental knockdown.

================================================================================
DOĞRULAMA STRATEJİSİ
================================================================================

DV-1: Vasiliev 2009 Example PV (30 L H2 cylinder, P_op=20 MPa):
        D=200mm, L=500mm, SF=2.25, T700S → ~3-4 mm laminat.
DV-2: Boyutsal tutarlılık: hassasiyet artımı → daha kalın laminat.
DV-3: Daha yüksek SF kodu → daha kalın laminat.
DV-4: SF kontrolü yönetmelik geçer durumda.

================================================================================
REFERANSLAR
================================================================================

[1] Vasiliev, V.V., "Composite Pressure Vessels", 2009, Chapter 5.
[2] Peters, S.T., "Composite Filament Windings", 2011, Chapter 7-8.
[3] ASME BPVC Section X, 2021.
[4] ISO 11119, 2020 series.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .material_allowables import EngineeringMaterial
from .netting_analysis import (
    MAGIC_ANGLE_DEG, combined_hoop_helical, recommend_winding_angle,
)
from .burst_pressure import (
    estimate_burst_cylinder, BurstResult,
)
from .safety_factor import (
    SafetyCode, assess_safety, SafetyAssessment, required_burst_pressure,
    get_safety_requirement,
)
from .clt_engine import make_helical_hoop_stackup, LaminateStackup


# ── Girdi/çıktı veri yapıları ────────────────────────────────────────────────

@dataclass
class VesselDesignInput:
    """Basınçlı kap tasarım girdileri."""
    P_operating_MPa: float
    diameter_mm: float
    length_mm: float
    material: EngineeringMaterial
    safety_code: SafetyCode = SafetyCode.ASME_BPVC_X
    safety_factor_custom: Optional[float] = None  # CUSTOM ile kullanılır
    target_alpha_deg: Optional[float] = None       # None ⟹ otomatik öner
    basis: str = "B"                                # "A" | "B" | "MEAN"
    max_iterations: int = 20                        # ply tarama max iter
    ply_thickness_override_mm: Optional[float] = None
    burst_method: str = "clt_fpf"                   # "clt_fpf" | "netting"


@dataclass
class LayerSchedule:
    """Önerilen laminat şeması."""
    alpha_deg: float                # ±α helisel sarma açısı
    n_helical_pairs: int            # yarı laminat ±α çift sayısı (simetrik ⟹ ×2 toplam)
    n_hoop: int                     # yarı laminat 90° hoop sayısı (×2 toplam)
    ply_thickness_mm: float         # tek ply kalınlığı
    total_thickness_mm: float
    n_total_plies: int
    t_helical_total_mm: float       # tam laminat helisel toplam
    t_hoop_total_mm: float

    @property
    def hoop_helical_ratio(self) -> float:
        if self.t_helical_total_mm < 1e-9:
            return float("inf")
        return self.t_hoop_total_mm / self.t_helical_total_mm


@dataclass
class VesselDesignReport:
    """
    Tam basınçlı kap tasarım raporu.

    Bu rapor son kullanıcının ihtiyaç duyduğu tüm tasarım sonuçlarını içerir.
    """
    input: VesselDesignInput
    layer_schedule: LayerSchedule
    burst_clt: BurstResult                # CLT-FPF tahmin
    burst_netting: BurstResult            # netting tahmin (karşılaştırma)
    safety_assessment: SafetyAssessment
    estimated_mass_kg: float              # cidar (cyl) + 2× dome yaklaşımı
    estimated_fiber_length_mm: float
    estimated_fiber_mass_kg: float
    manufacturability_score: float        # 0-100
    manufacturability_notes: List[str]
    n_iterations_used: int                # tarama döngüsü sayısı
    warnings: List[str] = field(default_factory=list)

    @property
    def passes_code(self) -> bool:
        return self.safety_assessment.passes

    def summary(self) -> str:
        verdict = "★ UYGUN ★" if self.passes_code else "✗ YETERSİZ ✗"
        return (
            f"Basınçlı Kap Tasarım Raporu — {verdict}\n"
            f"  Girdi:     P_op={self.input.P_operating_MPa:.2f}MPa "
            f"D={self.input.diameter_mm:.0f}mm L={self.input.length_mm:.0f}mm "
            f"Kod={self.input.safety_code.value}\n"
            f"  Laminat:   α=±{self.layer_schedule.alpha_deg:.2f}° "
            f"n_hel={self.layer_schedule.n_helical_pairs} "
            f"n_hoop={self.layer_schedule.n_hoop} "
            f"t={self.layer_schedule.total_thickness_mm:.2f}mm "
            f"({self.layer_schedule.n_total_plies} ply)\n"
            f"  Burst:     CLT={self.burst_clt.P_burst_design_MPa:.2f}MPa "
            f"Netting={self.burst_netting.P_burst_design_MPa:.2f}MPa\n"
            f"  Güvenlik:  SF={self.safety_assessment.SF_actual:.2f} "
            f"(req {self.safety_assessment.SF_required:.2f}) "
            f"MoS={self.safety_assessment.margin_of_safety*100:+.1f}%\n"
            f"  Kütle:     {self.estimated_mass_kg:.2f} kg "
            f"(fiber {self.estimated_fiber_mass_kg:.2f} kg, "
            f"{self.estimated_fiber_length_mm/1000:.0f} m)\n"
            f"  Üretim:    skor {self.manufacturability_score:.0f}/100"
        )


# ── Ana orkestratör ──────────────────────────────────────────────────────────

def size_pressure_vessel(input_data: VesselDesignInput) -> VesselDesignReport:
    """
    Verilen girdi için tam basınçlı kap tasarım raporu üret.

    Algoritma:
      1. Hedef burst basıncı: P_target = SF_req · P_op
      2. Sarma açısı seçimi: kullanıcı verdiyse onu, aksi halde önerilen
      3. Başlangıç ply sayıları: netting analizi
      4. Ply sayısı tarama: CLT-FPF burst ≥ P_target sağlanana kadar
         ply ekle (önce helisel, sonra hoop)
      5. Burst tahmini (CLT + netting)
      6. Kütle, fiber, üretilebilirlik hesabı
    """
    warnings: List[str] = []

    # 1. Hedef burst
    req = get_safety_requirement(
        input_data.safety_code, input_data.safety_factor_custom,
    )
    P_target = req.SF_burst * input_data.P_operating_MPa

    # 2. Sarma açısı seçimi
    if input_data.target_alpha_deg is not None:
        alpha = input_data.target_alpha_deg
        alpha_note = "kullanıcı belirledi"
    else:
        alpha, alpha_note = recommend_winding_angle(
            diameter_mm=input_data.diameter_mm,
            length_mm=input_data.length_mm,
            pressure_MPa=input_data.P_operating_MPa,
            material=input_data.material,
            allow_hoop_layer=True,
        )

    # 3. Ply kalınlığı
    if input_data.ply_thickness_override_mm is not None:
        t_ply = input_data.ply_thickness_override_mm
    else:
        t_ply = input_data.material.lamina.nominal_ply_thickness_mm
    if t_ply <= 0:
        raise ValueError(f"ply_thickness_mm > 0: {t_ply}")

    # 4. Başlangıç ply sayıları (netting tahmin)
    sigma_f_design = input_data.material.design_X_t(basis=input_data.basis)
    if sigma_f_design <= 0:
        raise ValueError("design_X_t > 0 olmalı")

    # Netting tahmini (hedef burst için)
    net = combined_hoop_helical(
        pressure_MPa=P_target,
        diameter_mm=input_data.diameter_mm,
        sigma_fiber_MPa=sigma_f_design,
        alpha_deg=alpha,
    )

    # Tek tam laminat = 2·n_pair·2·t_ply helisel + 2·n_hoop·2·t_ply hoop
    # ⟹ n_helical_pairs = ceil(t_helical_total / (4·t_ply))
    # ⟹ n_hoop          = ceil(t_hoop_total    / (2·t_ply))
    n_helical_pairs = max(1, math.ceil(net.t_helical_mm / (4.0 * t_ply)))
    n_hoop = max(0, math.ceil(net.t_hoop_mm / (2.0 * t_ply)))

    # 5. Ply sayısı tarama: CLT-FPF burst ≥ P_target
    schedule, burst_clt, n_iter = _iterate_ply_counts(
        diameter_mm=input_data.diameter_mm,
        material=input_data.material,
        alpha_deg=alpha,
        n_helical_pairs_init=n_helical_pairs,
        n_hoop_init=n_hoop,
        t_ply=t_ply,
        P_target=P_target,
        basis=input_data.basis,
        max_iter=input_data.max_iterations,
        burst_method=input_data.burst_method,
        warnings_list=warnings,
    )

    # Netting tahmin (karşılaştırma için)
    burst_net = estimate_burst_cylinder(
        diameter_mm=input_data.diameter_mm,
        material=input_data.material,
        alpha_deg=alpha,
        n_helical_pairs=schedule.n_helical_pairs,
        n_hoop=schedule.n_hoop,
        thickness_per_ply_mm=t_ply,
        method="netting",
        basis=input_data.basis,
    )

    # 6. SF değerlendirmesi
    assessment = assess_safety(
        P_operating_MPa=input_data.P_operating_MPa,
        P_burst_estimated_MPa=burst_clt.P_burst_design_MPa,
        code=input_data.safety_code,
        SF_custom=input_data.safety_factor_custom,
    )

    # 7. Kütle ve fiber tüketimi
    mass_kg, fiber_len_mm, fiber_mass_kg = _estimate_mass_and_fiber(
        input_data=input_data, schedule=schedule,
    )

    # 8. Üretilebilirlik skoru
    mfg_score, mfg_notes = _manufacturability_score(
        input_data=input_data, schedule=schedule,
    )

    if not assessment.passes:
        warnings.append(
            f"max_iterations={input_data.max_iterations} sonunda yönetmelik "
            f"gerekleri karşılanamadı. SF={assessment.SF_actual:.2f} < "
            f"{assessment.SF_required:.2f}. Daha yüksek dayanım malzemesi, "
            f"daha kalın ply veya daha düşük çalışma basıncı düşünün."
        )

    return VesselDesignReport(
        input=input_data,
        layer_schedule=schedule,
        burst_clt=burst_clt,
        burst_netting=burst_net,
        safety_assessment=assessment,
        estimated_mass_kg=mass_kg,
        estimated_fiber_length_mm=fiber_len_mm,
        estimated_fiber_mass_kg=fiber_mass_kg,
        manufacturability_score=mfg_score,
        manufacturability_notes=mfg_notes,
        n_iterations_used=n_iter,
        warnings=warnings,
    )


# ── Yardımcı: ply sayısı tarama ─────────────────────────────────────────────

def _iterate_ply_counts(
    diameter_mm: float,
    material: EngineeringMaterial,
    alpha_deg: float,
    n_helical_pairs_init: int,
    n_hoop_init: int,
    t_ply: float,
    P_target: float,
    basis: str,
    max_iter: int,
    burst_method: str,
    warnings_list: List[str],
) -> Tuple[LayerSchedule, BurstResult, int]:
    """
    P_target'a ulaşana kadar ply ekleyerek tarama.

    Strateji: her iterasyonda, eksenel kritik ise n_helical_pairs+1,
    hoop kritik ise n_hoop+1. CLT criterion sonucundan limiting mode'u
    çıkarmak için ply gerilmesine bakmıyoruz; basit strateji: her iki
    katmanı sırayla artır (önce helisel, sonra hoop). Yakınsama
    gerçek olduğunda dur.
    """
    n_hel = max(1, n_helical_pairs_init)
    n_hoop = max(0, n_hoop_init)

    last_burst: Optional[BurstResult] = None
    last_schedule: Optional[LayerSchedule] = None

    for it in range(1, max_iter + 1):
        last_schedule = _build_schedule(alpha_deg, n_hel, n_hoop, t_ply)
        last_burst = estimate_burst_cylinder(
            diameter_mm=diameter_mm,
            material=material,
            alpha_deg=alpha_deg,
            n_helical_pairs=n_hel,
            n_hoop=n_hoop,
            thickness_per_ply_mm=t_ply,
            method=burst_method,
            basis=basis,
        )
        # design burst gerekli mi?  P_target sadece nominal değil; design knockdown'u dahil:
        if last_burst.P_burst_design_MPa >= P_target:
            return last_schedule, last_burst, it

        # Yetersiz: hangisini artıralım?
        # CLT-FPF burst sonucunda hangi katman daha kritik bilmiyoruz;
        # basit heuristic: hoop sıfırsa ya da oran < 0.5 ise hoop ekle;
        # aksi halde helisel ekle.
        if n_hoop == 0:
            n_hoop = 1
        elif last_schedule.hoop_helical_ratio < 0.5:
            n_hoop += 1
        else:
            n_hel += 1

    # Max iter sonu yetersiz
    warnings_list.append(
        f"Tarama {max_iter} iterasyon sonunda hedef burst basıncına "
        f"({P_target:.2f} MPa) ulaşamadı; son tahmin: "
        f"{last_burst.P_burst_design_MPa:.2f} MPa."
    )
    return last_schedule, last_burst, max_iter


def _build_schedule(alpha_deg: float, n_hel: int, n_hoop: int,
                     t_ply: float) -> LayerSchedule:
    n_total_plies = 2 * (2 * n_hel + n_hoop)  # simetrik (×2): 4·n_hel + 2·n_hoop
    t_helical_total = 4.0 * n_hel * t_ply
    t_hoop_total = 2.0 * n_hoop * t_ply
    return LayerSchedule(
        alpha_deg=alpha_deg,
        n_helical_pairs=n_hel,
        n_hoop=n_hoop,
        ply_thickness_mm=t_ply,
        total_thickness_mm=t_helical_total + t_hoop_total,
        n_total_plies=n_total_plies,
        t_helical_total_mm=t_helical_total,
        t_hoop_total_mm=t_hoop_total,
    )


# ── Yardımcı: kütle ve fiber tüketimi ────────────────────────────────────────

def _estimate_mass_and_fiber(
    input_data: VesselDesignInput,
    schedule: LayerSchedule,
) -> Tuple[float, float, float]:
    """
    Yaklaşık kütle ve fiber uzunluğu.

    Silindirik cidar hacmi:
        V_wall = π·D·L·t
    Kütle:
        m_total = V_wall · ρ_composite
    Fiber kütlesi: V_fiber = V_wall · Vf; m_fiber = V_fiber · ρ_fiber

    Fiber uzunluğu (kullanılan): m_fiber / mass_per_mm
    İki dome için yaklaşık katkı: silindirik cidarın %20-30'u eklenir
    (yarı-eliptik yaklaşımı, kabaca tahmin).
    """
    D = input_data.diameter_mm
    L = input_data.length_mm
    t = schedule.total_thickness_mm
    rho_c = input_data.material.base.composite_density_g_cm3  # g/cm³
    rho_f = input_data.material.base.fiber.density_g_cm3
    Vf = input_data.material.base.fiber_volume_fraction

    # Hacimler (mm³ → cm³: ÷1000)
    V_wall_mm3 = math.pi * D * L * t
    V_wall_cm3 = V_wall_mm3 / 1000.0
    # Dome ek katkısı yaklaşımı: ~25% (yarı-eliptik kabuk, tipik PV)
    dome_factor = 1.25
    V_total_cm3 = V_wall_cm3 * dome_factor

    m_total_g = V_total_cm3 * rho_c
    m_total_kg = m_total_g / 1000.0

    # Fiber kütlesi
    V_fiber_cm3 = V_total_cm3 * Vf
    m_fiber_kg = V_fiber_cm3 * rho_f / 1000.0

    # Fiber uzunluğu (tow tex_g_km tabanlı)
    # m_kg = L_mm · tex / 1e9 ⟹ L_mm = m_kg · 1e9 / tex
    tex = input_data.material.base.fiber.tex_g_km
    if tex > 0:
        L_fiber_mm = m_fiber_kg * 1e9 / tex
    else:
        L_fiber_mm = 0.0

    return m_total_kg, L_fiber_mm, m_fiber_kg


# ── Yardımcı: üretilebilirlik skoru ─────────────────────────────────────────

def _manufacturability_score(
    input_data: VesselDesignInput,
    schedule: LayerSchedule,
) -> Tuple[float, List[str]]:
    """
    0-100 üretilebilirlik skoru.

    Kriterler:
      A) Sarma açısı geodezik sınırla uyumlu (boss açıklığı):
         Polar geometri yok varsayımıyla, α ∈ [10°, 85°] kabul edilir.
      B) Hoop/Helical kalınlık oranı: ideal 0.3-1.5 aralığı.
      C) Toplam ply sayısı: ≥ 4 (mekanik istikrar)
      D) D/t > 20 (ince cidar geçerli)
      E) Toplam kalınlık makul (D'nin %10'undan az)
    """
    notes: List[str] = []
    score = 100.0

    # A) Açı
    if schedule.alpha_deg < 10.0 or schedule.alpha_deg > 85.0:
        score -= 15.0
        notes.append(
            f"Açı sınır dışı (α={schedule.alpha_deg:.1f}°, ideal 10°-85°)"
        )
    elif schedule.alpha_deg < 20.0:
        score -= 5.0
        notes.append(f"Düşük açı (α={schedule.alpha_deg:.1f}°) — boss kritik")

    # B) Hoop/Helical oranı
    ratio = schedule.hoop_helical_ratio
    if ratio == float("inf"):
        score -= 10.0
        notes.append("Helisel ply yok — eksenel yük taşınamaz")
    elif ratio < 0.2:
        score -= 5.0
        notes.append(f"Düşük hoop oranı ({ratio:.2f}); eksenel-dominant tasarım")
    elif ratio > 3.0:
        score -= 10.0
        notes.append(f"Aşırı hoop oranı ({ratio:.2f}); helisel yetersiz")

    # C) Ply sayısı
    if schedule.n_total_plies < 4:
        score -= 20.0
        notes.append(
            f"Toplam ply çok az ({schedule.n_total_plies}); mekanik istikrar düşük"
        )
    elif schedule.n_total_plies > 100:
        score -= 5.0
        notes.append(f"Yüksek ply sayısı ({schedule.n_total_plies}); süre/maliyet artar")

    # D) D/t oranı
    D = input_data.diameter_mm
    t = schedule.total_thickness_mm
    if t > 0:
        D_over_t = D / t
        if D_over_t < 20.0:
            score -= 10.0
            notes.append(
                f"D/t={D_over_t:.1f} < 20 — kalın cidar, membran teori "
                f"sınırda; FEM önerilir"
            )
        if t / D > 0.10:
            score -= 5.0
            notes.append(
                f"Kalınlık D'nin %{100*t/D:.1f}'ı — fazla; daha güçlü fiber düşünün"
            )

    # E) Magic angle yakını ödül (üretim kolaylığı)
    delta_magic = abs(schedule.alpha_deg - MAGIC_ANGLE_DEG)
    if delta_magic < 2.0:
        notes.append(f"Magic angle yakını (Δ={delta_magic:.1f}°) — optimal")
    elif delta_magic > 30.0:
        score -= 5.0
        notes.append(f"Magic angle'dan uzak (Δ={delta_magic:.1f}°)")

    score = max(0.0, min(100.0, score))
    if not notes:
        notes.append("Tüm üretilebilirlik kriterleri uygun")
    return score, notes
