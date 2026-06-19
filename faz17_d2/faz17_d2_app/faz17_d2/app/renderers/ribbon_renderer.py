"""
app/renderers/ribbon_renderer.py — Tow ribbon renderer, pre-alloc (S4.2.6)
===========================================================================
RibbonRenderer: fiziksel genişlikli fiber şeridini GL mesh olarak çizer.

Tasarım (pre-alloc setMeshData)
--------------------------------
- setup(): MAX_SEG boyutunda ön-tahsis edilmiş vertex/face tamponları oluşturur.
  GLMeshItem ilk tam tamponla kurulur.
- update(frame): frame.ribbon_verts/faces (k aktif segment, zaten RenderFrame'de)
  direkt olarak setMeshData'ya verilir.
- CustomRibbonMeshItem (glBufferSubData) ertelenmiş sprint; önce bu profillenecek.
- Fizik hesabı YOK.
"""
from __future__ import annotations

import numpy as np


class RibbonRenderer:
    """
    Tow ribbon mesh renderer'ı (pre-alloc trick).

    Kullanım
    --------
    >>> renderer = RibbonRenderer()
    >>> renderer.setup(gl_view, topology)
    >>> renderer.update(frame)
    """

    def __init__(self) -> None:
        self._item = None
        self._view = None
        self._ready = False
        self._max_seg = 0

    # ── Yaşam döngüsü ────────────────────────────────────────────────────────

    def setup(self, view, topology) -> None:
        """
        topology.ribbon_max_seg kapasite ile GLMeshItem kur.

        Başlangıçta boş ribbon (0 segment).
        """
        try:
            import pyqtgraph.opengl as gl
        except ImportError:
            return

        self._max_seg = topology.ribbon_max_seg

        empty_verts = np.zeros((max(2, 2 * self._max_seg), 3), dtype=np.float32)
        empty_faces = np.zeros((max(2, 2 * max(self._max_seg - 1, 1)), 3), dtype=np.int32)

        # S6.11.1: "additive" → "translucent" (çakışma aşırı parlaklığını önler)
        self._item = gl.GLMeshItem(
            vertexes=empty_verts,
            faces=empty_faces,
            color=(1.00, 0.82, 0.15, 0.90),
            smooth=False,
            drawEdges=False,
            glOptions="translucent",
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
        """Animasyon sıfırlandığında ribbon'u gizle ve görsel state'i başlangıca döndür.

        **kwargs: MachineRenderer uyumlu çağrı imzası için yoksayılır.
        """
        if self._item is not None:
            try:
                self._item.setVisible(False)
            except Exception:
                pass

    # ── Güncelleme ───────────────────────────────────────────────────────────

    def update(self, frame) -> None:
        """
        frame.ribbon_verts + frame.ribbon_faces ile setMeshData çağır.

        Ribbon boşsa (0 segment) öğeyi gizle.
        """
        if not self._ready or self._item is None:
            return

        verts = frame.ribbon_verts
        faces = frame.ribbon_faces
        n_seg = frame.ribbon_n_segments

        if n_seg < 2 or verts.shape[0] == 0:
            self._item.setVisible(False)
            return

        self._item.setVisible(True)
        self._item.setMeshData(vertexes=verts, faces=faces)
