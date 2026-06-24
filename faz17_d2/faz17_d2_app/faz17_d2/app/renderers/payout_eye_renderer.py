"""
app/renderers/payout_eye_renderer.py — Payout gözü + fiber ray renderer (S4.2.6+)
====================================================================================
PayoutEyeRenderer: göz→temas bağlantısını line olarak çizer.

S6.11.1: GLScatterPlotItem (scatter dot, "pembe nokta") kaldırıldı.
Göz konumu yalnızca ray çizgisi ile gösterilir; scatter item (_eye_item)
artık None'dır.
"""
from __future__ import annotations

import numpy as np

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


class PayoutEyeRenderer:
    """
    Payout gözü konumu ve göz→temas çizgisi renderer'ı.
    """

    def __init__(self) -> None:
        self._eye_item = None
        self._ray_item = None
        self._view = None
        self._ready = False

    def setup(self, view, topology) -> None:
        try:
            import pyqtgraph.opengl as gl
        except ImportError:
            return

        # S6.11.1: scatter dot kaldırıldı
        self._eye_item = None

        self._ray_item = gl.GLLinePlotItem(
            pos=np.zeros((2, 3), dtype=np.float32),
            color=(1.0, 0.65, 0.10, 0.80),
            width=2.5,
            antialias=True,
            mode="line_strip",
        )
        self._ray_item.setVisible(False)

        view.addItem(self._ray_item)
        self._view = view
        self._ready = True

    def teardown(self) -> None:
        for item in (self._eye_item, self._ray_item):
            if item is not None and self._view is not None:
                try:
                    self._view.removeItem(item)
                except Exception:
                    pass
        self._eye_item = None
        self._ray_item = None
        self._ready = False

    def reset(self, **kwargs) -> None:
        """Animasyon sıfırlandığında göz ve ray öğelerini gizle.

        **kwargs: MachineRenderer uyumlu çağrı imzası için yoksayılır.
        """
        for item in (self._eye_item, self._ray_item):
            if item is not None:
                try:
                    item.setVisible(False)
                except Exception:
                    pass

    def update(self, frame) -> None:
        """eye_xyz ve contact_xyz ile göz + ray güncelle."""
        if not self._ready:
            return

        # S6.15.1: uzak eye_xyz yerine türetilen nozul ucu — kısa, kafayla aynı yerde
        eye = derive_nozzle_point(frame.eye_xyz, frame.contact_xyz)
        contact = frame.contact_xyz

        if self._eye_item is not None:
            self._eye_item.setVisible(True)
            self._eye_item.setData(pos=eye.reshape(1, 3))

        if self._ray_item is not None:
            seg = np.vstack([eye, contact]).astype(np.float32)
            self._ray_item.setVisible(True)
            self._ray_item.setData(pos=seg)
