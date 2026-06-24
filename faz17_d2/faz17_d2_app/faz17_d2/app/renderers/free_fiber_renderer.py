"""
app/renderers/free_fiber_renderer.py — Serbest fiber strand renderer (S6.14.1)
===============================================================================
FreeFiberRenderer: payout eye ile mandrel temas noktası arasındaki canlı fiber
şeridini GLLinePlotItem olarak çizer (2 nokta, her karede güncellenir).

PayoutEyeRenderer'dan farkı: katman rengine göre renk değişir; daha kalın.
"""
from __future__ import annotations

import numpy as np

try:
    from . import LAYER_COLORS
except ImportError:
    LAYER_COLORS = [
        (0.15, 0.85, 0.15, 1.0), (1.00, 0.90, 0.10, 1.0),
        (1.00, 0.50, 0.10, 1.0), (0.90, 0.10, 0.10, 1.0),
    ]


class FreeFiberRenderer:
    """
    Eye→Contact canlı fiber strand renderer'ı.

    Kullanım
    --------
    >>> renderer = FreeFiberRenderer()
    >>> renderer.setup(gl_view, topology)
    >>> renderer.update(frame)
    """

    def __init__(self) -> None:
        self._item = None
        self._view = None
        self._ready = False

    # ── Yaşam döngüsü ────────────────────────────────────────────────────────

    def setup(self, view, topology) -> None:
        """GLLinePlotItem (2-nokta çizgi) oluştur ve sahneye ekle."""
        try:
            import pyqtgraph.opengl as gl
        except ImportError:
            return

        self._item = gl.GLLinePlotItem(
            pos=np.zeros((2, 3), dtype=np.float32),
            color=LAYER_COLORS[0],
            width=3.5,
            antialias=True,
            mode="line_strip",
        )
        self._item.setVisible(False)
        view.addItem(self._item)
        self._view = view
        self._ready = True

    def teardown(self) -> None:
        if self._item is not None and self._view is not None:
            try:
                self._view.removeItem(self._item)
            except Exception:
                pass
        self._item = None
        self._ready = False

    def reset(self, **kwargs) -> None:
        """Animasyon sıfırlandığında fiber strand'ı gizle."""
        if self._item is not None:
            try:
                self._item.setVisible(False)
            except Exception:
                pass

    # ── Güncelleme ───────────────────────────────────────────────────────────

    def update(self, frame) -> None:
        """eye_xyz → contact_xyz strand'ını katman renginde çiz."""
        if not self._ready or self._item is None:
            return

        pts = np.array(
            [frame.eye_xyz, frame.contact_xyz],
            dtype=np.float32,
        )
        layer_color = LAYER_COLORS[min(int(frame.layer), len(LAYER_COLORS) - 1)]
        self._item.setData(pos=pts, color=layer_color)
        self._item.setVisible(True)
