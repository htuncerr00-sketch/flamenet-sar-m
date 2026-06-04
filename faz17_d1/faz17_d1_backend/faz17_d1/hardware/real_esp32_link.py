"""
backend.hardware.real_esp32_link — RealESP32Link loader
=========================================================
Loads the production ``faz18_bringup/real_esp32_link.py`` implementation with
the correct package context so its relative import ``from .esp32_link import …``
resolves to ``backend.hardware.esp32_link``.

Why a loader instead of a plain module?
---------------------------------------
The repository ships *two* parallel ``backend`` package roots:

  • repo-root ``./backend/``                       (this file's home)
  • ``faz17_d1/faz17_d1_backend/faz17_d1/``         (symlinked as the
    desktop UI's ``backend`` — see CLAUDE.md §1)

The single source of truth for the real serial link lives in
``faz18_bringup/real_esp32_link.py``. Rather than duplicate ~400 lines into
every backend root (and risk drift), each root carries this tiny loader that
locates the faz18 source *by walking up the directory tree* — so it resolves
identically no matter which ``backend`` import path the process happens to use
(repo-root, symlink, packaged build, or a fresh ``cp -r`` without symlinks).

Soft fallback
-------------
If the faz18 source cannot be located (e.g. a trimmed mock-only install), this
module exposes a ``RealESP32Link`` *stub* whose constructor raises a clear,
actionable ``ImportError`` — but **import of this module never fails**. That
keeps mock-only setups (no hardware) booting cleanly: ``link_factory.make_link``
can still hand out a ``MockESP32Link`` and the UI starts without a traceback.
"""
from __future__ import annotations

import sys
import importlib.util
from pathlib import Path
from typing import Optional

__all__ = ["RealESP32Link"]


def _find_faz18_source() -> Optional[Path]:
    """
    Walk up from this file's location looking for
    ``<ancestor>/faz18_bringup/real_esp32_link.py``.

    Robust to symlinks: we try both the literal ``__file__`` path (which may
    still be under the symlinked ``backend``) and its fully-resolved form, so
    whichever ancestor chain reaches the repo root first wins.
    """
    seen = set()
    for start in (Path(__file__), Path(__file__).resolve()):
        for ancestor in start.parents:
            if ancestor in seen:
                continue
            seen.add(ancestor)
            cand = ancestor / "faz18_bringup" / "real_esp32_link.py"
            if cand.is_file():
                return cand
    return None


_SRC = _find_faz18_source()
_LOAD_ERROR: Optional[BaseException] = None

if _SRC is not None:
    # Ensure backend.hardware.esp32_link is importable first, so the relative
    # `from .esp32_link import …` inside the faz18 source resolves immediately.
    if "backend.hardware.esp32_link" not in sys.modules:
        try:
            import backend.hardware.esp32_link  # noqa: F401
        except Exception as _exc:
            # esp32_link should always sit next to this loader; if it somehow
            # cannot import, fall through to the stub rather than crash.
            _LOAD_ERROR = _exc
            _SRC = None

if _SRC is not None:
    _spec = importlib.util.spec_from_file_location(
        __name__, str(_SRC), submodule_search_locations=[])
    _mod = importlib.util.module_from_spec(_spec)
    # Bind package context so the relative import lands on backend.hardware.*
    _mod.__package__ = __package__   # 'backend.hardware'
    sys.modules[__name__] = _mod
    try:
        _spec.loader.exec_module(_mod)
    except Exception as _exc:  # pragma: no cover - defensive
        # Runtime load failure — drop the half-initialised module and fall back
        # to the stub so callers get a clear error, not a broken import.
        sys.modules.pop(__name__, None)
        _LOAD_ERROR = _exc
        _SRC = None

if _SRC is None:
    # Hardware source unavailable — expose a clear, non-crashing stub.
    _reason = (
        f"faz18_bringup/real_esp32_link.py not locatable from "
        f"{Path(__file__).resolve().parent}"
    )
    if _LOAD_ERROR is not None:
        _reason += f" ({type(_LOAD_ERROR).__name__}: {_LOAD_ERROR})"

    class RealESP32Link:  # noqa: F811
        """
        Stub for environments without the faz18 real-link source.

        Importing this module always succeeds; only *instantiating* the real
        link raises — so mock-only installations boot cleanly and the failure
        surfaces with an actionable message exactly when real hardware is asked
        for.
        """

        def __init__(self, *args, **kwargs):
            raise ImportError(
                "RealESP32Link is unavailable on this installation.\n"
                f"Reason: {_reason}\n"
                "Real ESP32 hardware mode requires "
                "faz18_bringup/real_esp32_link.py."
            )
