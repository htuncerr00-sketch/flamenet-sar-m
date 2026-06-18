"""
app/renderers/machine_renderer.py — Makine kinematik renderer (S4.2.6+)
========================================================================
MachineRenderer: taşıyıcı konumunu RenderFrame.carriage_x_mm ile günceller.

Statik makine çerçevesi (raylar, iş mili) zaten entegre_tasarim_paneli.py'deki
_build_machine_frame / _build_static_frame fonksiyonlarınca kurulur.
Bu renderer yalnızca dinamik taşıyıcı konumunu takip eder.
"""
from __future__ import annotations


class MachineRenderer:
    """
    Makine taşıyıcısının X konumunu günceller.

    Statik makine çerçevesini etkilemez; carriage_x_mm değişimlerini
    GLMeshItem dönüşümü olarak uygular.
    """

    def __init__(self) -> None:
        self._carriage_items: list = []
        self._view = None
        self._ready = False
        self._last_x_mm: float = 0.0
        self._L_m: float = 0.3

    def setup(self, view, topology, carriage_items: list, L_m: float = 0.3) -> None:
        """
        carriage_items: taşıyıcıyı temsil eden GL öğelerinin listesi.
        L_m: mandrel uzunluğu (metre) — sınır için.
        """
        self._carriage_items = carriage_items
        self._view = view
        self._L_m = L_m
        self._ready = True

    def teardown(self) -> None:
        self._carriage_items = []
        self._ready = False

    def update(self, frame) -> None:
        """carriage_x_mm değişince taşıyıcıyı kaydır."""
        if not self._ready:
            return
        x_mm = float(frame.carriage_x_mm)
        if abs(x_mm - self._last_x_mm) < 0.01:
            return
        dx_m = (x_mm - self._last_x_mm) / 1000.0
        from PyQt5.QtGui import QMatrix4x4
        m = QMatrix4x4()
        m.translate(dx_m, 0.0, 0.0)
        for item in self._carriage_items:
            try:
                item.applyTransform(m, local=False)
            except Exception:
                pass
        self._last_x_mm = x_mm
