"""
core/safety_factor.py — Güvenlik Katsayısı ve Yönetmelik Tabloları (Faz 23 ENG-6)
==================================================================================

Bu modül endüstri standartları için gerekli güvenlik katsayılarını (SF =
P_burst / P_operating) içerir ve verilen tasarım için margin-of-safety
(MoS) hesaplaması yapar.

================================================================================
MATEMATİKSEL MODEL
================================================================================

1. Güvenlik katsayısı (Safety Factor, SF) tanımı:

       SF = P_burst / P_operating

   • SF ≥ SF_required ⟹ tasarım yönetmeliğe uygun
   • SF < SF_required ⟹ tasarım yetersiz

2. Margin of Safety (MoS):

       MoS = (P_burst / (SF_required · P_operating)) − 1.0
       MoS ≥ 0  ⟹ uygun
       MoS < 0  ⟹ yetersiz

3. Required burst basıncı:

       P_burst_required = SF_required · P_operating

================================================================================
YÖNETMELİK TABLOLARI (özet)
================================================================================

ASME BPVC Section X (2021), Fiber-Reinforced Plastic PV:
    • Burst test: SF = 2.25 × MAWP (Maximum Allowable Working Pressure)
    • Fatigue: 1000 döngü × 1.1 × MAWP, no leak
    • Ref: ASME BPVC Sec. X, RD-200

ISO 11119-2:2020, Composite Gas Cylinders (Type II/III):
    • Burst: SF = 2.25 (Type II/III), 3.0 (Type IV liner-bonded)
    • Fatigue: 10⁵ döngü × P_test
    • Ref: ISO 11119-2:2020, Cl. 6.3

ISO 11119-3:2020, Type IV (fully wrapped, plastic liner):
    • Burst: SF = 2.35 (CFRP), 3.0 (GFRP)

AIAA S-080-1998, Space Systems Metallic/Composite PV:
    • MEOP (Maximum Expected Operating Pressure) × 1.50 = Proof
    • MEOP × 2.0 = Burst (composite COPV)
    • Ref: AIAA S-080, Section 6

DOT-CFFC (US DOT, CGA C-19, Composite Cylinder):
    • Service pressure × 3.0 = burst (full bottle cycle test)
    • Cycle: 10000 ambient + 3 fast burst

EN 12245:2017, European Composite Cylinder:
    • Type 2/3: SF=2.25; Type 4: SF=2.4-3.0 fiber'a göre

UN ECE R134 (Hidrojen depolama, otomotiv):
    • Hidrojen: SF = 2.25 NWP (Nominal Working Pressure)
    • 5500 döngü 1.25·NWP

================================================================================
VARSAYIMLAR
================================================================================

VARSAYIM-1: Bu tablo özet değerlerdir. Tam yönetmelik uygulaması ek kontroller
            gerektirir (yorulma, sıcaklık, çevresel testler). Bu modül sadece
            burst SF kapısını kontrol eder.

VARSAYIM-2: SF değerleri en yaygın versiyon — özel servis koşulları
            (örn. derin deniz, uzay) için daha yüksek SF gerekebilir.

VARSAYIM-3: P_burst tahmininin doğruluğu ayrı (ENG-5 görevi). Bu modül
            sadece P_burst > SF·P_op kontrolü yapar.

================================================================================
SINIRLAMALAR
================================================================================

SINIR-1: Sadece burst SF — leak-before-burst (LBB), Fatigue Life, fragment
         containment ayrı analizler. Sertifikasyon için tam yönetmelik
         okunmalı.
SINIR-2: Dinamik/şok yükleme dahil değil.
SINIR-3: Servis sıcaklığı sınır kontrolleri yok (T_g — 50°C limit ayrı).

================================================================================
DOĞRULAMA STRATEJİSİ
================================================================================

DV-1: SF tablosu literatür değerleriyle uyumlu.
DV-2: MoS hesabı: P_burst = 2·SF·P_op → MoS = 1.0 (%100 margin).
DV-3: MoS = 0 sınır durumu.
DV-4: Yönetmelik adından enum dönüşü.

================================================================================
REFERANSLAR
================================================================================

[1] ASME BPVC Section X, "Fiber-Reinforced Plastic Pressure Vessels", 2021.
[2] ISO 11119-2:2020, "Gas cylinders — Refillable composite gas cylinders".
[3] ISO 11119-3:2020, "Gas cylinders — Fully wrapped composite — Type IV".
[4] AIAA S-080-1998, "Space Systems — Metallic/Composite Pressure Vessels".
[5] CGA C-19, "Standard for Type-3 and Type-4 Cylinders" (US DOT).
[6] EN 12245:2017, "Transportable gas cylinders — Fully wrapped composite".
[7] UN ECE R134, "Hydrogen-fueled vehicles".
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Dict, List, Optional


class SafetyCode(enum.Enum):
    """Desteklenen yönetmelik tabloları."""
    ASME_BPVC_X       = "asme_bpvc_x"            # 2.25
    ISO_11119_2       = "iso_11119_2"            # 2.25 (Type II/III)
    ISO_11119_3       = "iso_11119_3"            # 2.35-3.0 (Type IV)
    AIAA_S_080        = "aiaa_s_080"             # 2.0 (COPV uzay)
    DOT_CFFC          = "dot_cffc"               # 3.0
    EN_12245          = "en_12245"               # 2.25
    UN_ECE_R134       = "un_ece_r134"            # 2.25 (H2)
    CUSTOM            = "custom"                 # kullanıcı tanımlı


@dataclass(frozen=True)
class SafetyRequirement:
    """Tek bir yönetmelik için minimum gereksinimler."""
    code: SafetyCode
    name: str                               # insan-okuyabilir ad
    SF_burst: float                          # P_burst / P_op minimum
    SF_proof: float                          # proof pressure / P_op
    fatigue_cycles: int                      # min yorulma döngü sayısı
    notes: str = ""


# ── Yönetmelik tablosu ───────────────────────────────────────────────────────

_CODE_TABLE: Dict[SafetyCode, SafetyRequirement] = {
    SafetyCode.ASME_BPVC_X: SafetyRequirement(
        code=SafetyCode.ASME_BPVC_X,
        name="ASME BPVC Section X (Fiber-Reinforced Plastic PV)",
        SF_burst=2.25, SF_proof=1.30, fatigue_cycles=1000,
        notes="2021 edition, burst test 2.25·MAWP",
    ),
    SafetyCode.ISO_11119_2: SafetyRequirement(
        code=SafetyCode.ISO_11119_2,
        name="ISO 11119-2 (Composite Cylinder Type II/III)",
        SF_burst=2.25, SF_proof=1.50, fatigue_cycles=100_000,
        notes="2020 edition, Type II/III metallic-liner",
    ),
    SafetyCode.ISO_11119_3: SafetyRequirement(
        code=SafetyCode.ISO_11119_3,
        name="ISO 11119-3 (Composite Cylinder Type IV)",
        SF_burst=2.35, SF_proof=1.50, fatigue_cycles=12_000,
        notes="2020 edition, Type IV plastic-liner CFRP (GFRP: 3.0)",
    ),
    SafetyCode.AIAA_S_080: SafetyRequirement(
        code=SafetyCode.AIAA_S_080,
        name="AIAA S-080 (Space Systems Composite Overwrap PV)",
        SF_burst=2.0, SF_proof=1.50, fatigue_cycles=4 * 1000,  # 4·life cycles
        notes="1998 standard, COPV burst 2.0·MEOP",
    ),
    SafetyCode.DOT_CFFC: SafetyRequirement(
        code=SafetyCode.DOT_CFFC,
        name="US DOT CFFC (Composite Cylinder)",
        SF_burst=3.0, SF_proof=1.50, fatigue_cycles=10_000,
        notes="CGA C-19, full-bottle cycle + 3 fast burst",
    ),
    SafetyCode.EN_12245: SafetyRequirement(
        code=SafetyCode.EN_12245,
        name="EN 12245 (European Composite Cylinder)",
        SF_burst=2.25, SF_proof=1.50, fatigue_cycles=10_000,
        notes="2017 edition; Type 4 GFRP: 3.0",
    ),
    SafetyCode.UN_ECE_R134: SafetyRequirement(
        code=SafetyCode.UN_ECE_R134,
        name="UN ECE R134 (Hydrogen Vehicle Storage)",
        SF_burst=2.25, SF_proof=1.50, fatigue_cycles=5_500,
        notes="H2 hizmetinde NWP referansı, 70 MPa tipik",
    ),
}


# ── Genel API ────────────────────────────────────────────────────────────────

def get_safety_requirement(
    code: SafetyCode | str,
    SF_burst_custom: Optional[float] = None,
) -> SafetyRequirement:
    """
    Yönetmelik koduna göre gereksinim döndür.

    code = SafetyCode.CUSTOM ise SF_burst_custom verilmelidir.
    """
    if isinstance(code, str):
        try:
            code = SafetyCode(code)
        except ValueError:
            raise ValueError(f"Bilinmeyen güvenlik kodu: {code!r}")

    if code == SafetyCode.CUSTOM:
        if SF_burst_custom is None or SF_burst_custom <= 1.0:
            raise ValueError("CUSTOM için SF_burst_custom > 1 olmalı")
        return SafetyRequirement(
            code=code, name="Custom (kullanıcı)",
            SF_burst=SF_burst_custom, SF_proof=1.0,
            fatigue_cycles=0, notes="kullanıcı tanımlı",
        )
    if code not in _CODE_TABLE:
        raise KeyError(f"Yönetmelik tablosunda yok: {code}")
    return _CODE_TABLE[code]


def list_supported_codes() -> List[SafetyCode]:
    """Desteklenen tüm yönetmelik kodlarını döndür (CUSTOM hariç)."""
    return [c for c in _CODE_TABLE.keys()]


# ── MoS ve değerlendirme ─────────────────────────────────────────────────────

@dataclass
class SafetyAssessment:
    """Yönetmelik karşılaştırması sonucu."""
    code: SafetyCode
    P_operating_MPa: float
    P_burst_estimated_MPa: float
    SF_required: float
    SF_actual: float
    P_burst_required_MPa: float    # = SF_required · P_op
    margin_of_safety: float        # = SF_actual/SF_required − 1
    passes: bool                    # SF_actual ≥ SF_required
    notes: str = ""

    def summary(self) -> str:
        verdict = "GEÇER" if self.passes else "GEÇMEZ"
        return (
            f"Güvenlik [{self.code.value}]: "
            f"P_op={self.P_operating_MPa:.2f}MPa "
            f"P_burst={self.P_burst_estimated_MPa:.2f}MPa "
            f"SF={self.SF_actual:.2f} (req {self.SF_required:.2f}) "
            f"MoS={self.margin_of_safety*100:+.1f}% → {verdict}"
        )


def assess_safety(
    P_operating_MPa: float,
    P_burst_estimated_MPa: float,
    code: SafetyCode | str = SafetyCode.ASME_BPVC_X,
    SF_custom: Optional[float] = None,
) -> SafetyAssessment:
    """
    Verilen tahmin patlama basıncını yönetmelik gereksinimine karşı değerlendir.

    P_burst_estimated_MPa : ENG-5 burst_pressure modülünden gelir.
    P_operating_MPa       : Çalışma (servis) basıncı.
    code                  : Karşılaştırılacak yönetmelik (varsayılan ASME).
    SF_custom             : SafetyCode.CUSTOM ile birlikte kullanılır.
    """
    if P_operating_MPa <= 0:
        raise ValueError("P_operating_MPa > 0")
    if P_burst_estimated_MPa <= 0:
        raise ValueError("P_burst_estimated_MPa > 0")
    req = get_safety_requirement(code, SF_burst_custom=SF_custom)
    SF_actual = P_burst_estimated_MPa / P_operating_MPa
    SF_required = req.SF_burst
    P_burst_required = SF_required * P_operating_MPa
    MoS = SF_actual / SF_required - 1.0
    passes = SF_actual >= SF_required

    return SafetyAssessment(
        code=req.code,
        P_operating_MPa=P_operating_MPa,
        P_burst_estimated_MPa=P_burst_estimated_MPa,
        SF_required=SF_required,
        SF_actual=SF_actual,
        P_burst_required_MPa=P_burst_required,
        margin_of_safety=MoS,
        passes=passes,
        notes=req.name,
    )


def required_burst_pressure(
    P_operating_MPa: float,
    code: SafetyCode | str = SafetyCode.ASME_BPVC_X,
    SF_custom: Optional[float] = None,
) -> float:
    """Verilen yönetmelik için minimum gerekli patlama basıncı."""
    req = get_safety_requirement(code, SF_burst_custom=SF_custom)
    return req.SF_burst * P_operating_MPa
