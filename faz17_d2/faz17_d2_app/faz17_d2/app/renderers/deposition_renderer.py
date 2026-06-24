"""
app/renderers/deposition_renderer.py — Birikimli fiber deposition renderer (S6.14.2)
=====================================================================================
DepositionRenderer: mandrel yüzeyine yatırılmış fiberin katman katman birikimini
GLMeshItem olarak gösterir.

Tasarım
-------
- Tüm ribbon trajektoryası RenderFrameBuilder._dep_verts/faces/colors'dan gelir.
- setup(): boş GLMeshItem oluşturur.
- load_data(verts, faces, colors): pre-computed dizileri saklar.
- update(frame): frame.frame_idx ile faces[0:2*k] dilimini setMeshData'ya verir.
  DEP_PERIOD karede bir güncelleme (GPU yük tasarrufu).
- Fizik hesabı YOK.
"""
from __future__ import annotations

import numpy as np


DEP_PERIOD = 5   # Her kaç karede bir setMeshData çağrılır


class DepositionRenderer:
    """
    Birikimli fiber deposition mesh renderer'ı.

    Kullanım
    --------
    >>> renderer = DepositionRenderer()
    >>> renderer.setup(gl_view, topology)
    >>> renderer.load_data(dep_verts, dep_faces, dep_colors)
    >>> renderer.update(frame)  # her animasyon karesinde
    """

    def __init__(self) -> None:
        self._item = None
        self._view = None
        self._ready = False
        self._has_data = False

        self._dep_verts: np.ndarray | None = None
        self._dep_faces: np.ndarray | None = None
        self._dep_colors: np.ndarray | None = None
        self._last_k = -1

    # ── Yaşam döngüsü ────────────────────────────────────────────────────────

    def setup(self, view, topology) -> None:
        """Boş GLMeshItem oluştur ve sahneye ekle."""
        try:
            import pyqtgraph.opengl as gl
        except ImportError:
            return

        init_verts = np.zeros((4, 3), dtype=np.float32)
        init_faces = np.zeros((2, 3), dtype=np.int32)

        self._item = gl.GLMeshItem(
            vertexes=init_verts,
            faces=init_faces,
            smooth=False,
            drawEdges=False,
            glOptions="opaque",
        )
        self._item.setVisible(False)
        view.addItem(self._item)
        self._view = view
        self._ready = True

    def load_data(
        self,
        dep_verts: np.ndarray,   # (2N, 3) float32
        dep_faces: np.ndarray,   # (2*(N-1), 3) int32
        dep_colors: np.ndarray,  # (2N, 4) float32
    ) -> None:
        """
        Pre-computed deposition mesh dizilerini yükle.

        RenderFrameBuilder._dep_verts/faces/colors ile çağrılır.
        load_data() çağrılmadan update() erken çıkar.
        """
        if dep_verts is None or dep_faces is None or dep_colors is None:
            return
        self._dep_verts  = np.ascontiguousarray(dep_verts,  dtype=np.float32)
        self._dep_faces  = np.ascontiguousarray(dep_faces,  dtype=np.int32)
        self._dep_colors = np.ascontiguousarray(dep_colors, dtype=np.float32)
        self._last_k = -1   # zorla yenile
        self._has_data = True

    def teardown(self) -> None:
        if self._item is not None and self._view is not None:
            try:
                self._view.removeItem(self._item)
            except Exception:
                pass
        self._item = None
        self._ready = False
        self._has_data = False

    def reset(self, **kwargs) -> None:
        """Animasyon sıfırlandığında deposition mesh'i gizle ve sayacı sıfırla."""
        self._last_k = -1
        if self._item is not None:
            try:
                self._item.setVisible(False)
            except Exception:
                pass

    # ── Güncelleme ───────────────────────────────────────────────────────────

    def update(self, frame) -> None:
        """
        frame.frame_idx'e kadar olan fiber birikimini mesh olarak göster.

        DEP_PERIOD karede bir setMeshData çağrılır; arası atlanır.
        """
        if not self._ready or self._item is None or not self._has_data:
            return

        k = int(frame.frame_idx)

        # DEP_PERIOD dirty flag
        if k == self._last_k:
            return
        if k != 0 and (k % DEP_PERIOD) != 0 and k != (len(self._dep_faces) // 2):
            return

        self._last_k = k

        n_faces = max(0, 2 * k)   # k state → k tamamlanmış quad = 2k üçgen
        if n_faces == 0:
            self._item.setVisible(False)
            return

        faces_slice = self._dep_faces[:n_faces]
        self._item.setMeshData(
            vertexes=self._dep_verts,
            faces=faces_slice,
            vertexColors=self._dep_colors,
        )
        self._item.setVisible(True)
