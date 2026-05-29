"""
core/layer_buildup.py — Katman Birikim Geometrisi
===================================================
Her sarma katmanından sonra mandrel yarıçapının nasıl büyüdüğünü modeller
ve büyüyen yarıçap için sarma yolunu yeniden hesaplar.

Fizik
-----
Her tam kaplama katmanı, yüzeye sıkıştırılmış bant kalınlığı kadar radyal
malzeme ekler. Sarma açısı sabit tutulduğunda, büyüyen yarıçap Clairaut
sabitini (c = r·sin α) değiştirir; bu nedenle yol katman başına yeniden
hesaplanmalıdır.

Katman radyal artışı
--------------------
Δr_layer = t_compacted · coverage_multiplier

Helisel ±α dengeli katman tek geçişte tam kaplama verir → çarpan ≈ 1.
Hoop katmanı için de ≈ 1 (tek sıra). Çoklu kaplama varsa çarpan artar.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field, replace
from typing import List, Optional

import numpy as np

from .fiber_band import FiberBand
from .geometry_engine import MandrelProfile
from .path_generator import WindingPath, WindingPathParams, generate_path


@dataclass
class LayerBuildup:
    """
    Katman birikim hesaplayıcısı.

    base_profile : Başlangıç (çıplak) mandrel profili.
    band         : Fiber bant fiziği (sıkıştırılmış kalınlık kaynağı).
    coverage_multiplier : Katman başına kaplama çarpanı (varsayılan 1.0).
    """
    base_profile: MandrelProfile
    band: FiberBand
    coverage_multiplier: float = 1.0

    @property
    def thickness_per_layer_mm(self) -> float:
        """Tek katmanın radyal kalınlık artışı (mm)."""
        return self.band.compacted_thickness_mm * self.coverage_multiplier

    def radius_growth_mm(self, n_layers: int) -> float:
        """n katman sonrası toplam radyal büyüme (mm)."""
        return self.thickness_per_layer_mm * max(0, n_layers)

    def profile_after_layers(self, n_layers: int) -> MandrelProfile:
        """
        n katman birikimi sonrası mandrel profili.

        Tüm yüzeye eşit radyal büyüme uygulanır (uniform birikim varsayımı).
        """
        grow = self.radius_growth_mm(n_layers)
        new_r = self.base_profile.r_mm + grow
        return MandrelProfile(self.base_profile.z_mm.copy(), new_r)

    def build_sequence(self, n_layers: int) -> List[MandrelProfile]:
        """
        Katman katman profil dizisi.

        Döner: [katman 0 öncesi (çıplak), katman 1 öncesi, ..., katman n öncesi]
        Uzunluk = n_layers (her katmanın YATIRILMADAN ÖNCEKİ yüzeyi).
        """
        return [self.profile_after_layers(i) for i in range(n_layers)]

    def diameter_at_layer(self, layer_index: int) -> float:
        """layer_index katmanı yatırılmadan önceki ortalama çap (mm)."""
        prof = self.profile_after_layers(layer_index)
        return 2.0 * prof.avg_radius_mm


@dataclass
class LayeredPathResult:
    """Katman katman yeniden hesaplanmış yollar."""
    paths: List[WindingPath]          # Her katman için ayrı yol
    profiles: List[MandrelProfile]    # Her katmanın yatırılma yüzeyi
    clairaut_per_layer: List[float]   # Her katmanın Clairaut sabiti
    total_fiber_length_mm: float
    final_radius_mm: float
    base_radius_mm: float
    total_radial_growth_mm: float

    @property
    def n_layers(self) -> int:
        return len(self.paths)

    def summary(self) -> str:
        return (
            f"LayeredPath: {self.n_layers} katman | "
            f"r: {self.base_radius_mm:.2f}→{self.final_radius_mm:.2f}mm "
            f"(+{self.total_radial_growth_mm:.2f}mm) | "
            f"fiber={self.total_fiber_length_mm/1000:.1f}m | "
            f"c: {min(self.clairaut_per_layer):.2f}→{max(self.clairaut_per_layer):.2f}mm"
        )


def generate_layered_paths(
    base_profile: MandrelProfile,
    band: FiberBand,
    base_params: WindingPathParams,
    n_layers: int,
    hold_angle: bool = True,
    coverage_multiplier: float = 1.0,
) -> LayeredPathResult:
    """
    Büyüyen yarıçap için katman katman sarma yolu üret.

    Her katman için:
    1. O ana kadar birikmiş yarıçap ile profil oluştur.
    2. Sarma açısını koruyarak (hold_angle) tek katmanlık yol üret.
       - hold_angle=True: her katmanda sarma açısı sabit; Clairaut c büyür.
       - hold_angle=False: ilk katmanın Clairaut c'si korunur; açı değişir.
    3. Fiber uzunluğunu ve iş mili açısını biriktir.

    Parametreler
    ----------
    base_profile : Çıplak mandrel.
    band         : Bant fiziği.
    base_params  : Temel yol parametreleri (profile alanı yok sayılır).
    n_layers     : Yatırılacak katman sayısı.
    hold_angle   : Sarma açısı sabit mi tutulsun (True) yoksa Clairaut c mi (False).
    """
    buildup = LayerBuildup(base_profile, band, coverage_multiplier)

    paths: List[WindingPath] = []
    profiles: List[MandrelProfile] = []
    clairauts: List[float] = []
    total_fiber = 0.0

    base_c = base_profile.avg_radius_mm * math.sin(math.radians(base_params.alpha_deg))

    for layer in range(n_layers):
        prof_layer = buildup.profile_after_layers(layer)
        profiles.append(prof_layer)

        if hold_angle:
            alpha_layer = base_params.alpha_deg
        else:
            # Clairaut c sabit: c = r·sin(α) → α = asin(c / r_avg)
            r_avg = prof_layer.avg_radius_mm
            ratio = min(1.0, base_c / max(r_avg, 1e-9))
            alpha_layer = math.degrees(math.asin(ratio))

        layer_params = replace(
            base_params,
            profile=prof_layer,
            alpha_deg=alpha_layer,
            n_layers=1,
        )
        path_layer = generate_path(layer_params)
        paths.append(path_layer)
        clairauts.append(path_layer.clairaut_c)
        total_fiber += path_layer.total_fiber_length_mm

    final_prof = buildup.profile_after_layers(n_layers)

    return LayeredPathResult(
        paths=paths,
        profiles=profiles,
        clairaut_per_layer=clairauts,
        total_fiber_length_mm=total_fiber,
        final_radius_mm=final_prof.avg_radius_mm,
        base_radius_mm=base_profile.avg_radius_mm,
        total_radial_growth_mm=buildup.radius_growth_mm(n_layers),
    )
