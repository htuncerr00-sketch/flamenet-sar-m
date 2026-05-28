"""
backend.hardware.real_esp32_link — RealESP32Link loader
=========================================================
Loads faz18_bringup/real_esp32_link.py with the correct package context
so its relative import `from .esp32_link import …` resolves to
backend.hardware.esp32_link.

Falls back to a clear stub if the file is absent, so mock-only setups
(no hardware) don't crash at module-load time.
"""
import sys
import importlib.util
from pathlib import Path

_SRC = Path(__file__).parents[2] / 'faz18_bringup' / 'real_esp32_link.py'

if _SRC.exists():
    # Ensure backend.hardware.esp32_link is already in sys.modules so that
    # the relative import inside real_esp32_link.py resolves immediately.
    if 'backend.hardware.esp32_link' not in sys.modules:
        import backend.hardware.esp32_link  # noqa: F401

    _spec = importlib.util.spec_from_file_location(
        __name__, str(_SRC), submodule_search_locations=[])
    _mod = importlib.util.module_from_spec(_spec)
    _mod.__package__ = __package__   # 'backend.hardware'
    sys.modules[__name__] = _mod
    _spec.loader.exec_module(_mod)
else:
    # Hardware not available — expose a clear stub
    class RealESP32Link:  # noqa: F811
        """
        Stub: faz18_bringup/real_esp32_link.py not found.
        Real ESP32 hardware mode is unavailable on this installation.
        """
        def __init__(self, *args, **kwargs):
            raise ImportError(
                f"RealESP32Link not available: {_SRC} does not exist.\n"
                "Real hardware mode requires faz18_bringup/real_esp32_link.py."
            )

    __all__ = ['RealESP32Link']
