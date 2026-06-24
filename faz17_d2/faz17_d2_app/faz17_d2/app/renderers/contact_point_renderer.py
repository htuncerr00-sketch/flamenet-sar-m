"""
app/renderers/contact_point_renderer.py — Mandrel temas noktası marker renderer (S6.14.1)
==========================================================================================
ContactPointRenderer: fiber-mandrel temas noktasında küçük parlak scatter marker gösterir.

Tasarım
-------
- setup(): GLScatterPlotItem, başlangıçta gizli.
- update(frame): contact_xyz'e konumlandır, frame.layer rengini uygula.
- reset(**kwargs): gizle.
- Fizik hesabı YOK.
"""
from __future__ import annotations

import numpy as np

from . import LAYER_COLORS


class ContactPointRenderer:
    """
    Mandrel temas noktası GLScatterPlotItem renderer'ı.

    Kullanım
    --------
    >>> renderer = ContactPointRenderer()
    >>> renderer.setup(gl_view, topology)
    >>> renderer.update(frame)
    """

    def __init__(self) -> None:
        self._item = None
        self._view = None
        self._ready = False

    # ── Yaşam döngüsü ────────────────────────────────────────────────────────

    def setup(self, view, topology) -> None:
        """GLScatterPlotItem oluştur ve sahneye ekle."""
        try:
            import pyqtgraph.opengl as gl
        except ImportError:
            return

        self._item = gl.GLScatterPlotItem(
            pos=np.zeros((1, 3), dtype=np.float32),
            size=10.0,
            color=LAYER_COLORS[0],
            pxMode=True,
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
        """Animasyon sıfırlandığında temas noktası marker'ı gizle."""
        if self._item is not None:
            try:
                self._item.setVisible(False)
            except Exception:
                pass

    # ── Güncelleme ───────────────────────────────────────────────────────────

    def update(self, frame) -> None:
        """contact_xyz'e konumlandır; katman rengini uygula."""
        if not self._ready or self._item is None:
            return

        pos = np.array([frame.contact_xyz], dtype=np.float32)  # (1, 3) writable
        layer_color = LAYER_COLORS[min(int(frame.layer), len(LAYER_COLORS) - 1)]
        self._item.setData(pos=pos, color=layer_color)
        self._item.setVisible(True)
