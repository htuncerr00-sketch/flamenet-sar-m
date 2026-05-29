"""
core/machine_calibration.py — Makine Kalibrasyon Sistemi
=========================================================
Gerçek filament sarma makinesinin eksen başına fiziksel kalibrasyonunu
ve makine profillerini tanımlar. Tüm makine-yürütme katmanı (kinematik,
dinamik, besleme düzeltme, rotary senkronizasyon) bu kalibrasyondan beslenir.

Eksen modeli (4 eksen)
----------------------
    X  : taşıyıcı (carriage)   — eksenel, mm
    A  : iş mili (spindle)      — döner, derece (kümülatif)
    Y  : payout göz radyal      — standoff kontrolü, mm
    B  : payout göz yönelimi    — steering açısı, derece

Her eksen için: ölçek (steps/unit), enkoder çözünürlüğü, backlash,
takip hatası limiti, ölçek kalibrasyonu (scale_factor) ve sıfır ofseti.
Adım kuantizasyonu ve enkoder kuantizasyonu burada modellenir — gerçek
makinede pozisyon sürekli değil ayrıktır.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Optional

import numpy as np


# ── Eksen kalibrasyonu ─────────────────────────────────────────────────────────

@dataclass
class AxisCalibration:
    """
    Tek bir eksenin fiziksel kalibrasyonu.

    steps_per_unit          : Sürücü adımı / birim (mm veya derece).
    encoder_counts_per_unit : Enkoder sayımı / birim — geri besleme çözünürlüğü.
    backlash_units          : Yön değişiminde kaybedilen boşluk (birim).
    max_following_error     : İzin verilen maks takip hatası (birim).
    scale_factor            : Ölçek kalibrasyonu (komuta edilen / gerçek oran).
    zero_offset             : Makine sıfırı ofseti (birim).
    """
    steps_per_unit: float
    encoder_counts_per_unit: float
    backlash_units: float = 0.0
    max_following_error: float = 0.05
    scale_factor: float = 1.0
    zero_offset: float = 0.0

    def __post_init__(self) -> None:
        if self.steps_per_unit <= 0:
            raise ValueError(f"steps_per_unit > 0 olmalı: {self.steps_per_unit}")
        if self.encoder_counts_per_unit <= 0:
            raise ValueError(f"encoder_counts_per_unit > 0 olmalı: {self.encoder_counts_per_unit}")
        if self.backlash_units < 0:
            raise ValueError(f"backlash_units >= 0 olmalı: {self.backlash_units}")
        if self.scale_factor <= 0:
            raise ValueError(f"scale_factor > 0 olmalı: {self.scale_factor}")

    # ── Çözünürlük ─────────────────────────────────────────────────────────
    @property
    def step_resolution(self) -> float:
        """Tek adımın birim karşılığı (en küçük komut artışı)."""
        return 1.0 / self.steps_per_unit

    @property
    def encoder_resolution(self) -> float:
        """Tek enkoder sayımının birim karşılığı (en küçük ölçülebilir hareket)."""
        return 1.0 / self.encoder_counts_per_unit

    # ── Kuantizasyon ───────────────────────────────────────────────────────
    def quantize_command(self, value_units: float) -> float:
        """
        Komut değerini en yakın sürücü adımına yuvarla (ölçek + ofset uygulanmış).

        Gerçek makine yalnızca tamsayı adım komutları kabul eder; bu yüzden
        komut pozisyonu daima step_resolution katlarına kuantize olur.
        """
        scaled = (value_units - self.zero_offset) * self.scale_factor
        steps = round(scaled * self.steps_per_unit)
        return steps / self.steps_per_unit / self.scale_factor + self.zero_offset

    def quantize_encoder(self, value_units: float) -> float:
        """Geri besleme değerini en yakın enkoder sayımına yuvarla."""
        counts = round(value_units * self.encoder_counts_per_unit)
        return counts / self.encoder_counts_per_unit

    def command_steps(self, value_units: float) -> int:
        """Bir pozisyon için tamsayı adım sayısı (ölçek + ofset dahil)."""
        scaled = (value_units - self.zero_offset) * self.scale_factor
        return int(round(scaled * self.steps_per_unit))

    # ── Backlash ───────────────────────────────────────────────────────────
    def apply_backlash(self, target_units: float, direction: float,
                        prev_direction: float) -> float:
        """
        Yön değişiminde backlash etkisini uygula.

        Eksen yön değiştirdiğinde, dişli boşluğu kapanana kadar gerçek hareket
        target'ın gerisinde kalır. Aynı yönde devam ediliyorsa backlash yoktur.

        direction / prev_direction : +1 / -1 / 0 (hareket yönü işareti).
        Döner: backlash kayması uygulanmış gerçek pozisyon.
        """
        if self.backlash_units <= 0 or direction == 0 or prev_direction == 0:
            return target_units
        if direction != prev_direction:
            # Yön döndü → boşluk kadar geri kal (komut yönünün tersine)
            return target_units - direction * self.backlash_units
        return target_units


# ── Makine kalibrasyonu (4 eksen + montaj geometrisi) ─────────────────────────

@dataclass
class MachineCalibration:
    """
    Tam 4-eksen makine kalibrasyonu + montaj geometrisi.

    carriage / spindle / eye_radial / eye_orient : eksen kalibrasyonları.
    spindle_gear_ratio : motor:iş mili dişli oranı (motor turu / iş mili turu).
    eye_offset_z_mm    : göz montaj eksenel ofseti (taşıyıcı sıfırına göre).
    eye_base_standoff_mm : göz radyal taban mesafesi (Y=0'da yüzeye uzaklık).
    eye_pivot_offset_mm  : göz steering pivotu ile fiber çıkışı arası kol.
    mandrel_zero_z_mm  : mandrel eksenel datumu (makine sıfırına göre).
    mandrel_zero_r_mm  : mandrel radyal datumu (eksen kaçıklığı düzeltmesi).
    name               : makine profili adı.
    """
    carriage: AxisCalibration
    spindle: AxisCalibration
    eye_radial: AxisCalibration
    eye_orient: AxisCalibration

    spindle_gear_ratio: float = 1.0
    eye_offset_z_mm: float = 0.0
    eye_base_standoff_mm: float = 150.0
    eye_pivot_offset_mm: float = 30.0
    mandrel_zero_z_mm: float = 0.0
    mandrel_zero_r_mm: float = 0.0
    name: str = "default"

    def __post_init__(self) -> None:
        if self.spindle_gear_ratio <= 0:
            raise ValueError(f"spindle_gear_ratio > 0 olmalı: {self.spindle_gear_ratio}")
        if self.eye_base_standoff_mm <= 0:
            raise ValueError(f"eye_base_standoff_mm > 0 olmalı: {self.eye_base_standoff_mm}")

    # ── Mandrel datum dönüşümü ─────────────────────────────────────────────
    def mandrel_to_machine_z(self, z_mandrel_mm: float) -> float:
        """Mandrel eksenel koordinatını makine taşıyıcı koordinatına çevir."""
        return z_mandrel_mm + self.mandrel_zero_z_mm

    def machine_to_mandrel_z(self, z_machine_mm: float) -> float:
        """Makine taşıyıcı koordinatını mandrel eksenel koordinatına çevir."""
        return z_machine_mm - self.mandrel_zero_z_mm

    # ── İş mili dönüşümü ───────────────────────────────────────────────────
    def spindle_motor_deg(self, spindle_deg: float) -> float:
        """İş mili açısından motor açısına (dişli oranı ile)."""
        return spindle_deg * self.spindle_gear_ratio

    def motor_to_spindle_deg(self, motor_deg: float) -> float:
        """Motor açısından iş mili açısına."""
        return motor_deg / self.spindle_gear_ratio

    def summary(self) -> str:
        return (
            f"MakineKalibrasyon '{self.name}': "
            f"X={self.carriage.steps_per_unit:.0f}adım/mm "
            f"(enk={self.carriage.encoder_counts_per_unit:.0f}/mm, "
            f"backlash={self.carriage.backlash_units:.3f}mm) | "
            f"A={self.spindle.steps_per_unit:.0f}adım/° "
            f"(dişli={self.spindle_gear_ratio:.2f}) | "
            f"standoff={self.eye_base_standoff_mm:.0f}mm | "
            f"mandrel_zero=({self.mandrel_zero_z_mm:.1f},{self.mandrel_zero_r_mm:.1f})mm"
        )


# ── Hazır makine profilleri ───────────────────────────────────────────────────

def default_calibration() -> MachineCalibration:
    """
    Tipik bir 4-eksen filament sarma makinesi için varsayılan kalibrasyon.

    Değerler endüstriyel orta-sınıf bir makineyi temsil eder:
    - X: 1.8° step motor, 5 mm/tur vida, 1/16 mikroadım → 640 adım/mm
    - A: NEMA34 + 5:1 redüktör, 1/8 mikroadım → 200·8·5/360 ≈ 22.2 adım/°
    - Enkoderler: X 1 µm, A 0.01° (10000 sayım/tur)
    """
    return MachineCalibration(
        carriage=AxisCalibration(
            steps_per_unit=640.0, encoder_counts_per_unit=1000.0,
            backlash_units=0.02, max_following_error=0.10,
        ),
        spindle=AxisCalibration(
            steps_per_unit=22.22, encoder_counts_per_unit=27.78,
            backlash_units=0.05, max_following_error=0.50,
        ),
        eye_radial=AxisCalibration(
            steps_per_unit=320.0, encoder_counts_per_unit=500.0,
            backlash_units=0.03, max_following_error=0.15,
        ),
        eye_orient=AxisCalibration(
            steps_per_unit=44.44, encoder_counts_per_unit=55.56,
            backlash_units=0.10, max_following_error=0.50,
        ),
        spindle_gear_ratio=5.0,
        eye_base_standoff_mm=150.0,
        eye_pivot_offset_mm=30.0,
        name="industrial_4axis_default",
    )


def high_precision_calibration() -> MachineCalibration:
    """
    Yüksek hassasiyetli makine (servo + lineer enkoder, düşük backlash).
    Ar-Ge / hassas tüp sarma için.
    """
    return MachineCalibration(
        carriage=AxisCalibration(
            steps_per_unit=2000.0, encoder_counts_per_unit=20000.0,
            backlash_units=0.002, max_following_error=0.02,
        ),
        spindle=AxisCalibration(
            steps_per_unit=100.0, encoder_counts_per_unit=1000.0,
            backlash_units=0.005, max_following_error=0.05,
        ),
        eye_radial=AxisCalibration(
            steps_per_unit=1000.0, encoder_counts_per_unit=5000.0,
            backlash_units=0.005, max_following_error=0.03,
        ),
        eye_orient=AxisCalibration(
            steps_per_unit=200.0, encoder_counts_per_unit=2000.0,
            backlash_units=0.02, max_following_error=0.10,
        ),
        spindle_gear_ratio=1.0,
        eye_base_standoff_mm=120.0,
        eye_pivot_offset_mm=25.0,
        name="high_precision_servo",
    )


_PROFILES: Dict[str, "callable"] = {
    "industrial_4axis_default": default_calibration,
    "high_precision_servo": high_precision_calibration,
}


def get_machine_profile(name: str) -> MachineCalibration:
    """Adıyla hazır makine profili döndür."""
    if name not in _PROFILES:
        raise KeyError(
            f"Bilinmeyen makine profili '{name}'. "
            f"Mevcut: {list(_PROFILES.keys())}"
        )
    return _PROFILES[name]()


def available_profiles() -> list:
    """Mevcut makine profili adları."""
    return list(_PROFILES.keys())
