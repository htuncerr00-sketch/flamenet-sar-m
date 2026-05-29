"""
core/layer_stacking.py — Çok Katmanlı Yığılma Modeli
======================================================
Faz: ENDÜSTRİYEL ÜRETİM DOĞRULUĞU — Görev 4

`layer_buildup.py` (radyal büyüme + katman yolları) üzerine, gerçek üretim
doğruluğu için yığılma (stacking) fiziğini ekler:

- Kalınlık birikimi (nominal + nesting düzeltmeli efektif)
- Efektif yarıçap büyümesi (nesting dahil)
- Katman yerleşimi (nesting): üst katman, alt katmanın oluklarına oturur
- Yarıçap artışına bağlı bant genişliği kayması (W/sin α ve adım evrimi)
- Kümülatif bindirme evrimi: sabit devre sayısında çevre büyüdükçe bindirme azalır

Analitik temel
--------------
Nominal katman kalınlığı : t_nom = tow_thickness · compaction
Nesting efektif kalınlık : t_eff = t_nom · (1 − nesting_factor)   (katman > 0)
Efektif yarıçap          : r_k = r_0 + Σ t_eff(i)
Çevre                    : C_k = 2π · r_k
Efektif adım             : step = C_k / N_devre   (sabit devre sayısında)
Bindirme evrimi          : overlap_k = 1 − step_k / W   (negatif → boşluk!)
Bant eksenel projeksiyon : W_axial = W / sin(α_k)

Nesting fiziği
--------------
İlk katman çıplak yüzeye tam kalınlıkla oturur. Sonraki katmanlar, alttaki
bandın tepe-oluk profiline kısmen gömülür; bu nedenle net radyal büyüme nominal
kalınlıktan biraz azdır. `nesting_factor` ∈ [0, ~0.2] bu gömülmeyi temsil eder
(0 = gömülme yok, ideal istifleme).

Mühendislik varsayımları
------------------------
- Üniform radyal büyüme (her z'de eşit) — layer_buildup ile tutarlı.
- Devre sayısı taban katmanından sabit tutulabilir (hold_circuits=True) veya
  her katmanda tam kaplama için yeniden hesaplanabilir (False).
- Nesting birinci katmandan sonra uygulanır.

Başarısızlık modu analizi
-------------------------
- Bindirme negatife düşerse → katmanlar arası boşluk → kuru fiber, sızdırma.
- Aşırı bindirme birikimi → reçine fazlası, kalınlık şişmesi, ağırlık artışı.
- Bant genişliği kayması → kaplama deseni kayar, kenar kalitesi düşer.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from .fiber_band import FiberBand
from .geometry_engine import MandrelProfile
from .layer_buildup import LayerBuildup


@dataclass
class StackLayer:
    """Yığındaki tek bir katmanın durumu."""
    index: int
    inner_radius_mm: float
    outer_radius_mm: float
    nominal_thickness_mm: float
    effective_thickness_mm: float       # nesting sonrası net radyal büyüme
    circumference_mm: float             # orta yarıçapta çevre
    alpha_deg: float
    bandwidth_axial_mm: float           # W / sin(α)
    actual_circuits: int
    circuits_for_full_coverage: int
    effective_overlap_pct: float        # bu katmanda gerçekleşen bindirme
    has_gap: bool                       # bindirme negatif → boşluk

    @property
    def mid_radius_mm(self) -> float:
        return 0.5 * (self.inner_radius_mm + self.outer_radius_mm)


@dataclass
class LayerStackReport:
    """Çok katmanlı yığılma analiz raporu."""
    layers: List[StackLayer]
    base_radius_mm: float
    final_radius_mm: float
    nominal_total_thickness_mm: float
    effective_total_thickness_mm: float
    nesting_savings_mm: float
    overlap_evolution_pct: List[float]
    bandwidth_evolution_mm: List[float]
    n_layers_with_gap: int
    is_consistent: bool
    warnings: List[str]

    def summary(self) -> str:
        status = "TUTARLI ✓" if self.is_consistent else "TUTARSIZ ✗"
        return (
            f"KatmanYığını: {status} | {len(self.layers)} katman | "
            f"r: {self.base_radius_mm:.2f}→{self.final_radius_mm:.2f}mm | "
            f"kalınlık: nominal={self.nominal_total_thickness_mm:.3f} "
            f"efektif={self.effective_total_thickness_mm:.3f}mm "
            f"(nesting −{self.nesting_savings_mm:.3f}) | "
            f"bindirme: {self.overlap_evolution_pct[0]:.1f}%→{self.overlap_evolution_pct[-1]:.1f}% | "
            f"boşluklu_katman={self.n_layers_with_gap}"
        )


def analyze_layer_stack(
    base_profile: MandrelProfile,
    band: FiberBand,
    alpha_deg: float,
    n_layers: int,
    nesting_factor: float = 0.0,
    hold_circuits: bool = True,
    hold_angle: bool = True,
) -> LayerStackReport:
    """
    Çok katmanlı yığılma fiziğini analiz et.

    Parametreler
    ----------
    base_profile : Çıplak mandrel profili.
    band         : Fiber bant fiziği.
    alpha_deg    : Taban katmanı nominal sarma açısı.
    n_layers     : Katman sayısı.
    nesting_factor : Katmanlar arası gömülme oranı [0, 0.5).
    hold_circuits  : True → devre sayısı tabandan sabit (gerçekçi: makine tekrarı);
                     bindirme yarıçapla evrilir. False → her katmanda tam kaplama.
    hold_angle     : True → açı sabit; False → Clairaut c sabit (açı yarıçapla düşer).

    Döner
    -----
    LayerStackReport — katman-katman yığılma evrimi + tutarlılık kararı.
    """
    if not (0.0 <= nesting_factor < 0.5):
        raise ValueError("nesting_factor ∈ [0, 0.5) olmalı")
    if n_layers < 1:
        raise ValueError("n_layers >= 1 olmalı")

    buildup = LayerBuildup(base_profile, band)
    t_nom = buildup.thickness_per_layer_mm
    W = band.tow_width_mm
    r0 = base_profile.avg_radius_mm
    base_c = r0 * math.sin(math.radians(alpha_deg))

    # Taban devre sayısı (tam kaplama @ taban yarıçapı)
    base_circ = 2.0 * math.pi * r0
    base_circuits = band.circuits_for_coverage(base_circ)

    layers: List[StackLayer] = []
    overlap_evo: List[float] = []
    bandwidth_evo: List[float] = []
    n_gap = 0

    r_inner = r0
    for k in range(n_layers):
        # Efektif kalınlık: ilk katman tam, sonrakiler nesting ile gömülür
        t_eff = t_nom if k == 0 else t_nom * (1.0 - nesting_factor)
        r_outer = r_inner + t_eff
        r_mid = 0.5 * (r_inner + r_outer)
        circ = 2.0 * math.pi * r_mid

        # Bu katmandaki açı
        if hold_angle:
            alpha_k = alpha_deg
        else:
            ratio = min(1.0, base_c / max(r_mid, 1e-9))
            alpha_k = math.degrees(math.asin(ratio))

        bandwidth_axial = band.bandwidth_at_angle(alpha_k)

        # Devre sayısı ve bindirme evrimi
        circuits_full = band.circuits_for_coverage(circ)
        if hold_circuits:
            actual = base_circuits
        else:
            actual = circuits_full

        # Gerçekleşen bindirme: step = C / N; overlap = 1 − step/W
        step = circ / max(actual, 1)
        eff_overlap_pct = (1.0 - step / max(W, 1e-9)) * 100.0
        has_gap = eff_overlap_pct < 0.0

        if has_gap:
            n_gap += 1

        layers.append(StackLayer(
            index=k,
            inner_radius_mm=r_inner,
            outer_radius_mm=r_outer,
            nominal_thickness_mm=t_nom,
            effective_thickness_mm=t_eff,
            circumference_mm=circ,
            alpha_deg=alpha_k,
            bandwidth_axial_mm=bandwidth_axial,
            actual_circuits=actual,
            circuits_for_full_coverage=circuits_full,
            effective_overlap_pct=eff_overlap_pct,
            has_gap=has_gap,
        ))
        overlap_evo.append(eff_overlap_pct)
        bandwidth_evo.append(bandwidth_axial)
        r_inner = r_outer

    nominal_total = t_nom * n_layers
    effective_total = sum(l.effective_thickness_mm for l in layers)
    nesting_savings = nominal_total - effective_total

    warnings: List[str] = []
    if n_gap > 0:
        warnings.append(
            f"{n_gap} katmanda bindirme negatife düştü → boşluk/kuru fiber "
            f"(sabit devre sayısında çevre büyümesi)")
    if hold_circuits and overlap_evo[-1] < overlap_evo[0] - 1e-9:
        warnings.append(
            f"Bindirme yarıçap büyümesiyle azaldı: {overlap_evo[0]:.1f}%→{overlap_evo[-1]:.1f}% "
            f"(üst katmanlarda kaplama zayıflar)")
    max_overlap = max(overlap_evo)
    if max_overlap > 50.0:
        warnings.append(
            f"Aşırı bindirme {max_overlap:.1f}% > 50% (reçine fazlası/kalınlık şişmesi)")

    is_consistent = (n_gap == 0)

    return LayerStackReport(
        layers=layers,
        base_radius_mm=r0,
        final_radius_mm=r_inner,
        nominal_total_thickness_mm=nominal_total,
        effective_total_thickness_mm=effective_total,
        nesting_savings_mm=nesting_savings,
        overlap_evolution_pct=overlap_evo,
        bandwidth_evolution_mm=bandwidth_evo,
        n_layers_with_gap=n_gap,
        is_consistent=is_consistent,
        warnings=warnings,
    )
