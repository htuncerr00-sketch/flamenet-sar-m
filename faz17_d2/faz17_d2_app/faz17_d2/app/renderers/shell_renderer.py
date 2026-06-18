"""
app/renderers/shell_renderer.py — Fiber kabuk renderer (S4.2.4)
================================================================
ShellRenderer: katman büyümesini temsil eden saydam silindirik kabuğu çizer.

Tasarım
-------
- setup(): GLMeshItem'ı bir kez oluşturur (topology'den faces gelir).
- update(frame): frame.shell_verts + frame.shell_colors varsa setMeshData çağırır.
- frame.shell_verts boş ya da sıfır-yarıçaplıysa: visible=False.
- Fizik hesabı YOK; tüm veri RenderFrame'den gelir.
"""
from __future__ import annotations

from typing import Optional

import numpy as np


class ShellRenderer:
    """
    Birikmiş fiber kabuğu renderer'ı.

    Kullanım
    --------
    >>> renderer = ShellRenderer()
    >>> renderer.setup(gl_view, topology)
    >>> renderer.update(frame)     # her animasyon karesinde
    >>> renderer.teardown()        # sahneyi temizle
    """

    def __init__(self) -> None:
        self._item = None          # pyqtgraph.opengl.GLMeshItem
        self._view = None
        self._ready = False

    # ── Yaşam döngüsü ────────────────────────────────────────────────────────

    def setup(self, view, topology) -> None:
        """
        GL öğesini oluştur ve sahneye ekle.

        view     : pyqtgraph.opengl.GLViewWidget (veya uyumlu)
        topology : RenderSceneTopology — shell_faces static yüzey indeksleri
        """
        try:
            import pyqtgraph.opengl as gl
        except ImportError:
            return

        from backend.core.render_frame import make_empty_frame
        frame0 = make_empty_frame(topology)

        self._item = gl.GLMeshItem(
            vertexes=frame0.shell_verts,
            faces=topology.shell_faces,
            vertexColors=frame0.shell_colors,
            smooth=True,
            drawEdges=False,
            glOptions="translucent",
        )
        self._item.setVisible(False)
        view.addItem(self._item)
        self._view = view
        self._ready = True

    def teardown(self) -> None:
        """GL öğesini sahneden kaldır."""
        if self._item is not None and self._view is not None:
            try:
                self._view.removeItem(self._item)
            except Exception:
                pass
        self._item = None
        self._ready = False

    def reset(self) -> None:
        """Animasyon sıfırlandığında kabuğu gizle ve başlangıç durumuna döndür."""
        if self._item is not None:
            try:
                self._item.setVisible(False)
            except Exception:
                pass

    # ── Güncelleme ───────────────────────────────────────────────────────────

    def update(self, frame) -> None:
        """
        Mevcut RenderFrame'e göre kabuğu güncelle.

        frame : RenderFrame — shell_verts, shell_colors, is_shell_visible
        """
        if not self._ready or self._item is None:
            return

        visible = bool(getattr(frame, 'is_shell_visible', False))
        self._item.setVisible(visible)
        if not visible:
            return

        verts  = frame.shell_verts
        colors = frame.shell_colors
        if verts.shape[0] == 0:
            return

        self._item.setMeshData(
            vertexes=verts,
            vertexColors=colors,
        )
