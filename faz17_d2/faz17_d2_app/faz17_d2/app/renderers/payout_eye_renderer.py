"""
app/renderers/payout_eye_renderer.py — Payout gözü + fiber ray renderer (S4.2.6+)
====================================================================================
PayoutEyeRenderer: payout gözü konumunu scatter, göz→temas bağlantısını line
olarak çizer.
"""
from __future__ import annotations

import numpy as np


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

        self._eye_item = gl.GLScatterPlotItem(
            pos=np.zeros((1, 3), dtype=np.float32),
            size=14,
            color=(1.0, 0.4, 0.1, 1.0),
            pxMode=True,
        )
        self._eye_item.setVisible(False)

        self._ray_item = gl.GLLinePlotItem(
            pos=np.zeros((2, 3), dtype=np.float32),
            color=(1.0, 0.6, 0.2, 0.55),
            width=1.5,
            antialias=True,
            mode="line_strip",
        )
        self._ray_item.setVisible(False)

        view.addItem(self._eye_item)
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

    def update(self, frame) -> None:
        """eye_xyz ve contact_xyz ile göz + ray güncelle."""
        if not self._ready:
            return

        eye = frame.eye_xyz
        contact = frame.contact_xyz

        if self._eye_item is not None:
            self._eye_item.setVisible(True)
            self._eye_item.setData(pos=eye.reshape(1, 3))

        if self._ray_item is not None:
            seg = np.vstack([eye, contact]).astype(np.float32)
            self._ray_item.setVisible(True)
            self._ray_item.setData(pos=seg)
