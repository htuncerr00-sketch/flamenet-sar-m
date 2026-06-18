"""
app/renderers/heatmap_renderer.py — Kaplama ısı haritası renderer (S4.2.5)
===========================================================================
HeatmapRenderer: mandrel yüzeyine kaplanan fiber yoğunluğunu renk
haritası olarak gösterir.

Tasarım
-------
- setup(): static topology (heatmap_verts, heatmap_faces) ile GLMeshItem.
- update(frame): frame.heatmap_dirty=True ise setMeshData; False ise atla.
- Dirty flag: RenderFrameBuilder'da HEATMAP_PERIOD=10 karede bir True → GPU
  yükü azaltılır.
- Fizik hesabı YOK.
"""
from __future__ import annotations


class HeatmapRenderer:
    """
    Kaplama ısı haritası renderer'ı.

    Kullanım
    --------
    >>> renderer = HeatmapRenderer()
    >>> renderer.setup(gl_view, topology)
    >>> renderer.update(frame)
    >>> renderer.set_visible(False)
    """

    def __init__(self) -> None:
        self._item = None
        self._view = None
        self._ready = False
        self._visible = True

    # ── Yaşam döngüsü ────────────────────────────────────────────────────────

    def setup(self, view, topology) -> None:
        """
        topology.heatmap_verts (static) + topology.heatmap_faces (static) ile
        GLMeshItem oluştur.
        """
        try:
            import pyqtgraph.opengl as gl
        except ImportError:
            return

        import numpy as np
        n_hm = topology.heatmap_verts.shape[0]
        init_colors = np.zeros((n_hm, 4), dtype=np.float32)
        init_colors[:, :] = [0.20, 0.40, 0.80, 0.70]   # başlangıç: mavi

        self._item = gl.GLMeshItem(
            vertexes=topology.heatmap_verts,
            faces=topology.heatmap_faces,
            vertexColors=init_colors,
            smooth=False,
            drawEdges=False,
            glOptions="translucent",
        )
        self._item.setVisible(self._visible)
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

    # ── Güncelleme ───────────────────────────────────────────────────────────

    def update(self, frame) -> None:
        """
        frame.heatmap_dirty=True ise vertex renkleri güncelle; False ise atla.

        Dirty flag'i atlayarak her karede setMeshData/glBufferData çağrısından
        kaçınılır (GPU yük tasarrufu).
        """
        if not self._ready or self._item is None:
            return
        if not bool(getattr(frame, 'heatmap_dirty', False)):
            return

        vc = frame.heatmap_vc
        if vc.shape[0] == 0:
            return

        self._item.setMeshData(vertexColors=vc)

    # ── Görünürlük ───────────────────────────────────────────────────────────

    def set_visible(self, visible: bool) -> None:
        self._visible = visible
        if self._item is not None:
            self._item.setVisible(visible)
