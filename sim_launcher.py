"""
sim_launcher.py — Windows-friendly simulation-mode launcher
============================================================
One-command launch of the PySide6 desktop app without hardware or symlinks.

    python sim_launcher.py                       # simulation, no hardware
    python sim_launcher.py --link real --port COM3 --baud 921600

What it does
------------
1. Adds repo root to sys.path so backend/ package is importable.
2. Defaults FW_LINK_KIND=mock — no serial port or ESP32 required.
3. Adds the app directory to sys.path and delegates to app.main.

Architecture is unchanged. Wire protocol is unchanged. Switching to real
hardware only requires --link real --port <COMx>.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# 1. Backend package setup (proper package — no sys.modules aliasing needed)
#    backend/ at repo root re-exports faz17_d1.* with correct structure.
# ---------------------------------------------------------------------------
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
_BACKEND_SRC = REPO_ROOT / 'faz17_d1' / 'faz17_d1_backend'
if str(_BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SRC))

import backend  # noqa: E402  triggers backend/__init__.py path setup

# ---------------------------------------------------------------------------
# 2. Default to simulation (mock) mode.
#    The user can override with --link real on the CLI or FW_LINK_KIND=real.
# ---------------------------------------------------------------------------
os.environ.setdefault('FW_LINK_KIND', 'mock')

# ---------------------------------------------------------------------------
# 3. Add the app root to sys.path and delegate to app.main.
# ---------------------------------------------------------------------------
_APP_ROOT = REPO_ROOT / 'faz17_d2' / 'faz17_d2_app' / 'faz17_d2'
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))

from app.main import main   # noqa: E402
sys.exit(main())
