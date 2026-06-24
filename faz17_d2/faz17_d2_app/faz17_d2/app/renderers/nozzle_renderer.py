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

try:
    from .fiber_geometry import derive_nozzle_point
except ImportError:
    def derive_nozzle_point(eye_xyz, contact_xyz,
                            standoff_factor=0.7, min_standoff_m=0.030,
                            lead_factor=0.45):
        c = np.asarray(contact_xyz, dtype=np.float64).reshape(3)
        e = np.asarray(eye_xyz, dtype=np.float64).reshape(3)
        radial = np.array([0.0, c[1], c[2]])
        r = float(np.linalg.norm(radial))
        rh = radial / r if r > 1e-9 else np.array([0.0, 1.0, 0.0])
        if r < 1e-9:
            r = 0.0
        standoff = max(standoff_factor * r, min_standoff_m)
        lead = (1.0 if (e[0] - c[0]) >= 0 else -1.0) * lead_factor * standoff
        return (c + standoff * rh + np.array([lead, 0.0, 0.0])).astype(np.float32)


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
        """Türetilmiş nozul ucuna taşı; katman rengini uygula.

        S6.15.1: uzak ``eye_xyz`` yerine temas noktasından türetilen nozul ucu
        kullanılır — crosshair sarım kafasıyla aynı yerde olur.
        """
        if not self._ready or self._item is None:
            return

        eye = np.asarray(
            derive_nozzle_point(frame.eye_xyz, frame.contact_xyz),
            dtype=np.float32,
        )   # (3,)
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
