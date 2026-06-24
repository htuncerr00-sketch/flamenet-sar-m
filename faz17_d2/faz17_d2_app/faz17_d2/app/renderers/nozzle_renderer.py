"""
app/renderers/nozzle_renderer.py — Payout nozul marker renderer (S6.14.1)
==========================================================================
NozzleRenderer: payout eye konumunda küçük çapraz (crosshair) marker gösterir.

Tasarım
-------
- setup(): 3 çizgiden oluşan GLLinePlotItem (X/Y/Z kesişimi, statik başlangıç).
- update(frame): eye_xyz'e taşı, frame.layer'a göre LAYER_COLORS kullan.
- reset(**kwargs): gizle.
- Fizik hesabı YOK.
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


class NozzleRenderer:
    """
    Payout nozul/eye konumu crosshair marker renderer'ı.

    Kullanım
    --------
    >>> renderer = NozzleRenderer()
    >>> renderer.setup(gl_view, topology)
    >>> renderer.update(frame)
    >>> renderer.set_visible(False)
    """

    _SIZE_M = 0.010   # crosshair kol uzunluğu (10 mm = 0.01 m)

    def __init__(self) -> None:
        self._item = None
        self._view = None
        self._ready = False
        self._visible = True

    # ── Yaşam döngüsü ────────────────────────────────────────────────────────

    def setup(self, view, topology) -> None:
        """Nozul crosshair GLLinePlotItem'ı oluştur ve sahneye ekle."""
        try:
            import pyqtgraph.opengl as gl
        except ImportError:
            return

        # 3 eksen: X, Y, Z — 6 nokta (3 ayrı çizgi, mode='lines' ile)
        s = self._SIZE_M
        init_pos = np.array([
            [-s, 0, 0], [s, 0, 0],
            [0, -s, 0], [0, s, 0],
            [0, 0, -s], [0, 0, s],
        ], dtype=np.float32)

        self._item = gl.GLLinePlotItem(
            pos=init_pos,
            color=(1.0, 1.0, 1.0, 0.9),
            width=2.5,
            antialias=True,
            mode="lines",
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
        """Animasyon sıfırlandığında nozul marker'ı gizle."""
        if self._item is not None:
            try:
                self._item.setVisible(False)
            except Exception:
                pass

    # ── Güncelleme ───────────────────────────────────────────────────────────

    def update(self, frame) -> None:
        """eye_xyz'e taşı; katman rengini uygula."""
        if not self._ready or self._item is None:
            return

        eye = np.array(frame.eye_xyz, dtype=np.float32)   # (3,) writable kopya
        s = self._SIZE_M
        pos = np.array([
            eye + [-s, 0, 0], eye + [s, 0, 0],
            eye + [0, -s, 0], eye + [0, s, 0],
            eye + [0, 0, -s], eye + [0, 0, s],
        ], dtype=np.float32)

        layer_color = LAYER_COLORS[min(int(frame.layer), len(LAYER_COLORS) - 1)]
        self._item.setData(pos=pos, color=layer_color)
        self._item.setVisible(True)

    # ── Görünürlük ───────────────────────────────────────────────────────────

    def set_visible(self, visible: bool) -> None:
        self._visible = visible
        if self._item is not None:
            self._item.setVisible(visible)
