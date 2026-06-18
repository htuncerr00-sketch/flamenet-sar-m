"""
app/renderers — S4.2.4-6 renderer sınıfları
============================================
Her renderer: setup(view, topology) + update(frame) arayüzü.
RenderFrame dışında fizik hesabı yoktur.
"""
from .shell_renderer import ShellRenderer
from .heatmap_renderer import HeatmapRenderer
from .ribbon_renderer import RibbonRenderer
from .machine_renderer import MachineRenderer
from .fiber_path_renderer import FiberPathRenderer
from .payout_eye_renderer import PayoutEyeRenderer

__all__ = [
    "ShellRenderer",
    "HeatmapRenderer",
    "RibbonRenderer",
    "MachineRenderer",
    "FiberPathRenderer",
    "PayoutEyeRenderer",
]
