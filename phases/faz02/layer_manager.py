"""
layer_manager.py — Çok Katmanlı Sarım Yöneticisi
==================================================
Seçilen PatternCandidate'i kullanarak tüm katmanlar için
tam sarım programı (WoundLayer listesi) üretir.

Teori:
  Her katman, bir öncekine göre φ_offset kadar döndürülmüş
  başlangıç noktasına sahiptir. Bu, bant bantın aralarına girmesini
  ve homojen kalınlık dağılımını sağlar.

  φ_offset(layer_i) = i · (2π / n) / d   [rad]

  Örnek (n=27, d=2):
    Layer 0: φ_offset = 0
    Layer 1: φ_offset = π/27 = 0.1164 rad = 6.667°

  Helical katmanlar: Her devre +α (forward) ve -α (return) içerir.
  Balanced laminate: [+α / -α] plylar otomatik oluşur.

  Hoop katmanı:
    α_hoop = arctan(2πR/b) ≈ 88-89°
    Her devre Δz = b (tam bant genişliği)
    n_hoop = ceil(L / b) devre

Sarım Programı Çıktısı:
  WindingSchedule: Tüm katmanlar için sıralı devre listesi
  Her WoundCircuit: (layer_idx, circuit_idx, phi_start, winding_type)
  ToolPoint listesine HelicalPathGenerator üzerinden dönüştürülür.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, Iterator, List, Optional, Tuple

from geometry import CylindricalMandrel
from pattern_solver import PatternCandidate
from winding_math import (
    WindingParameters,
    ClairautConstants,
    HelicalPathGenerator,
    ToolPoint,
)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class WindingType(Enum):
    """Sarım türü."""
    HELICAL = auto()  # α ∈ (5°, 85°) — çapraz sarım
    HOOP    = auto()  # α ≈ 88-89°    — çevresel sarım


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class HoopParameters:
    """
    Hoop winding parametreleri.

    alpha_hoop_rad: Gerçek hoop sarım açısı (≠ 90°, ≈ 88-89°)
    n_circuits:     Gerekli devre sayısı = ceil(L/b)
    delta_z:        Her devredeki eksenel ilerleme = b [mm]

    Hesaplama:
      tan(α_hoop) = 2πR/b  →  α_hoop = arctan(2πR/b)
      cos(α_hoop) = b / √(b² + 4π²R²)
    """
    alpha_hoop_rad: float
    n_circuits:     int
    delta_z:        float   # = b [mm]
    b_eff_hoop:     float   # = b / cos(α_hoop) ≈ b (çok küçük fark)

    @property
    def alpha_hoop_deg(self) -> float:
        return math.degrees(self.alpha_hoop_rad)

    @classmethod
    def from_mandrel(
        cls,
        radius:    float,
        length:    float,
        bandwidth: float,
    ) -> "HoopParameters":
        alpha_hoop = math.atan2(2.0 * math.pi * radius, bandwidth)
        cos_a      = math.cos(alpha_hoop)
        b_eff      = bandwidth / cos_a
        n_circ     = math.ceil(length / bandwidth)
        return cls(
            alpha_hoop_rad = alpha_hoop,
            n_circuits     = n_circ,
            delta_z        = bandwidth,
            b_eff_hoop     = b_eff,
        )

    def summary(self) -> str:
        return (
            f"Hoop Winding Parameters\n"
            f"  α_hoop:     {self.alpha_hoop_deg:.4f}° (90° - {90 - self.alpha_hoop_deg:.4f}°)\n"
            f"  Devre sayısı: {self.n_circuits}\n"
            f"  Δz / devre: {self.delta_z:.4f} mm\n"
            f"  b_eff:      {self.b_eff_hoop:.4f} mm\n"
        )


@dataclass(slots=True)
class WoundCircuit:
    """
    Tek bir sarım devresi — tüm toolpath ve metadata.

    layer_index:    Katman numarası (0-based)
    circuit_index:  Bu katmandaki devre numarası (0-based)
    global_index:   Program içindeki toplam sıra (0-based)
    winding_type:   HELICAL veya HOOP
    phi_start_rad:  Bu devrenin başlangıç azimut açısı [rad]
    phi_end_rad:    Bu devrenin bitiş azimut açısı [rad] (kümülatif)
    toolpath:       Hesaplanan ToolPoint listesi
    """
    layer_index:   int
    circuit_index: int
    global_index:  int
    winding_type:  WindingType
    phi_start_rad: float
    phi_end_rad:   float
    toolpath:      List[ToolPoint] = field(default_factory=list)

    @property
    def phi_start_deg(self) -> float:
        return math.degrees(self.phi_start_rad)

    @property
    def phi_end_deg(self) -> float:
        return math.degrees(self.phi_end_rad)

    @property
    def arc_length_mm(self) -> float:
        """Bu devrenin toplam arc-length'i [mm]."""
        if not self.toolpath:
            return 0.0
        return self.toolpath[-1].s - self.toolpath[0].s

    def __repr__(self) -> str:
        return (
            f"WoundCircuit(L={self.layer_index}, C={self.circuit_index}, "
            f"global={self.global_index}, "
            f"φ_start={self.phi_start_deg:.2f}°, "
            f"φ_end={self.phi_end_deg:.2f}°, "
            f"type={self.winding_type.name})"
        )


@dataclass(slots=True)
class WoundLayer:
    """
    Tek bir sarım katmanı.

    layer_index:  Katman numarası (0-based)
    winding_type: HELICAL veya HOOP
    phi_offset:   Bu katmanın başlangıç φ ofseti [rad]
    circuits:     Bu katmandaki tüm devreler
    """
    layer_index:  int
    winding_type: WindingType
    phi_offset:   float        # [rad]
    circuits:     List[WoundCircuit] = field(default_factory=list)

    @property
    def n_circuits(self) -> int:
        return len(self.circuits)

    @property
    def total_arc_length_mm(self) -> float:
        return sum(c.arc_length_mm for c in self.circuits)

    @property
    def phi_offset_deg(self) -> float:
        return math.degrees(self.phi_offset)

    def __repr__(self) -> str:
        return (
            f"WoundLayer(idx={self.layer_index}, "
            f"type={self.winding_type.name}, "
            f"φ_offset={self.phi_offset_deg:.4f}°, "
            f"circuits={self.n_circuits})"
        )


@dataclass(slots=True)
class WindingSchedule:
    """
    Tam sarım programı: tüm katmanlar ve devreler.

    statistics: Özet istatistikler
    """
    mandrel:         CylindricalMandrel
    winding:         WindingParameters
    constants:       ClairautConstants
    pattern:         PatternCandidate
    layers:          List[WoundLayer]     = field(default_factory=list)
    hoop_params:     Optional[HoopParameters] = None

    @property
    def total_circuits(self) -> int:
        return sum(l.n_circuits for l in self.layers)

    @property
    def total_arc_length_mm(self) -> float:
        return sum(l.total_arc_length_mm for l in self.layers)

    @property
    def total_fiber_m(self) -> float:
        return self.total_arc_length_mm / 1000.0

    def all_circuits(self) -> Iterator[WoundCircuit]:
        """Tüm devreleri global sırayla ver."""
        for layer in self.layers:
            yield from layer.circuits

    def estimate_time_min(self) -> float:
        """Tahmini sarım süresi [dakika]."""
        F_mm_min = self.winding.fiber_speed * math.cos(self.winding.alpha_rad) * 60.0
        if F_mm_min <= 0:
            return 0.0
        # Her geçiş: 2·L mm eksenel mesafe
        total_x_travel = self.pattern.k * 2.0 * self.mandrel.length
        return total_x_travel / F_mm_min

    def summary(self) -> str:
        lines = [
            "=" * 60,
            "WINDING SCHEDULE",
            "=" * 60,
            f"  Pattern:    n={self.pattern.n}, p={self.pattern.p}, k={self.pattern.k}",
            f"  Katmanlar:  {len(self.layers)} adet",
            f"  Devreler:   {self.total_circuits} toplam",
            f"  Fiber:      {self.total_fiber_m:.2f} m",
            f"  Süre (est): {self.estimate_time_min():.1f} dak",
            "-" * 60,
        ]
        for layer in self.layers:
            lines.append(
                f"  Katman {layer.layer_index:2d} [{layer.winding_type.name:<8}]  "
                f"φ_offset={layer.phi_offset_deg:7.3f}°  "
                f"circuits={layer.n_circuits:3d}"
            )
        lines.append("=" * 60)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Layer Manager
# ---------------------------------------------------------------------------

class LayerManager:
    """
    Çok katmanlı winding programı üreticisi.

    Sorumluluklar:
      1. PatternCandidate'den φ_offset değerlerini hesapla
      2. Her katman için WoundLayer oluştur
      3. Her devre için başlangıç φ hesapla
      4. HelicalPathGenerator kullanarak toolpath üret
      5. WindingSchedule döndür

    Kullanım:
        manager = LayerManager(mandrel, winding, constants, pattern)
        schedule = manager.build_schedule(n_points_per_pass=100)

    Hoop desteği:
        manager = LayerManager(..., include_hoop=True)
        schedule = manager.build_schedule()
        # → Son katman hoop katmanıdır
    """

    def __init__(
        self,
        mandrel:       CylindricalMandrel,
        winding:       WindingParameters,
        constants:     ClairautConstants,
        pattern:       PatternCandidate,
        include_hoop:  bool = False,
    ) -> None:
        self.mandrel      = mandrel
        self.winding      = winding
        self.constants    = constants
        self.pattern      = pattern
        self.include_hoop = include_hoop

        # Helical path generator (yeniden kullanılır)
        self._helix_gen = HelicalPathGenerator(mandrel, winding, constants)

    # -----------------------------------------------------------------------
    # Layer Offset Hesabı
    # -----------------------------------------------------------------------

    def compute_layer_offsets(self) -> List[float]:
        """
        Her katman için φ_offset değerini hesapla.

        Formül: φ_offset(i) = i · (2π/n) / d   [rad]

        Bu formül:
          - d=1: offset=0 (tek katman, offset anlamsız)
          - d=2: [0, π/n]           → %50 kayma
          - d=4: [0, π/2n, π/n, 3π/2n] → %25 kayma

        Koussios (2004) s.127: "Preferably 50% overlap for d=2"
        """
        n = self.pattern.n
        d = self.pattern.d
        return [
            i * (2.0 * math.pi / n) / d
            for i in range(d)
        ]

    def compute_hoop_layer_offset(self, layer_idx: int) -> float:
        """
        Hoop katmanı φ ofseti.
        Hoop katmanı her zaman son eklendiğinden eksenel kayma yoktur.
        """
        return 0.0  # Hoop önceki helical katmanın üstüne başlar

    # -----------------------------------------------------------------------
    # Circuit Phi Start
    # -----------------------------------------------------------------------

    def circuit_phi_start(
        self,
        layer_idx:   int,
        circuit_idx: int,
        phi_offset:  float,
    ) -> float:
        """
        Belirli bir devrenin başlangıç φ açısı.

        Formül:
          φ_start(layer, circuit) = φ_offset(layer) + circuit · (2π·p/n)

          Her devre n·Δθ/(2π) ≈ p azimut "slot" atlar.
          Dolayısıyla circuit_idx devre sonrasında:
          φ_cumulative = circuit_idx · Δθ_circuit  (tek devrenin tam artışı)

        Burada:
          Δθ_circuit = K = turn-around açısı (ileri+geri)

        DİKKAT: φ_start kümülatif, 2π'yi geçebilir ve geçmelidir.
        G-code'da A ekseni kümülatif derece kullanır.
        """
        K = self.constants.dphi_dz * 2.0 * self.mandrel.length  # turn-around [rad]
        # Her devre K rad artış
        return phi_offset + circuit_idx * K

    # -----------------------------------------------------------------------
    # Schedule Builder
    # -----------------------------------------------------------------------

    def build_schedule(
        self,
        n_points_per_pass:     int  = 100,
        compute_toolpath:      bool = True,
    ) -> WindingSchedule:
        """
        Tam winding programını oluştur.

        Args:
            n_points_per_pass: Her geçiş için toolpath nokta sayısı
            compute_toolpath:  True → toolpath hesapla; False → sadece schedule meta

        Returns:
            WindingSchedule nesnesi
        """
        offsets     = self.compute_layer_offsets()
        all_layers: List[WoundLayer] = []
        global_idx  = 0

        # --- Helical katmanlar ---
        for layer_i, phi_offset in enumerate(offsets):
            circuits: List[WoundCircuit] = []

            for circ_i in range(self.pattern.k):
                phi_start = self.circuit_phi_start(layer_i, circ_i, phi_offset)

                toolpath: List[ToolPoint] = []
                if compute_toolpath:
                    toolpath = self._helix_gen.generate_circuit(
                        phi_offset        = phi_start,
                        n_points_per_pass = n_points_per_pass,
                    )

                # Devre sonu φ
                K        = self.constants.dphi_dz * 2.0 * self.mandrel.length
                phi_end  = phi_start + K

                circuit = WoundCircuit(
                    layer_index   = layer_i,
                    circuit_index = circ_i,
                    global_index  = global_idx,
                    winding_type  = WindingType.HELICAL,
                    phi_start_rad = phi_start,
                    phi_end_rad   = phi_end,
                    toolpath      = toolpath,
                )
                circuits.append(circuit)
                global_idx += 1

            layer = WoundLayer(
                layer_index  = layer_i,
                winding_type = WindingType.HELICAL,
                phi_offset   = phi_offset,
                circuits     = circuits,
            )
            all_layers.append(layer)

        # --- Hoop katmanı (opsiyonel) ---
        hoop_params: Optional[HoopParameters] = None
        if self.include_hoop:
            hoop_params = HoopParameters.from_mandrel(
                radius    = self.mandrel.radius,
                length    = self.mandrel.length,
                bandwidth = self.winding.bandwidth,
            )
            hoop_circuits = self._build_hoop_circuits(
                layer_idx          = len(all_layers),
                hoop_params        = hoop_params,
                global_idx_start   = global_idx,
                n_points_per_pass  = n_points_per_pass,
                compute_toolpath   = compute_toolpath,
            )
            hoop_layer = WoundLayer(
                layer_index  = len(all_layers),
                winding_type = WindingType.HOOP,
                phi_offset   = 0.0,
                circuits     = hoop_circuits,
            )
            all_layers.append(hoop_layer)

        return WindingSchedule(
            mandrel     = self.mandrel,
            winding     = self.winding,
            constants   = self.constants,
            pattern     = self.pattern,
            layers      = all_layers,
            hoop_params = hoop_params,
        )

    # -----------------------------------------------------------------------
    # Hoop Circuit Builder
    # -----------------------------------------------------------------------

    def _build_hoop_circuits(
        self,
        layer_idx:         int,
        hoop_params:       HoopParameters,
        global_idx_start:  int,
        n_points_per_pass: int,
        compute_toolpath:  bool,
    ) -> List[WoundCircuit]:
        """
        Hoop katmanı için devre listesi oluştur.

        Her hoop devresi: z_start → z_start + b (tek geçiş, geri dönüş yok)
        Sonraki devre: z = z_start + b'den başlar.

        Hoop için HelicalPathGenerator kullanılır, ama:
          - α = α_hoop (≈ 88-89°)
          - Her devre yalnızca forward geçiş
          - z: 0→b, b→2b, ..., (n-1)b→L
        """
        # Hoop için ayrı bir WindingParameters oluştur
        hoop_winding = WindingParameters(
            alpha_rad   = hoop_params.alpha_hoop_rad,
            bandwidth   = self.winding.bandwidth,
            fiber_speed = self.winding.fiber_speed,
            n_layers    = 1,
        )
        from winding_math import ClairautCalculator
        from geometry import CylindricalMandrel
        hoop_calc    = ClairautCalculator(self.mandrel, hoop_winding)
        hoop_consts  = hoop_calc.compute()
        hoop_gen     = HelicalPathGenerator(self.mandrel, hoop_winding, hoop_consts)

        b   = self.winding.bandwidth
        L   = self.mandrel.length
        circuits: List[WoundCircuit] = []

        for circ_i in range(hoop_params.n_circuits):
            z_start = circ_i * b
            z_end   = min(z_start + b, L)
            phi_st  = circ_i * 2.0 * math.pi  # her devre 1 tam devir

            toolpath: List[ToolPoint] = []
            if compute_toolpath:
                toolpath = hoop_gen.generate_pass(
                    z_start   = z_start,
                    z_end     = z_end,
                    phi_start = phi_st,
                    n_points  = max(2, n_points_per_pass // 10),
                    segment   = "hoop",
                    s_offset  = circ_i * b / math.cos(hoop_params.alpha_hoop_rad),
                )

            circuit = WoundCircuit(
                layer_index   = layer_idx,
                circuit_index = circ_i,
                global_index  = global_idx_start + circ_i,
                winding_type  = WindingType.HOOP,
                phi_start_rad = phi_st,
                phi_end_rad   = phi_st + 2.0 * math.pi,
                toolpath      = toolpath,
            )
            circuits.append(circuit)

        return circuits
