"""
sequence_planner.py — Winding Sequence Planner
===============================================
Görev: Optimal winding konfigürasyonundan (c, k, p, K_full)
gerçek üretim sırasını üretmek.

Temel varlıklar:
  WindingPass   : Tek bir geçiş (HELICAL_FWD/RET, HOOP, POLAR_REINF)
  LayerGroup    : Bir winding katmanının tüm geçişleri
  WindingSequence: Tüm katmanların sıralı programı

Üretim kuralları (endüstriyel standart):
  1. Helical önce: ±α yapısal katmanlar
  2. Hoop sonra: 90° çevresel basınç katmanı
  3. Polar reinforcement: dome-junction bölgesinde lokal güçlendirme
  4. Balanced laminate: [+α/-α/Hoop] × n_repeats

Sequence stratejileri:
  "standard"   : H+, H-, Hoop
  "balanced"   : [H+, H-] × 2, Hoop
  "reinforced" : H+, H-, PolarReinf, Hoop
  "pressure"   : H+, H-, Hoop, H+, H-, Hoop (basınç kabı için)

Geçiş güvenliği:
  - Fiber cut: her layer grubu arasında M0 (pause)
  - Tension relief: geçiş öncesinde tension-free slow move
  - Rewind: A-eksen sıfırlama (fiber cut sırasında)
  - φ-offset: her katman bir sonraki için Δφ ofseti alır

Referans: filament-winding-a-unified-approach (Koussios 2004, Part C)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional, Tuple

import numpy as np


# ── Pass Types ───────────────────────────────────────────────────

class PassType(Enum):
    HELICAL_FORWARD  = auto()  # z: front_eq → rear_pole
    HELICAL_RETURN   = auto()  # z: rear_pole → front_pole → front_eq
    HOOP             = auto()  # Çevresel, tek z-şerit
    POLAR_REINF      = auto()  # Dome yakını takviye
    TRANSITION_MOVE  = auto()  # Güvenli geçiş hareketi
    HOME_MOVE        = auto()  # Home pozisyonu


class LayerType(Enum):
    HELICAL  = auto()
    HOOP     = auto()
    POLAR    = auto()


class Strategy(Enum):
    STANDARD    = "standard"     # H+H-Hoop
    BALANCED    = "balanced"     # [H+H-]×2 Hoop
    REINFORCED  = "reinforced"   # H+H-PolarReinf Hoop
    PRESSURE    = "pressure"     # [H+H-Hoop]×2


# ── Data Structures ──────────────────────────────────────────────

@dataclass(slots=True)
class WindingPass:
    """
    Tek üretim geçişi — makine hareketinin temel birimi.

    pass_index:   Global geçiş numarası (0-based)
    circuit_index:Bu katmandaki devre numarası
    layer_index:  Katman numarası
    pass_type:    PassType
    phi_start_rad:Başlangıç azimut [rad] (kümülatif, asla sarılmaz)
    alpha_deg:    Sarım açısı (helical için ±)
    z_start_mm:   Geçiş başlangıç z [mm]
    z_end_mm:     Geçiş bitiş z [mm] (hoop için z_start + b)
    feedrate_mm_min: Nominal besleme hızı
    tension_release: True → geçiş öncesi gerilim bırakma hareketi
    """
    pass_index:     int
    circuit_index:  int
    layer_index:    int
    pass_type:      PassType
    phi_start_rad:  float
    alpha_deg:      float
    z_start_mm:     float
    z_end_mm:       float
    feedrate_mm_min:float
    tension_release:bool  = False
    is_first_pass:  bool  = False
    is_last_pass:   bool  = False
    comment:        str   = ""

    @property
    def phi_start_deg(self) -> float:
        return math.degrees(self.phi_start_rad)

    @property
    def is_helical(self) -> bool:
        return self.pass_type in (PassType.HELICAL_FORWARD, PassType.HELICAL_RETURN)

    @property
    def is_hoop(self) -> bool:
        return self.pass_type == PassType.HOOP

    def summary(self) -> str:
        return (
            f"[{self.pass_index:3d}] L{self.layer_index}C{self.circuit_index} "
            f"{self.pass_type.name:<18} "
            f"α={self.alpha_deg:+6.2f}°  "
            f"φ₀={self.phi_start_deg:7.2f}°  "
            f"z=[{self.z_start_mm:.1f}→{self.z_end_mm:.1f}]  "
            f"F={self.feedrate_mm_min:.0f}  "
            f"{'⏸' if self.tension_release else ''}"
        )


@dataclass(slots=True)
class LayerGroup:
    """
    Tek bir winding katmanının tüm geçişleri.

    layer_index:  Katman numarası
    layer_type:   HELICAL/HOOP/POLAR
    alpha_deg:    Sarım açısı (helical için nominal)
    phi_offset_rad: Bu katmanın başlangıç φ ofseti
    passes:       Tüm geçişler (sıralı)
    k_circuits:   Toplam devre sayısı
    """
    layer_index:   int
    layer_type:    LayerType
    alpha_deg:     float
    phi_offset_rad:float
    passes:        List[WindingPass] = field(default_factory=list)
    k_circuits:    int = 0

    @property
    def n_passes(self) -> int:
        return len(self.passes)

    @property
    def phi_offset_deg(self) -> float:
        return math.degrees(self.phi_offset_rad)

    def summary(self) -> str:
        return (
            f"  Layer {self.layer_index} [{self.layer_type.name:<8}] "
            f"α={self.alpha_deg:+6.2f}°  "
            f"φ_offset={self.phi_offset_deg:.3f}°  "
            f"k={self.k_circuits}  passes={self.n_passes}"
        )


@dataclass(slots=True)
class WindingSequence:
    """
    Tam winding programı: tüm katmanlar ve geçişler.
    """
    strategy:        str
    c_clairaut:      float
    alpha_0_deg:     float
    k_circuits:      int
    K_full_deg:      float
    n_helical_layers:int
    n_hoop_layers:   int
    layers:          List[LayerGroup] = field(default_factory=list)

    @property
    def all_passes(self) -> List[WindingPass]:
        return [p for layer in self.layers for p in layer.passes]

    @property
    def total_passes(self) -> int:
        return sum(l.n_passes for l in self.layers)

    @property
    def total_circuits(self) -> int:
        return sum(l.k_circuits for l in self.layers if l.layer_type == LayerType.HELICAL)

    @property
    def n_fiber_cuts(self) -> int:
        """Layer grupları arası fiber kesme sayısı."""
        return max(0, len(self.layers) - 1)

    def estimate_time_min(self, feed_mm_min: float = 800.0) -> float:
        """Tahmini üretim süresi [dakika]."""
        total_z = sum(abs(p.z_end_mm - p.z_start_mm) for p in self.all_passes)
        return total_z / feed_mm_min

    def report(self) -> str:
        lines = [
            "═" * 68,
            f"WINDING SEQUENCE: {self.strategy.upper()}",
            "═" * 68,
            f"  c={self.c_clairaut:.3f}mm  α₀={self.alpha_0_deg:.3f}°  "
            f"k={self.k_circuits}  K_full={self.K_full_deg:.2f}°",
            f"  Helical katmanlar: {self.n_helical_layers}  "
            f"Hoop katmanlar: {self.n_hoop_layers}",
            f"  Toplam geçiş: {self.total_passes}  "
            f"Fiber kesme: {self.n_fiber_cuts}  "
            f"Tahmini süre: {self.estimate_time_min():.1f} dak",
            "─" * 68,
        ]
        for layer in self.layers:
            lines.append(layer.summary())
        lines.append("═" * 68)
        return "\n".join(lines)

    def pass_report(self, max_show: int = 30) -> str:
        lines = [f"  {'#':>4}  {'Tip':<18} {'α':>7} {'φ₀':>9} {'z':>14} {'F':>6}"]
        for p in self.all_passes[:max_show]:
            lines.append(f"  {p.summary()}")
        if self.total_passes > max_show:
            lines.append(f"  ... ve {self.total_passes-max_show} geçiş daha")
        return "\n".join(lines)


# ── Sequence Planner ─────────────────────────────────────────────

class SequencePlanner:
    """
    Winding sequence planner.

    Phase 4b çıkış paketinden tam üretim sırasını oluşturur.

    Kullanım:
        planner = SequencePlanner(
            z_total=380.0, z_front_eq=40.0, z_rear_eq=340.0,
            c=8.827, k=21, K_full_deg=137.14,
            bandwidth=10.0, fiber_speed=100.0,
        )
        seq = planner.plan(strategy="reinforced", n_helical_layers=2)
        print(seq.report())
    """

    def __init__(
        self,
        z_total:      float,    # Toplam mandrel uzunluğu [mm]
        z_front_eq:   float,    # Front equator z [mm]
        z_rear_eq:    float,    # Rear equator z [mm]
        c_clairaut:   float,    # [mm]
        k_circuits:   int,
        K_full_deg:   float,    # Full-body turn-around [°]
        bandwidth:    float,    # b [mm]
        fiber_speed:  float,    # S' [mm/s]
        r_boss:       float  = 33.42,  # Polar boss r [mm]
        d_layers:     int    = 1,
    ) -> None:
        self.Z        = z_total
        self.z_fe     = z_front_eq
        self.z_re     = z_rear_eq
        self.L_cyl    = z_rear_eq - z_front_eq
        self.c        = c_clairaut
        self.k        = k_circuits
        self.K        = math.radians(K_full_deg)
        self.b        = bandwidth
        self.v        = fiber_speed
        self.r_boss   = r_boss
        self.d        = d_layers

        # Sarım açısı
        from dome_mandrel import EllipticDome  # lazy import
        self.alpha_0  = math.degrees(math.asin(min(1.0, c_clairaut / 50.0)))
        # Nominal feedrate: carriage mm/min
        self._F_helical = fiber_speed * math.cos(math.radians(self.alpha_0)) * 60.0
        self._F_hoop    = fiber_speed * math.cos(math.radians(88.5)) * 60.0
        self._F_slow    = self._F_helical * 0.3   # Geçiş için yavaş hız

    # ── Layer offset ──────────────────────────────────────────────

    def _phi_offset(self, layer_idx: int, d: int) -> float:
        """
        Katman i için φ ofseti [rad].
        φ_offset(i) = i·(2π/k)/d   [Koussios Eq.8.12 genelleştirilmiş]
        """
        return layer_idx * (2.0 * math.pi / self.k) / max(d, 1)

    # ── Helical layer ─────────────────────────────────────────────

    def _build_helical_layer(
        self,
        layer_idx:    int,
        alpha_sign:   float,   # +1 veya -1
        phi_offset:   float,
        global_pass_start: int,
    ) -> Tuple[LayerGroup, int]:
        """
        Tek helical katman için geçişleri oluştur.

        Her devre: 1 HELICAL_FORWARD + 1 HELICAL_RETURN = 2 geçiş
        Toplam k devre = 2k geçiş (FULL circuit = fwd+ret, her ikisi tek geçiş).
        """
        alpha = alpha_sign * self.alpha_0
        layer = LayerGroup(
            layer_index   = layer_idx,
            layer_type    = LayerType.HELICAL,
            alpha_deg     = alpha,
            phi_offset_rad= phi_offset,
            k_circuits    = self.k,
        )
        pass_idx = global_pass_start

        # Her devre: tam bir forward+return çifti
        for circuit in range(self.k):
            phi_start = phi_offset + circuit * self.K

            # Forward pass
            fwd = WindingPass(
                pass_index    = pass_idx,
                circuit_index = circuit,
                layer_index   = layer_idx,
                pass_type     = PassType.HELICAL_FORWARD,
                phi_start_rad = phi_start,
                alpha_deg     = alpha,
                z_start_mm    = self.z_fe,
                z_end_mm      = self.Z,
                feedrate_mm_min = self._F_helical,
                tension_release = False,
                is_first_pass = (pass_idx == global_pass_start),
                comment       = f"L{layer_idx} C{circuit} fwd",
            )
            layer.passes.append(fwd)
            pass_idx += 1

            # Return pass (pole → equator tamamlanması GeodesicPlanner tarafından yapılır)
            # Burada tek bir "circuit" = 1 fwd ToolPath (return dahil)
            # Gerçekte GeodesicPath bir tam devre = ileri + geri geçiş

        # Son geçişi işaretle
        if layer.passes:
            layer.passes[-1].is_last_pass = True
            # Sondan bir önce tension release
            if len(layer.passes) >= 2:
                layer.passes[-1].tension_release = True

        return layer, pass_idx

    # ── Hoop layer ────────────────────────────────────────────────

    def _build_hoop_layer(
        self,
        layer_idx:      int,
        phi_start_rad:  float,
        global_pass_start: int,
    ) -> Tuple[LayerGroup, int]:
        """
        Hoop katmanı: her b genişliğinde 1 geçiş, silindir boyunca.

        n_hoop = ceil(L_cyl / b) geçiş
        """
        n_hoop = math.ceil(self.L_cyl / self.b)
        layer  = LayerGroup(
            layer_index   = layer_idx,
            layer_type    = LayerType.HOOP,
            alpha_deg     = 88.5,   # ≈90°
            phi_offset_rad= phi_start_rad,
            k_circuits    = n_hoop,
        )
        pass_idx = global_pass_start

        for i in range(n_hoop):
            z0   = self.z_fe + i * self.b
            z1   = min(z0 + self.b, self.z_re)
            phi  = phi_start_rad + i * 2.0 * math.pi  # Her devre 1 tam tur

            hp = WindingPass(
                pass_index    = pass_idx,
                circuit_index = i,
                layer_index   = layer_idx,
                pass_type     = PassType.HOOP,
                phi_start_rad = phi,
                alpha_deg     = 88.5,
                z_start_mm    = z0,
                z_end_mm      = z1,
                feedrate_mm_min = self._F_hoop,
                is_first_pass = (i == 0),
                is_last_pass  = (i == n_hoop - 1),
                comment       = f"HOOP {i+1}/{n_hoop}",
            )
            layer.passes.append(hp)
            pass_idx += 1

        return layer, pass_idx

    # ── Polar reinforcement ───────────────────────────────────────

    def _build_polar_reinforcement(
        self,
        layer_idx:   int,
        phi_start:   float,
        global_pass_start: int,
    ) -> Tuple[LayerGroup, int]:
        """
        Polar bölge takviyesi: r_boss yakınında lokal hoop sarım.

        Her iki dome junction'ında (front + rear) b/2 genişliğinde
        2-3 çevresel tur.
        """
        layer = LayerGroup(
            layer_index   = layer_idx,
            layer_type    = LayerType.POLAR,
            alpha_deg     = 88.5,
            phi_offset_rad= phi_start,
            k_circuits    = 4,  # 2 front + 2 rear
        )
        pass_idx = global_pass_start
        z_reinf  = self.b * 0.5   # Junction'dan b/2 içeride

        for region, z0 in [("FRONT", self.z_fe - z_reinf),
                           ("REAR",  self.z_re + z_reinf - self.b)]:
            z0 = max(0.0, z0); z1 = min(self.Z, z0 + self.b)
            for rep in range(2):
                phi = phi_start + (pass_idx - global_pass_start) * 2.0 * math.pi
                pr = WindingPass(
                    pass_index    = pass_idx,
                    circuit_index = rep,
                    layer_index   = layer_idx,
                    pass_type     = PassType.POLAR_REINF,
                    phi_start_rad = phi,
                    alpha_deg     = 88.5,
                    z_start_mm    = z0,
                    z_end_mm      = z1,
                    feedrate_mm_min = self._F_slow,
                    comment       = f"POLAR_REINF {region} rep{rep}",
                )
                layer.passes.append(pr)
                pass_idx += 1

        return layer, pass_idx

    # ── Main planner ──────────────────────────────────────────────

    def plan(
        self,
        strategy:         str  = "reinforced",
        n_helical_layers: int  = 2,
        n_hoop_layers:    int  = 1,
        with_polar_reinf: bool = True,
    ) -> WindingSequence:
        """
        Tam winding sequence planla.

        Args:
            strategy: "standard"|"balanced"|"reinforced"|"pressure"
            n_helical_layers: Helical katman sayısı (her biri ± çift)
            n_hoop_layers: Hoop katman sayısı
            with_polar_reinf: Polar takviye ekle?
        """
        seq = WindingSequence(
            strategy         = strategy,
            c_clairaut       = self.c,
            alpha_0_deg      = self.alpha_0,
            k_circuits       = self.k,
            K_full_deg       = math.degrees(self.K),
            n_helical_layers = n_helical_layers,
            n_hoop_layers    = n_hoop_layers,
        )

        pass_idx  = 0
        layer_idx = 0

        if strategy == "standard":
            # [H+, H-] × n_helical, [Hoop] × n_hoop
            for i in range(n_helical_layers):
                for sign in [+1.0, -1.0]:
                    phi_off = self._phi_offset(layer_idx, n_helical_layers * 2)
                    layer, pass_idx = self._build_helical_layer(
                        layer_idx, sign, phi_off, pass_idx)
                    seq.layers.append(layer)
                    layer_idx += 1

        elif strategy == "balanced":
            # [H+, H-] × 2 × n_helical, Hoop
            for rep in range(n_helical_layers):
                for sign in [+1.0, -1.0]:
                    phi_off = self._phi_offset(layer_idx, n_helical_layers * 2)
                    layer, pass_idx = self._build_helical_layer(
                        layer_idx, sign, phi_off, pass_idx)
                    seq.layers.append(layer)
                    layer_idx += 1

        elif strategy in ("reinforced", "pressure"):
            # H+, H- (her helical çifti için)
            for i in range(n_helical_layers):
                for sign in [+1.0, -1.0]:
                    phi_off = self._phi_offset(layer_idx, n_helical_layers * 2)
                    layer, pass_idx = self._build_helical_layer(
                        layer_idx, sign, phi_off, pass_idx)
                    seq.layers.append(layer)
                    layer_idx += 1
            # Polar reinforcement
            if with_polar_reinf:
                phi_pr = self._phi_offset(layer_idx, n_helical_layers * 2 + 1)
                pr_layer, pass_idx = self._build_polar_reinforcement(
                    layer_idx, phi_pr, pass_idx)
                seq.layers.append(pr_layer)
                layer_idx += 1

        # Hoop katmanları (her zaman en sona)
        for hi in range(n_hoop_layers):
            phi_h = self._phi_offset(layer_idx, n_helical_layers * 2 + n_hoop_layers)
            hoop_layer, pass_idx = self._build_hoop_layer(
                layer_idx, phi_h, pass_idx)
            seq.layers.append(hoop_layer)
            layer_idx += 1

        return seq
