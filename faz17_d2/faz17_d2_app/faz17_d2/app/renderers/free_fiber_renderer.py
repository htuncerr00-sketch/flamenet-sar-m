"""
app/renderers/free_fiber_renderer.py — Serbest fiber strand renderer (S6.14.1 / S6.15.1)
==========================================================================================
FreeFiberRenderer: sarım kafası (nozul) ile mandrel temas noktası arasındaki canlı
fiber şeridini çizer.

S6.15.1: düz 2-nokta çizgi yerine EĞRİ (quadratic bezier, N nokta). Nozul ucu temas
noktasından TÜRETİLİR (``free_fiber_curve``) — böylece şerit daima sarım kafasına
bağlanır (eski uzak ``eye_xyz`` kopukluğu giderilir). Katman rengine göre renklenir.
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
    from .fiber_geometry import free_fiber_curve
except ImportError:
    # Doğrudan-dosya test yüklemesi için yedek (paket bağlamı yok)
    def free_fiber_curve(eye_xyz, contact_xyz, n=18, bow_factor=0.16):
        c = np.asarray(contact_xyz, dtype=np.float64).reshape(3)
        e = np.asarray(eye_xyz, dtype=np.float64).reshape(3)
        radial = np.array([0.0, c[1], c[2]])
        r = float(np.linalg.norm(radial))
        rh = radial / r if r > 1e-9 else np.array([0.0, 1.0, 0.0])
        if r < 1e-9:
            r = 0.0
        standoff = max(0.7 * r, 0.030)
        lead = (1.0 if (e[0] - c[0]) >= 0 else -1.0) * 0.45 * standoff
        nozzle = c + standoff * rh + np.array([lead, 0.0, 0.0])
        mid = 0.5 * (nozzle + c)
        ctrl = mid + bow_factor * float(np.linalg.norm(nozzle - c)) * rh
        t = np.linspace(0.0, 1.0, max(2, int(n))).reshape(-1, 1)
        om = 1.0 - t
        pts = (om * om) * nozzle + (2 * om * t) * ctrl + (t * t) * c
        return pts.astype(np.float32)

_N_CURVE = 18   # eğri örnek sayısı


class FreeFiberRenderer:
    """
    Nozul→Temas canlı eğri fiber strand renderer'ı.

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
        """GLLinePlotItem (N-nokta eğri) oluştur ve sahneye ekle."""
        try:
            import pyqtgraph.opengl as gl
        except ImportError:
            return

        self._item = gl.GLLinePlotItem(
            pos=np.zeros((_N_CURVE, 3), dtype=np.float32),
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
        """Türetilmiş nozul → contact_xyz eğri strand'ını katman renginde çiz."""
        if not self._ready or self._item is None:
            return

        pts = free_fiber_curve(frame.eye_xyz, frame.contact_xyz, n=_N_CURVE)
        layer_color = LAYER_COLORS[min(int(getattr(frame, 'layer', 0)),
                                       len(LAYER_COLORS) - 1)]
        self._item.setData(pos=pts, color=layer_color)
        self._item.setVisible(True)
