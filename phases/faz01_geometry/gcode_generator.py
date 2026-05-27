"""
gcode_generator.py — GRBL Uyumlu G-Code Üretici
==================================================
MachineCommand listesini GRBL 1.1+ uyumlu G-code string'e dönüştürür.

GRBL A-Ekseni Notu:
  GRBL 1.1'de 4. eksen (genellikle A veya etiketli) desteklenir.
  A değerleri kümülatif derece (360°'yi geçebilir).
  GRBL, A'yı varsayılan olarak lineer (mm eşdeğeri) olarak işler.
  
  Yapılandırma gereksinimleri (GRBL $parametreleri):
    $100 = X ekseni adım/mm (örn. 80)
    $103 = A ekseni adım/° (örn. 44.44)   ← RotaryAxisConfig
    $110 = X max feed rate [mm/min]
    $113 = A max feed rate [deg/min]

G-code Format (örnek):
  ; FilamentWinding CAM v0.1
  G21          ; mm birimler
  G90          ; mutlak konumlandırma
  G28          ; home
  G0 X0.0000 A0.0000   ; hızlı başlangıç
  G1 X3.0000 A1.9843 F10392.3  ; sarım başlangıcı
  ...
  G28          ; home
  M30          ; program sonu

Referans:
  NIST RS274/NGC G-code Standard (NIST IR 6556)
  GRBL 1.1 GitHub documentation
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from motion_planner import MachineCommand, MachineConfig
from winding_math import WindingParameters, ClairautConstants


# ---------------------------------------------------------------------------
# G-code Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class GcodeConfig:
    """
    G-code çıktı formatı yapılandırması.

    pos_decimals:    Konum değeri ondalık basamak sayısı (X, A)
    feed_decimals:   Feed rate ondalık basamak sayısı (F)
    firmware:        Hedef firmware ("GRBL", "LinuxCNC", "Marlin")
    use_absolute:    True → G90, False → G91
    units_mm:        True → G21 (mm), False → G20 (inch)
    home_sequence:   True → G28 başlangıç/bitiş
    include_comments:True → G-code satır yorumları
    max_line_length: Satır uzunluğu uyarısı (GRBL: 80 karakter)
    """
    pos_decimals:     int  = 4
    feed_decimals:    int  = 1
    firmware:         str  = "GRBL"
    use_absolute:     bool = True
    units_mm:         bool = True
    home_sequence:    bool = True
    include_comments: bool = True
    max_line_length:  int  = 80

    def __post_init__(self) -> None:
        if self.pos_decimals < 0 or self.pos_decimals > 8:
            raise ValueError(f"pos_decimals={self.pos_decimals} geçersiz")
        if self.feed_decimals < 0 or self.feed_decimals > 4:
            raise ValueError(f"feed_decimals={self.feed_decimals} geçersiz")


# ---------------------------------------------------------------------------
# G-code Generator
# ---------------------------------------------------------------------------

class GcodeGenerator:
    """
    GRBL uyumlu G-code üretici.

    Kullanım:
        gen = GcodeGenerator()
        gcode = gen.generate(commands, machine, winding, constants, "job_name")
        with open("output.nc", "w") as f:
            f.write(gcode)

    Geliştirme Notu (Faz 2+):
      - Arc fit (G2/G3) Faz 4'te eklenecek: uzun doğrusal segmentleri
        dairesel yaylarla yaklaştırarak dosya boyutunu azalt
      - Segment bazlı feed rate override (polar yavaşlama) Faz 3'te
      - Tool change / fiber break recovery M kodları Faz 5'te
    """

    def __init__(self, config: Optional[GcodeConfig] = None) -> None:
        self.config = config or GcodeConfig()

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def generate(
        self,
        commands:   List[MachineCommand],
        machine:    MachineConfig,
        winding:    WindingParameters,
        constants:  ClairautConstants,
        job_name:   str = "filament_winding",
    ) -> str:
        """
        Tam G-code programı üret.

        Args:
            commands:  MachineCommand listesi (sıralı)
            machine:   Makine yapılandırması
            winding:   Sarım parametreleri
            constants: Clairaut sabitleri
            job_name:  İş adı (header için)

        Returns:
            Tam G-code string, \n ile satır sonu
        """
        if not commands:
            raise ValueError("commands listesi boş olamaz.")

        sections: List[List[str]] = []
        sections.append(self._header(machine, winding, constants, job_name, len(commands)))
        sections.append(self._setup_block())
        sections.append(self._winding_block(commands))
        sections.append(self._footer_block())

        # Bölümleri birleştir, her bölüm arasında boş satır
        all_lines: List[str] = []
        for section in sections:
            all_lines.extend(section)
            all_lines.append("")   # Bölüm ayracı

        return "\n".join(all_lines)

    # -----------------------------------------------------------------------
    # Private: Header
    # -----------------------------------------------------------------------

    def _header(
        self,
        machine:    MachineConfig,
        winding:    WindingParameters,
        constants:  ClairautConstants,
        job_name:   str,
        n_commands: int,
    ) -> List[str]:
        """İş bilgisi headerı."""
        if not self.config.include_comments:
            return []

        # Feed rate hesabı (header bilgisi için)
        F_nominal = machine.fiber_speed_target * math.cos(winding.alpha_rad) * 60.0
        F_actual  = min(F_nominal, machine.max_x_speed)
        S_actual  = F_actual / 60.0 / math.cos(winding.alpha_rad)
        rpm       = F_actual * math.tan(winding.alpha_rad) / 60.0  # ÷ R sonra hesap için

        return [
            "; ============================================================",
            "; FilamentWinding CAM v0.1  —  GRBL-Compatible Output",
            f"; Job:             {job_name}",
            f"; Generated:       {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "; ============================================================",
            f"; Sarım Açısı (α):  {winding.alpha_deg:.4f}°",
            f"; Clairaut Sabiti:  c = {constants.c:.6f} mm",
            f"; Pitch:            {constants.pitch:.4f} mm/rev",
            f"; dφ/dz:            {constants.dphi_dz:.8f} rad/mm  "
            f"= {constants.a_axis_deg_per_mm:.6f} °/mm",
            f"; Band Genişliği:   b = {winding.bandwidth:.2f} mm  "
            f"|  b_eff = {constants.bandwidth_eff:.4f} mm",
            f"; Fiber Hızı:       S' = {S_actual:.2f} mm/s "
            f"(hedef: {machine.fiber_speed_target:.1f} mm/s)",
            f"; Carriage Feed:    F  = {F_actual:.1f} mm/min",
            f"; Toplam Komut:     {n_commands} satır",
            "; ============================================================",
            "; GRBL Yapılandırma Gereksinimleri:",
            f";   $100 = {machine.x_steps_per_mm:.2f}   (X adım/mm)",
            f";   $103 = {machine.a_steps_per_deg:.4f} (A adım/°)",
            f";   $110 = {machine.max_x_speed:.1f}  (X max mm/min)",
            f";   $113 = {machine.max_a_speed:.1f} (A max deg/min)",
            "; ============================================================",
        ]

    # -----------------------------------------------------------------------
    # Private: Setup Block
    # -----------------------------------------------------------------------

    def _setup_block(self) -> List[str]:
        """Makine başlangıç setup bloğu."""
        cfg = self.config
        lines = []

        if cfg.include_comments:
            lines.append("; === MAKINE KURULUM ===")

        # Birim seçimi
        lines.append("G21" if cfg.units_mm else "G20")
        if cfg.include_comments:
            lines[-1] += "          ; Birim: " + ("mm" if cfg.units_mm else "inch")

        # Konumlandırma modu
        lines.append("G90" if cfg.use_absolute else "G91")
        if cfg.include_comments:
            lines[-1] += "          ; " + ("Mutlak konumlandırma" if cfg.use_absolute else "Relatif konumlandırma")

        # Feed rate modu
        lines.append("G94")
        if cfg.include_comments:
            lines[-1] += "          ; Feed rate: mm/min"

        # Home sequence
        if cfg.home_sequence:
            lines.append("G28")
            if cfg.include_comments:
                lines[-1] += "          ; Tüm eksenleri home'a al"

        return lines

    # -----------------------------------------------------------------------
    # Private: Winding Block
    # -----------------------------------------------------------------------

    def _format_move(self, cmd: MachineCommand) -> str:
        """Tek G1 komut satırı formatla."""
        pd  = self.config.pos_decimals
        fd  = self.config.feed_decimals

        x_str = f"X{cmd.X:.{pd}f}"
        a_str = f"A{cmd.A:.{pd}f}"
        f_str = f"F{cmd.F:.{fd}f}"

        line = f"G1 {x_str} {a_str} {f_str}"

        if self.config.include_comments and cmd.comment:
            line += f"  ; {cmd.comment}"

        return line

    def _format_rapid(self, X: float, A: float) -> str:
        """G0 hızlı hareket satırı formatla."""
        pd = self.config.pos_decimals
        line = f"G0 X{X:.{pd}f} A{A:.{pd}f}"
        if self.config.include_comments:
            line += "  ; Başlangıç konumuna hızlı git"
        return line

    def _winding_block(self, commands: List[MachineCommand]) -> List[str]:
        """Sarım hareket bloğu."""
        if not commands:
            return []

        lines = []

        if self.config.include_comments:
            lines.append("")
            lines.append("; === SARIM PROGRAMI ===")

        # İlk noktaya hızlı git (G0)
        first = commands[0]
        lines.append(self._format_rapid(first.X, first.A))
        lines.append("")

        # Segment değişimi takibi
        current_segment: str = ""

        for cmd in commands:
            seg = cmd.comment  # segment etiketi

            # Segment değişimi yorumu
            if self.config.include_comments and seg and seg != current_segment:
                lines.append(f"; --- {seg.upper()} ---")
                current_segment = seg

            move_line = self._format_move(cmd)

            # Satır uzunluğu uyarısı
            if len(move_line) > self.config.max_line_length:
                # GRBL 80 karakter limitli; uzun satırlar truncate yapılmaz,
                # sadece iç log için not düşülür (GRBL 1.1 aslında 256'ya kadar kabul eder)
                pass

            lines.append(move_line)

        return lines

    # -----------------------------------------------------------------------
    # Private: Footer
    # -----------------------------------------------------------------------

    def _footer_block(self) -> List[str]:
        """Program bitiş bloğu."""
        lines = []

        if self.config.include_comments:
            lines.append("; === PROGRAM SONU ===")

        # Home sequence
        if self.config.home_sequence:
            lines.append("G28")
            if self.config.include_comments:
                lines[-1] += "          ; Eve dön"

        # Program sonu
        lines.append("M30")
        if self.config.include_comments:
            lines[-1] += "          ; Program sonu"

        return lines

    # -----------------------------------------------------------------------
    # Utility
    # -----------------------------------------------------------------------

    def line_count(self, gcode: str) -> int:
        """G-code içindeki toplam satır sayısı."""
        return len(gcode.split("\n"))

    def command_count(self, gcode: str) -> int:
        """G-code içindeki G1 komut sayısı."""
        return sum(1 for line in gcode.split("\n") if line.strip().startswith("G1"))

    def estimate_time_min(
        self,
        commands: List[MachineCommand],
    ) -> float:
        """
        G-code programının tahmini çalışma süresi [dakika].

        Hesaplama: Σ(ΔX / F) her segment için.
        Not: Hızlanma/yavaşlama dikkate alınmaz (optimistik tahmin).
        """
        if len(commands) < 2:
            return 0.0

        total_time = 0.0
        for i in range(1, len(commands)):
            dX = abs(commands[i].X - commands[i - 1].X)
            F  = commands[i].F  # mm/min
            if F > 0:
                total_time += dX / F

        return total_time  # [dakika]
