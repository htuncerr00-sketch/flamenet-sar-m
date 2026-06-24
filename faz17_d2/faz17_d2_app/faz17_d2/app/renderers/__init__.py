"""
app/renderers — S4.2.4-6 renderer sınıfları
============================================
Her renderer: setup(view, topology) + update(frame) arayüzü.
RenderFrame dışında fizik hesabı yoktur.

S6.14.1: LAYER_COLORS + NozzleRenderer + FreeFiberRenderer + ContactPointRenderer eklendi.
"""

# ── Katman renk paleti (S6.14.1) ─────────────────────────────────────────────
# Sıra: Layer 0=yeşil, 1=sarı, 2=turuncu, 3+=kırmızı (RGBA float tuple)
LAYER_COLORS = [
    (0.15, 0.85, 0.15, 1.0),   # 0: yeşil
    (1.00, 0.90, 0.10, 1.0),   # 1: sarı
    (1.00, 0.50, 0.10, 1.0),   # 2: turuncu
    (0.90, 0.10, 0.10, 1.0),   # 3+: kırmızı
]

# ── Serbest fiber geometri yardımcıları (S6.15.1) ────────────────────────────
from .fiber_geometry import (
    derive_nozzle_point,
    sample_quadratic_bezier,
    free_fiber_curve,
)

# ── Renderer sınıfları ────────────────────────────────────────────────────────
from .shell_renderer import ShellRenderer
from .heatmap_renderer import HeatmapRenderer
from .ribbon_renderer import RibbonRenderer
from .machine_renderer import MachineRenderer
from .fiber_path_renderer import FiberPathRenderer
from .payout_eye_renderer import PayoutEyeRenderer
from .nozzle_renderer import NozzleRenderer
from .free_fiber_renderer import FreeFiberRenderer
from .contact_point_renderer import ContactPointRenderer
from .deposition_renderer import DepositionRenderer

__all__ = [
    "LAYER_COLORS",
    "derive_nozzle_point",
    "sample_quadratic_bezier",
    "free_fiber_curve",
    "ShellRenderer",
    "HeatmapRenderer",
    "RibbonRenderer",
    "MachineRenderer",
    "FiberPathRenderer",
    "PayoutEyeRenderer",
    "NozzleRenderer",
    "FreeFiberRenderer",
    "ContactPointRenderer",
    "DepositionRenderer",
]
