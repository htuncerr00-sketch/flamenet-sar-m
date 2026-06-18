"""
app/renderers/machine_renderer.py — Makine kinematik renderer (S4.4)
=====================================================================
MachineRenderer: taşıyıcı X konumu (carriage_x_mm) ve iş mili açısı
(spindle_angle_deg) animasyonunu yönetir.

Değişiklikler (S4.4):
  S4.4.1 — PyQt5 bağımlılığı kaldırıldı; item.translate() doğrudan kullanılıyor.
  S4.4.2 — Mandrel rotasyonu eklendi: setup() mandrel_items alıyor,
            update() mutlak açı ile resetTransform() + rotate() yapıyor.
  S4.4.3 — reset(center_x_mm, center_a_deg) eklendi; _stop_anim() sonrası
            clear_simulation_state() ile iç state senkronizasyonu için.

Statik makine çerçevesi (raylar, headstock, tailstock) _build_static_frame()
tarafından kurulur ve bu renderer tarafından dokunulmaz.
"""
from __future__ import annotations

import numpy as np


class MachineRenderer:
    """
    Taşıyıcı X konumu ve mandrel açısını RenderFrame'den günceller.

    Kullanım
    --------
    >>> r = MachineRenderer()
    >>> r.setup(view, topology,
    ...         carriage_items=gl_view.carriage_items,
    ...         mandrel_items=gl_view.mandrel_items,
    ...         L_m=gl_view.L_m)
    >>> r.update(frame)
    >>> r.teardown()
    """

    def __init__(self) -> None:
        self._carriage_items: list = []
        self._mandrel_items:  list = []
        self._view = None
        self._ready = False
        self._last_x_mm: float  = 0.0
        self._last_a_deg: float = 0.0
        self._L_m: float = 0.3

    # ── Yaşam döngüsü ────────────────────────────────────────────────────────

    def setup(
        self,
        view,
        topology,
        carriage_items: list | None = None,
        mandrel_items:  list | None = None,
        L_m: float = 0.3,
    ) -> None:
        """
        GL öğelerini kaydet ve mevcut sahne konumuna senkronize et.

        carriage_items : dinamik taşıyıcı GLMeshItem listesi
        mandrel_items  : dönen mandrel GLMeshItem/GLLinePlotItem listesi
        L_m            : mandrel uzunluğu (metre) — taşıyıcı sınır clamp için
        """
        self._carriage_items = list(carriage_items) if carriage_items else []
        self._mandrel_items  = list(mandrel_items)  if mandrel_items  else []
        self._view = view
        self._L_m  = float(L_m)

        # Mevcut GL konumuna senkronize ol — desenkronizasyonu önler.
        # _MachineGLView._carriage_x_m → mm; _anim_mandrel_angle → deg
        try:
            self._last_x_mm  = float(getattr(view, '_carriage_x_m', 0.0)) * 1000.0
            self._last_a_deg = float(getattr(view, '_anim_mandrel_angle', 0.0))
        except Exception:
            self._last_x_mm  = 0.0
            self._last_a_deg = 0.0

        self._ready = True

    def teardown(self) -> None:
        """Referansları bırak. GL nesneleri sahnenin sorumluluğundadır."""
        self._carriage_items = []
        self._mandrel_items  = []
        self._ready = False

    def reset(self, center_x_mm: float = 0.0, center_a_deg: float = 0.0) -> None:
        """
        clear_simulation_state() sonrası iç durumu senkronize et.

        GL nesnelerini taşımaz — _MachineGLView.clear_simulation_state()
        zaten fiziksel konumu sıfırlar. Burada yalnız iç sayaçlar güncellenir.
        """
        self._last_x_mm  = float(center_x_mm)
        self._last_a_deg = float(center_a_deg)

    # ── Güncelleme ───────────────────────────────────────────────────────────

    def update(self, frame) -> None:
        """
        RenderFrame'deki carriage_x_mm ve spindle_angle_deg ile sahneyi güncelle.

        Taşıyıcı: artımlı translate (deadband < 0.01 mm)
        Mandrel : mutlak resetTransform + rotate (deadband < 0.05°)
        """
        if not self._ready:
            return

        # ── Taşıyıcı X (artımlı translate) ──────────────────────────────────
        x_mm = float(frame.carriage_x_mm)
        if abs(x_mm - self._last_x_mm) >= 0.01:
            dx_m = float(np.clip(x_mm, 0.0, self._L_m * 1000.0) -
                         np.clip(self._last_x_mm, 0.0, self._L_m * 1000.0)) / 1000.0
            for item in self._carriage_items:
                try:
                    item.translate(dx_m, 0.0, 0.0)
                except Exception:
                    pass
            self._last_x_mm = x_mm

        # ── Mandrel açısı (mutlak transform) ─────────────────────────────────
        a_deg = float(frame.spindle_angle_deg)
        if abs(a_deg - self._last_a_deg) >= 0.05:
            for item in self._mandrel_items:
                try:
                    item.resetTransform()
                    item.rotate(a_deg, 1, 0, 0, local=False)
                except Exception:
                    pass
            self._last_a_deg = a_deg
