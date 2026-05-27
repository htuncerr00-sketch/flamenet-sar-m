"""
process_recipe.py — First Carbon Fiber Part Recipe + Cure Cycle + Vacuum Workflow
==================================================================================
Gerçek üretim reçetesi: T700 karbon / EPON 828 epoksi / silindirik basınçlı kap.

Parça: ∅100mm × 300mm silindir, 8 kat ±10.17° helical, Vf=0.55 hedef.
Tasarım basıncı: 10 bar (ASTM D2105 / ISO 14125).

REÇİNE SİSTEMİ (EPON 828 + Ancamine 2049):
  Bileşim: 100 phr EPON 828 + 27 phr Ancamine 2049 (alifatik amin)
  Karıştırma: 3 dakika manuel + 2 dakika vakum (çözünmüş gazı uzaklaştır)
  Pot ömrü @25°C: 90 dakika (kullanım penceresi: 60 dakika)
  Viskozite @25°C: ~7000 cP (optimal sarım için 1000-5000 cP → 45-50°C ısıtma)
  Reçine sıcaklığı: 45°C → η ≈ 2000 cP (işlenebilir)
  Fiber/reçine oranı: 60/40 ağırlık (target Vf=0.55)

CURE DÖNGÜSÜ (Kamal model ile doğrulanmış, Faz 13):
  Aşama 1: 25°C, 2h  (α → ~0.10)  Ağır küresel
  Aşama 2: 80°C, 2h  (α → ~0.75)  Ana kürileme
  Aşama 3: 120°C, 2h (α → ~0.97)  Tamamlama
  Post-cure: 150°C, 1h (α → ~0.99, Tg→178°C)
  Isınma hızı: 2°C/dk maks (termal gradyan < 5°C/mm)

VAKUM İŞ AKIŞI:
  1. Mandrel hazırlama (sökme macunu + salınım bezi)
  2. Sarım (ıslak, 15N fiber gerilmesi)
  3. Peel ply + vakum torbası
  4. Vakum: -0.85 bar → kompaksiyon P ≈ 86kPa
  5. Kür fırına giriş (vakum altında)
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Tuple
import numpy as np

# ── Resin System ──────────────────────────────────────────────────

@dataclass(frozen=True)
class ResinSystem:
    name:           str = "EPON828_Ancamine2049"
    resin:          str = "Hexion EPON 828 (Bisphenol-A diglycidyl ether)"
    hardener:       str = "Evonik Ancamine 2049 (Modified aliphatic amine)"
    mix_ratio_phr:  float = 27.0    # parts per hundred resin
    mix_ratio_weight:str = "100:27 weight"
    pot_life_min:   float = 90.0    # @25°C
    use_window_min: float = 60.0    # Safe processing window
    viscosity_25C_cP:float = 7000.0
    viscosity_45C_cP:float = 2000.0
    process_temp_C: float = 45.0    # Resin bath temperature
    density_g_cc:   float = 1.25
    # Kamal parameters (from Faz 13)
    A1:    float = 1.2e5; A2: float = 3.8e7
    Ea1_J: float = 55000; Ea2_J: float = 72000
    m:     float = 0.85;  n:   float = 1.60
    alpha_gel: float = 0.62
    Tg_inf_C:  float = 178.0
    H_rxn_J_kg:float = 420000.0

@dataclass(frozen=True)
class FiberSpec:
    name:         str = "Toray T700SC-12000-50C"
    tow_size:     str = "12K (12000 filaments)"
    filament_dia: float = 7.0    # µm
    density:      float = 1.80   # g/cc
    tensile_MPa:  float = 4900
    modulus_GPa:  float = 230
    elongation_pct:float = 2.1
    sizing:       str = "EP compatible (50C)"
    tex:          float = 800    # g/km
    bandwidth_mm: float = 10.0   # Tow spread width (flat)

RESIN = ResinSystem()
FIBER = FiberSpec()


# ── Cure Cycle ────────────────────────────────────────────────────

@dataclass(frozen=True)
class CureStage:
    name:          str
    T_target_C:    float
    hold_h:        float
    ramp_C_per_min:float
    alpha_start:   float
    alpha_end:     float    # Kamal model prediction
    notes:         str = ""

@dataclass
class CureCycle:
    stages: List[CureStage] = field(default_factory=list)

    def add_stage(self, stage: CureStage) -> None:
        self.stages.append(stage)

    def total_time_h(self) -> float:
        t = 0.0
        prev_T = 25.0
        for s in self.stages:
            ramp_h = abs(s.T_target_C - prev_T) / max(s.ramp_C_per_min * 60, 1e-6)
            t += ramp_h + s.hold_h
            prev_T = s.T_target_C
        return t

    def critical_exotherm_T_C(self) -> float:
        """Adiabatik maksimum ekzotherm: T_max = T_cure + ΔH/(ρ·Cp)"""
        rho, Cp, HR = 1250, 1500, RESIN.H_rxn_J_kg
        delta_T = HR / (Cp)   # K
        return 120.0 + delta_T   # konservatif

    def report(self) -> str:
        lines = ["  Cure Cycle:"]
        for s in self.stages:
            lines.append(f"    {s.name:<20}: {s.T_target_C}°C × {s.hold_h}h  "
                        f"ramp={s.ramp_C_per_min}°C/min  α:{s.alpha_start:.2f}→{s.alpha_end:.2f}  {s.notes}")
        lines.append(f"    Total: {self.total_time_h():.1f}h")
        lines.append(f"    Max exotherm: {self.critical_exotherm_T_C():.0f}°C (adiabatic)")
        return "\n".join(lines)

def build_cure_cycle() -> CureCycle:
    cc = CureCycle()
    cc.add_stage(CureStage("Pot life / ambient",25,2.0,0.0,0.0,0.10,
        "Sarım tamamlanır, vakum uygulanır"))
    cc.add_stage(CureStage("Ramp to 80°C",80,2.0,2.0,0.10,0.75,
        "Ana kürileme — viskozite düşer, kompaksiyon"))
    cc.add_stage(CureStage("Ramp to 120°C",120,2.0,2.0,0.75,0.97,
        "Tamamlayıcı kürileme — Tg artar"))
    cc.add_stage(CureStage("Post-cure 150°C",150,1.0,2.0,0.97,0.99,
        "Tg → 178°C — mekanik özellikler maksimum"))
    return cc

CURE_CYCLE = build_cure_cycle()


# ── Winding Recipe ────────────────────────────────────────────────

@dataclass(frozen=True)
class WindingRecipe:
    # Mandrel
    mandrel_material:str = "Alüminyum 6061-T6"
    mandrel_R_mm:    float = 50.0    # İç yarıçap = mandrel OD/2
    mandrel_L_mm:    float = 300.0
    mandrel_CTE:     float = 23.1e-6  # Alüminyum CTE

    # Fiber architecture
    pattern:         str = "Helical ±10.17°"
    alpha_deg:       float = 10.17   # Clairaut c=8.827mm, R=50mm
    n_layers:        int  = 8        # ±10.17° × 4 bilayer = 8 total
    k_circuits:      int  = 21       # Circuits per layer

    # Process parameters
    fiber_tension_N: float = 15.0
    feed_speed_mm_s: float = 100.0   # 6000 mm/min
    resin_bath_T_C:  float = 45.0    # Resin temperature

    # Expected outcomes (from Faz 13 model)
    Vf_target:       float = 0.55
    void_target_pct: float = 1.8
    t_wall_mm:       float = 3.2     # 8 layers × 0.4mm/layer
    P_design_bar:    float = 10.0

    # Quality limits
    void_max_pct:    float = 3.0
    Vf_min:          float = 0.50
    Vf_max:          float = 0.65

    def clairaut_constant(self) -> float:
        return self.mandrel_R_mm * math.sin(math.radians(self.alpha_deg))

    def hoop_stress_MPa(self, P_bar: float) -> float:
        """σ_hoop = P·r/t (thin-wall)"""
        P_Pa = P_bar * 1e5
        r    = self.mandrel_R_mm / 1000.0
        t    = self.t_wall_mm / 1000.0
        return P_Pa * r / t / 1e6

    def FPF_pressure_bar(self, UTS_MPa: float = 4900) -> float:
        """First Ply Failure pressure (simplified Tsai-Wu)."""
        sigma_design = self.hoop_stress_MPa(self.P_design_bar)
        SF = UTS_MPa * self.Vf_target / sigma_design
        return self.P_design_bar * SF

    def report(self) -> str:
        c = self.clairaut_constant()
        FPF = self.FPF_pressure_bar()
        sigma_h = self.hoop_stress_MPa(self.P_design_bar)
        return (
            f"  Winding Recipe:\n"
            f"    Fiber: {FIBER.name}  Resin: {RESIN.name}\n"
            f"    Pattern: {self.pattern}  c={c:.3f}mm  k={self.k_circuits}\n"
            f"    Layers: {self.n_layers}  t_wall={self.t_wall_mm}mm\n"
            f"    Tension: {self.fiber_tension_N}N  Speed: {self.feed_speed_mm_s}mm/s\n"
            f"    Vf target: {self.Vf_target}  Void target: {self.void_target_pct}%\n"
            f"    Design P: {self.P_design_bar}bar  σ_hoop={sigma_h:.1f}MPa\n"
            f"    FPF estimate: {FPF:.1f}bar (SF={FPF/self.P_design_bar:.1f})"
        )

WINDING_RECIPE = WindingRecipe()


# ── Vacuum Workflow ────────────────────────────────────────────────

VACUUM_WORKFLOW = [
    ("1. Mandrel prep",    "Temizle + sökme macunu (Frekote 700-NC × 3 kat) + PE film"),
    ("2. Fiber prep",      "Bobini kontrol et, fiber kopma yok, nem < 60% RH"),
    ("3. Resin prep",      f"EPON828: tartı={100:.0f}g + Ancamine: {RESIN.mix_ratio_phr:.0f}g (±0.5g)"),
    ("4. Degassing",       "Karıştır 3dk → vakum fırın -0.9bar 5dk → köpük giderildi"),
    ("5. Resin bath",      f"{RESIN.process_temp_C:.0f}°C'ye ısıt (reçine vizkozite ~2000cP)"),
    ("6. Winding start",   "T=0: sarım başlat, pot life sayacı başlat"),
    ("7. Layer check",     "Her 2 katman: fiber izleri kontrol, boşluk/örtüşme yok"),
    ("8. Peel ply",        "Son katman + peel ply (breather fabric)"),
    ("9. Vacuum bag",      "Nylon bag + vakum bağlantısı (-0.85 bar → P_comp≈86kPa)"),
    ("10. Cure oven",      "Vakum altında fırına gir → cure cycle başlat"),
    ("11. Post-cure",      "150°C×1h → fırın soğutma < 2°C/dk"),
    ("12. Demolding",      "> 60°C altına soğu → torku mandrel çıkar"),
    ("13. QC inspection",  "Ultrasonik C-tarama, void haritalama, boyut kontrol"),
]


# ── Process SOP (adım adım) ──────────────────────────────────────

PRODUCTION_SOP = """
══════════════════════════════════════════════════════
ÜRETIM SOP — FW-001 İlk Karbon Parça
Revizyon: 1.0  Tarih: FAZ15
══════════════════════════════════════════════════════
ÖNCE YAPILACAKLAR:
□ Güvenlik gözlüğü + nitril eldiven (epoksi skin sensitizer)
□ Reçine pot ömrü sayacını başlat
□ Fiber gerilmesini load cell ile kalibre et
□ ESP32 bağlantısını kontrol et (green LED = OK)
□ E-stop düğmesini test et
□ Encoder okumalarını kontrol et

BAŞLANGIÇ PROSEDÜRÜ:
1. Makineyi enerji ver (MCB → RCD → PSU sırası)
2. FluidNC başlar → WebUI'ye bağlan: http://192.168.1.100
3. $H → X ekseni home
4. G91 G1 A360 F3000 → spindle test (CW)
5. Tension: tare load cell ($TARE), 15N set et
6. Dry-run: winding G-code dosyasını yükle, M30 olmadan çalıştır
7. Görsel kontrol: fiber yolu, dome geçişleri
8. Reçine hazırla → reçine banyosunu doldur
9. SARIM BAŞLAT → watchdog.heartbeat() her 100ms
10. Tamamlandı → M30 → Vacuum bag → Kür fırını

ACİL DURUM:
• E-stop düğmesi → tüm eksenler durur, STO aktif
• python: mcu.emergency_stop() → aynı etki
• Reçine pot ömrü aşıldı → DUR, reçineyi değiştir
• Fiber koptu → DUR, konumu kaydet, fiber değiştir

KAPATMA:
1. Tüm eksenler X=0, A=0
2. Motor enable OFF
3. PSU kapatma sırası: 5V → 24V → 48V
4. Günlük log yaz (çevrim sayısı, hata, gerilme log)
══════════════════════════════════════════════════════
"""
