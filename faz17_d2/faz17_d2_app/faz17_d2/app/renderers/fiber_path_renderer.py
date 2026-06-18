"""
app/renderers/fiber_path_renderer.py — Fiber yolu iz çizgisi renderer (S4.2.6+)
================================================================================
FiberPathRenderer: contact_xyz noktalarının kümülatif izini GLLinePlotItem
olarak çizer (temas merkez çizgisi, ribbon'dan farklı).
"""
from __future__ import annotations

import numpy as np


class FiberPathRenderer:
    """
    Fiber yolu merkez çizgisi renderer'ı.

    Ribbon'dan farkı: mesh değil çizgi; LOD bağımsız; referans amaçlı.
    """

    def __init__(self) -> None:
        self._item = None
        self._view = None
        self._ready = False
        self._pts: list = []

    def setup(self, view, topology) -> None:
        try:
            import pyqtgraph.opengl as gl
        except ImportError:
            return
        self._item = gl.GLLinePlotItem(
            pos=np.zeros((2, 3), dtype=np.float32),
            color=(0.95, 0.78, 0.12, 0.6),
            width=1.5,
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
        self._pts = []

    def reset(self) -> None:
        """Yeni animasyon başında iz geçmişini temizle."""
        self._pts = []
        if self._item is not None:
            self._item.setVisible(False)

    def update(self, frame) -> None:
        """contact_xyz'i birikimli iz listesine ekle ve GLLinePlotItem güncelle."""
        if not self._ready or self._item is None:
            return
        pt = frame.contact_xyz.copy()
        self._pts.append(pt)
        if len(self._pts) < 2:
            return
        pts_arr = np.array(self._pts, dtype=np.float32)
        self._item.setVisible(True)
        self._item.setData(pos=pts_arr)
