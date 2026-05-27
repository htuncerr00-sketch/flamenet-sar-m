"""
app/settings.py — Centralized QSettings Wrapper
=====================================================
Per-key getter/setter with type safety. Used by main_window for layout
persistence and by panels for user preferences.

ORG / APP names are used as registry keys on Windows, plist keys on macOS,
.config/<ORG>/<APP>.conf on Linux.
"""
from __future__ import annotations
from typing import Any, Optional

from PySide6.QtCore import QSettings, QByteArray

ORG = "FaramentWinding"
APP = "DesktopApp"


class AppSettings:
    """
    Thin wrapper around QSettings that:
      - centralizes ORG/APP names
      - provides typed getters
      - groups keys by panel for clarity
    """
    # --- top-level keys ---
    K_WINDOW_GEOMETRY = "MainWindow/geometry"
    K_WINDOW_STATE    = "MainWindow/state"
    K_LAST_TAB        = "MainWindow/lastTab"
    K_LAST_RECIPE     = "Recipe/lastId"
    K_RECORDING       = "Recording/enabled"
    K_THEME           = "Appearance/theme"
    K_AUTOSAVE_SEC    = "Recording/autosaveSeconds"

    def __init__(self) -> None:
        self._s = QSettings(ORG, APP)

    # ------- raw access -------
    def value(self, key: str, default: Any = None, t=None) -> Any:
        v = self._s.value(key, default)
        if t is not None and v is not None and not isinstance(v, t):
            try:
                v = t(v)
            except (TypeError, ValueError):
                return default
        return v

    def set(self, key: str, val: Any) -> None:
        self._s.setValue(key, val)
        self._s.sync()

    # ------- typed convenience -------
    def get_bytes(self, key: str) -> Optional[QByteArray]:
        v = self._s.value(key)
        return v if isinstance(v, QByteArray) else None

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            v = self._s.value(key, default)
            return int(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        v = self._s.value(key, default)
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.lower() in ("true", "1", "yes", "on")
        return bool(v) if v is not None else default

    def get_str(self, key: str, default: str = "") -> str:
        v = self._s.value(key, default)
        return str(v) if v is not None else default

    # ------- groups for clarity -------
    def save_window_geometry(self, geom: bytes) -> None:
        self.set(self.K_WINDOW_GEOMETRY, geom)

    def save_window_state(self, state: bytes) -> None:
        self.set(self.K_WINDOW_STATE, state)

    def restore_window_geometry(self) -> Optional[QByteArray]:
        return self.get_bytes(self.K_WINDOW_GEOMETRY)

    def restore_window_state(self) -> Optional[QByteArray]:
        return self.get_bytes(self.K_WINDOW_STATE)
