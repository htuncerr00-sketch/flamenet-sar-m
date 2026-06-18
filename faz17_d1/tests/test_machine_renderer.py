"""
tests/test_machine_renderer.py — S4.4 MachineRenderer birim testleri
=====================================================================
PyQt5 bağımlılığı yok; GL nesneleri mock ile simüle edilir.

Çalıştır:
    cd faz17_d1/faz17_d1_backend
    python -m pytest ../tests/test_machine_renderer.py -v
"""
from __future__ import annotations

import importlib.util
import math
import sys
import os
import time

import numpy as np
import pytest

# ─── Machine renderer'ı doğrudan yükle ───────────────────────────────────────
_RENDERER_PATH = os.path.join(
    os.path.dirname(__file__),
    "../../faz17_d2/faz17_d2_app/faz17_d2/app/renderers/machine_renderer.py",
)

spec = importlib.util.spec_from_file_location("machine_renderer", _RENDERER_PATH)
_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_mod)
MachineRenderer = _mod.MachineRenderer


# ─── Mock nesneler ───────────────────────────────────────────────────────────

class _GLItem:
    """Minimal GL öğesi mock."""
    def __init__(self):
        self.x = 0.0
        self.angle = 0.0
        self.translate_count = 0
        self.reset_count = 0
        self.rotate_count = 0

    def translate(self, dx, dy, dz):
        self.x += dx
        self.translate_count += 1

    def resetTransform(self):
        self.angle = 0.0
        self.reset_count += 1

    def rotate(self, a, ax, ay, az, local=False):
        self.angle = a
        self.rotate_count += 1


class _GLView:
    """_MachineGLView mock — property'ler dahil."""
    def __init__(self, L_m=0.3, carriage_x_m=0.15, mandrel_angle=0.0):
        self._L_m = L_m
        self._carriage_x_m = carriage_x_m
        self._anim_mandrel_angle = mandrel_angle
        self._carriage_items = [_GLItem()]
        self._mandrel_items  = [_GLItem(), _GLItem(), _GLItem()]

    @property
    def carriage_items(self):
        return self._carriage_items

    @property
    def mandrel_items(self):
        return self._mandrel_items

    @property
    def L_m(self):
        return self._L_m


class _Frame:
    """Minimal RenderFrame mock."""
    def __init__(self, x_mm=0.0, a_deg=0.0):
        self.carriage_x_mm = x_mm
        self.spindle_angle_deg = a_deg


# ─────────────────────────────────────────────────────────────────────────────
# MR-01: Import ve PyQt5 bağımlılığı yok
# ─────────────────────────────────────────────────────────────────────────────

class TestMachineRendererImport:

    def test_mr01_no_pyqt5_import(self):
        """MR-01: Modül import edildiğinde PyQt5 yüklenmemeli."""
        pyqt5_before = set(k for k in sys.modules if k.startswith('PyQt5'))
        # Yeniden import (zaten yüklü, ama kontrol için)
        spec2 = importlib.util.spec_from_file_location("mr2", _RENDERER_PATH)
        mod2 = importlib.util.module_from_spec(spec2)
        spec2.loader.exec_module(mod2)
        pyqt5_after = set(k for k in sys.modules if k.startswith('PyQt5'))
        new_pyqt5 = pyqt5_after - pyqt5_before
        assert not new_pyqt5, f"PyQt5 yüklendi: {new_pyqt5}"

    def test_mr02_instantiation(self):
        """MR-02: MachineRenderer() oluşturma başarılı."""
        r = MachineRenderer()
        assert not r._ready
        assert r._last_x_mm == 0.0
        assert r._last_a_deg == 0.0
        assert r._carriage_items == []
        assert r._mandrel_items == []


# ─────────────────────────────────────────────────────────────────────────────
# MR-03: setup() senkronizasyonu
# ─────────────────────────────────────────────────────────────────────────────

class TestMachineRendererSetup:

    def test_mr03_setup_syncs_carriage_from_view(self):
        """MR-03: setup() view._carriage_x_m'den _last_x_mm senkronize eder."""
        view = _GLView(L_m=0.4, carriage_x_m=0.25)  # 250 mm
        r = MachineRenderer()
        r.setup(view, None,
                carriage_items=view.carriage_items,
                mandrel_items=view.mandrel_items,
                L_m=view.L_m)
        assert r._ready
        assert abs(r._last_x_mm - 250.0) < 1e-6, f"beklenen 250.0, alınan {r._last_x_mm}"

    def test_mr04_setup_syncs_mandrel_angle_from_view(self):
        """MR-04: setup() view._anim_mandrel_angle'dan _last_a_deg senkronize eder."""
        view = _GLView(mandrel_angle=45.0)
        r = MachineRenderer()
        r.setup(view, None,
                carriage_items=view.carriage_items,
                mandrel_items=view.mandrel_items,
                L_m=view.L_m)
        assert abs(r._last_a_deg - 45.0) < 1e-6

    def test_mr05_setup_empty_items(self):
        """MR-05: Boş item listesiyle setup() hata vermez."""
        view = _GLView()
        r = MachineRenderer()
        r.setup(view, None, carriage_items=[], mandrel_items=[], L_m=0.3)
        assert r._ready

    def test_mr06_setup_none_items(self):
        """MR-06: None item listesiyle setup() hata vermez."""
        view = _GLView()
        r = MachineRenderer()
        r.setup(view, None, carriage_items=None, mandrel_items=None, L_m=0.3)
        assert r._ready
        assert r._carriage_items == []
        assert r._mandrel_items  == []


# ─────────────────────────────────────────────────────────────────────────────
# MR-07: Taşıyıcı hareketi
# ─────────────────────────────────────────────────────────────────────────────

class TestCarriageMotion:

    def _make(self, initial_x_mm=150.0):
        view = _GLView(carriage_x_m=initial_x_mm / 1000.0)
        r = MachineRenderer()
        r.setup(view, None,
                carriage_items=view.carriage_items,
                mandrel_items=view.mandrel_items,
                L_m=view.L_m)
        return r, view

    def test_mr07_carriage_translate_forward(self):
        """MR-07: Taşıyıcı ileri hareket — doğru dx."""
        r, view = self._make(150.0)
        carriage = view._carriage_items[0]
        r.update(_Frame(x_mm=200.0))
        assert carriage.translate_count == 1
        assert abs(carriage.x - 0.05) < 1e-6   # 50 mm = 0.05 m

    def test_mr08_carriage_translate_backward(self):
        """MR-08: Taşıyıcı geri hareket — negatif dx."""
        r, view = self._make(200.0)
        carriage = view._carriage_items[0]
        r.update(_Frame(x_mm=100.0))
        assert carriage.translate_count == 1
        assert abs(carriage.x - (-0.1)) < 1e-6

    def test_mr09_carriage_deadband(self):
        """MR-09: 0.01 mm altı hareket → translate çağrılmaz."""
        r, view = self._make(150.0)
        carriage = view._carriage_items[0]
        r.update(_Frame(x_mm=150.005))   # < 0.01 mm
        assert carriage.translate_count == 0

    def test_mr10_carriage_clamp(self):
        """MR-10: 0 ve L_m*1000 dışı değerler clamp edilir."""
        r, view = self._make(150.0)   # L_m=0.3 → max=300 mm
        carriage = view._carriage_items[0]
        r.update(_Frame(x_mm=500.0))  # 500 > 300 → clamp 300
        # 150→300 = 150 mm = 0.15 m
        assert abs(carriage.x - 0.15) < 1e-6

    def test_mr11_multiple_frames(self):
        """MR-11: Birden fazla frame — birikimli translate doğru."""
        r, view = self._make(0.0)
        carriage = view._carriage_items[0]
        r.update(_Frame(x_mm=100.0))   # +100 mm = +0.1 m
        r.update(_Frame(x_mm=200.0))   # +100 mm = +0.1 m
        r.update(_Frame(x_mm=200.5))   # +0.5 mm = +0.0005 m
        assert carriage.translate_count == 3
        assert abs(carriage.x - 0.2005) < 1e-6


# ─────────────────────────────────────────────────────────────────────────────
# MR-12: Mandrel rotasyonu
# ─────────────────────────────────────────────────────────────────────────────

class TestMandrelRotation:

    def _make(self, initial_a=0.0):
        view = _GLView(mandrel_angle=initial_a)
        r = MachineRenderer()
        r.setup(view, None,
                carriage_items=view.carriage_items,
                mandrel_items=view.mandrel_items,
                L_m=view.L_m)
        return r, view

    def test_mr12_mandrel_rotate_absolute(self):
        """MR-12: Mandrel resetTransform + rotate(a_deg) mutlak açı."""
        r, view = self._make(0.0)
        m0 = view._mandrel_items[0]
        r.update(_Frame(a_deg=90.0))
        assert m0.reset_count == 1
        assert m0.rotate_count == 1
        assert abs(m0.angle - 90.0) < 1e-6

    def test_mr13_mandrel_all_items_updated(self):
        """MR-13: Tüm mandrel öğeleri güncellenir."""
        r, view = self._make(0.0)
        r.update(_Frame(a_deg=45.0))
        for item in view._mandrel_items:
            assert item.reset_count == 1
            assert abs(item.angle - 45.0) < 1e-6

    def test_mr14_mandrel_deadband(self):
        """MR-14: 0.05° altı açı değişimi → rotate çağrılmaz."""
        r, view = self._make(90.0)
        m0 = view._mandrel_items[0]
        r.update(_Frame(a_deg=90.03))   # < 0.05°
        assert m0.reset_count == 0

    def test_mr15_mandrel_cumulative_absolute(self):
        """MR-15: Mutlak rotasyon — önceki değer resetTransform ile sıfırlanır."""
        r, view = self._make(0.0)
        m0 = view._mandrel_items[0]
        r.update(_Frame(a_deg=45.0))
        r.update(_Frame(a_deg=720.0))   # kümülatif açı
        assert m0.reset_count == 2
        assert abs(m0.angle - 720.0) < 1e-6   # son mutlak değer

    def test_mr16_mandrel_negative_angle(self):
        """MR-16: Negatif açı işlenir."""
        r, view = self._make(0.0)
        r.update(_Frame(a_deg=-90.0))
        assert abs(view._mandrel_items[0].angle - (-90.0)) < 1e-6


# ─────────────────────────────────────────────────────────────────────────────
# MR-17: reset() senkronizasyonu
# ─────────────────────────────────────────────────────────────────────────────

class TestResetSync:

    def test_mr17_reset_syncs_internal_state(self):
        """MR-17: reset() _last_x_mm ve _last_a_deg günceller."""
        view = _GLView(carriage_x_m=0.2)
        r = MachineRenderer()
        r.setup(view, None,
                carriage_items=view.carriage_items,
                mandrel_items=view.mandrel_items,
                L_m=0.3)
        r.update(_Frame(x_mm=250.0, a_deg=180.0))
        r.reset(center_x_mm=150.0, center_a_deg=0.0)
        assert abs(r._last_x_mm - 150.0) < 1e-6
        assert abs(r._last_a_deg - 0.0) < 1e-6

    def test_mr18_reset_after_stop_prevents_jump(self):
        """MR-18: reset sonrası frame → animasyon yeni konumdan başlar."""
        view = _GLView(carriage_x_m=0.15)
        r = MachineRenderer()
        r.setup(view, None,
                carriage_items=view.carriage_items,
                mandrel_items=view.mandrel_items,
                L_m=0.3)
        r.update(_Frame(x_mm=280.0))   # taşıyıcı 280 mm'e gitti
        carriage = view._carriage_items[0]
        translate_before = carriage.translate_count
        carriage.x = 0.0   # GL konumu sıfırla (clear_simulation_state simülasyonu)

        r.reset(center_x_mm=150.0)
        r.update(_Frame(x_mm=200.0))   # 150→200 = +50 mm
        assert abs(carriage.x - 0.05) < 1e-6


# ─────────────────────────────────────────────────────────────────────────────
# MR-19: Teardown
# ─────────────────────────────────────────────────────────────────────────────

class TestTeardown:

    def test_mr19_teardown_disables_ready(self):
        """MR-19: teardown() sonrası _ready=False."""
        view = _GLView()
        r = MachineRenderer()
        r.setup(view, None,
                carriage_items=view.carriage_items,
                mandrel_items=view.mandrel_items)
        r.teardown()
        assert not r._ready

    def test_mr20_teardown_clears_items(self):
        """MR-20: teardown() öğe listelerini boşaltır."""
        view = _GLView()
        r = MachineRenderer()
        r.setup(view, None,
                carriage_items=view.carriage_items,
                mandrel_items=view.mandrel_items)
        r.teardown()
        assert r._carriage_items == []
        assert r._mandrel_items  == []

    def test_mr21_update_after_teardown_silent(self):
        """MR-21: teardown sonrası update() exception vermez."""
        view = _GLView()
        r = MachineRenderer()
        r.setup(view, None,
                carriage_items=view.carriage_items,
                mandrel_items=view.mandrel_items)
        r.teardown()
        r.update(_Frame(x_mm=100.0, a_deg=45.0))   # sessiz dönmeli

    def test_mr22_double_teardown_safe(self):
        """MR-22: İkinci teardown() exception vermez (double-free yok)."""
        view = _GLView()
        r = MachineRenderer()
        r.setup(view, None,
                carriage_items=view.carriage_items,
                mandrel_items=view.mandrel_items)
        r.teardown()
        r.teardown()   # ikinci kez


# ─────────────────────────────────────────────────────────────────────────────
# MR-23: Benchmark
# ─────────────────────────────────────────────────────────────────────────────

class TestBenchmark:

    def test_mr23_update_speed(self):
        """MR-23: N=1000 update < 50 ms (carriage + mandrel birlikte)."""
        view = _GLView(carriage_x_m=0.0)
        r = MachineRenderer()
        r.setup(view, None,
                carriage_items=view.carriage_items,
                mandrel_items=view.mandrel_items,
                L_m=view.L_m)
        N = 1000
        t0 = time.perf_counter()
        for i in range(N):
            r.update(_Frame(x_mm=float(i % 300), a_deg=float(i * 1.2)))
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        avg_us = elapsed_ms / N * 1000.0
        print(f"\n[MR-23] MachineRenderer.update(): {avg_us:.2f} µs/kare (N={N})")
        assert elapsed_ms < 50.0, f"Çok yavaş: {elapsed_ms:.1f} ms"
