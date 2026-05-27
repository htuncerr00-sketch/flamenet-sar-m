"""
app/link_factory.py — Backend Link Factory
=================================================
Selects MockESP32Link or RealESP32Link based on runtime configuration.

Sources, in priority order:
  1. Explicit argument to make_link()
  2. Environment variable FW_LINK_KIND ("mock" or "real")
  3. Environment variable FW_LINK_PORT (if set, implies "real")
  4. Default: "mock"

This is intentionally tiny — bring-up sprint, not a new architecture.
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Optional

from backend.hardware.esp32_link import ESP32LinkBase, MockESP32Link
from backend.hardware.real_esp32_link import RealESP32Link


@dataclass
class LinkConfig:
    kind:            str   = "mock"      # "mock" or "real"
    port:            str   = "/dev/ttyUSB0"
    baud:            int   = 921_600
    mock_rate_hz:    float = 1000.0
    mock_seed:       int   = 42
    watchdog_s:      float = 1.0
    auto_reconnect:  bool  = True

    @classmethod
    def from_env(cls) -> "LinkConfig":
        cfg = cls()
        kind = os.environ.get("FW_LINK_KIND", "").lower()
        port = os.environ.get("FW_LINK_PORT", "")
        if kind in ("real", "mock"):
            cfg.kind = kind
        elif port:
            cfg.kind = "real"
        if port:
            cfg.port = port
        baud = os.environ.get("FW_LINK_BAUD")
        if baud:
            try:
                cfg.baud = int(baud)
            except ValueError:
                pass
        return cfg


def make_link(cfg: Optional[LinkConfig] = None) -> ESP32LinkBase:
    """Construct and return a link object (not connected yet)."""
    cfg = cfg or LinkConfig.from_env()
    if cfg.kind == "real":
        return RealESP32Link(
            port=cfg.port,
            baud=cfg.baud,
            watchdog_s=cfg.watchdog_s,
            auto_reconnect=cfg.auto_reconnect,
        )
    return MockESP32Link(seed=cfg.mock_seed, rate_hz=cfg.mock_rate_hz)
