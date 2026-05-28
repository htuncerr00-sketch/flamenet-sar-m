"""
sim_launcher.py — Windows-friendly simulation-mode launcher
============================================================
One-command launch of the PySide6 desktop app without hardware or symlinks.

    python sim_launcher.py                       # simulation, no hardware
    python sim_launcher.py --link real --port COM3 --baud 921600

What it does
------------
1. Wires the 'backend' package alias so faz17_d1 is importable as 'backend.*'
   (replaces the Linux symlink described in CLAUDE.md for cross-platform use).
2. Provides backend.hardware.real_esp32_link from faz18_bringup/ so that
   link_factory.py can import it at module-load time even in mock mode.
3. Defaults FW_LINK_KIND=mock — no serial port or ESP32 required.
4. Adds the app directory to sys.path and delegates to app.main.

Architecture is unchanged. Wire protocol is unchanged. Switching to real
hardware only requires --link real --port <COMx>.
"""
from __future__ import annotations
import importlib
import importlib.util
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# 1. Backend package aliasing
#    faz17_d1/faz17_d1_backend/faz17_d1/ is the actual package; expose it as
#    'backend' so all `from backend.* import …` in the app resolve correctly.
# ---------------------------------------------------------------------------
_BACKEND_PATH = REPO_ROOT / 'faz17_d1' / 'faz17_d1_backend'
sys.path.insert(0, str(_BACKEND_PATH))

import faz17_d1                # noqa: E402
import faz17_d1.hardware       # noqa: E402
import faz17_d1.core           # noqa: E402
import faz17_d1.ai             # noqa: E402
import faz17_d1.persistence    # noqa: E402

sys.modules['backend']             = faz17_d1
sys.modules['backend.hardware']    = faz17_d1.hardware
sys.modules['backend.core']        = faz17_d1.core
sys.modules['backend.ai']          = faz17_d1.ai
sys.modules['backend.persistence'] = faz17_d1.persistence

# ---------------------------------------------------------------------------
# 2. Pre-import hardware sub-modules under their 'backend.*' names.
#    real_esp32_link.py does `from .esp32_link import …` (relative), so
#    backend.hardware.esp32_link must be in sys.modules before we exec it.
# ---------------------------------------------------------------------------
importlib.import_module('backend.hardware.esp32_link')
importlib.import_module('backend.hardware.telemetry_stream')

# ---------------------------------------------------------------------------
# 3. Inject real_esp32_link as backend.hardware.real_esp32_link.
#    The file lives in faz18_bringup/ (outside the backend package tree), so
#    load it with importlib and set __package__ before executing so its
#    relative imports resolve against backend.hardware.
# ---------------------------------------------------------------------------
_RL_FILE = REPO_ROOT / 'faz18_bringup' / 'real_esp32_link.py'
if not _RL_FILE.exists():
    raise FileNotFoundError(
        f"real_esp32_link.py not found at {_RL_FILE}\n"
        "Repository may be incomplete — expected faz18_bringup/real_esp32_link.py"
    )

_rl_spec = importlib.util.spec_from_file_location(
    'backend.hardware.real_esp32_link', str(_RL_FILE))
_rl_mod = importlib.util.module_from_spec(_rl_spec)
_rl_mod.__package__ = 'backend.hardware'
sys.modules['backend.hardware.real_esp32_link'] = _rl_mod
_rl_spec.loader.exec_module(_rl_mod)

# ---------------------------------------------------------------------------
# 4. Default to simulation (mock) mode.
#    The user can override with --link real on the CLI or FW_LINK_KIND=real.
# ---------------------------------------------------------------------------
os.environ.setdefault('FW_LINK_KIND', 'mock')

# ---------------------------------------------------------------------------
# 5. Add the app root to sys.path and delegate to app.main.
# ---------------------------------------------------------------------------
_APP_ROOT = REPO_ROOT / 'faz17_d2' / 'faz17_d2_app' / 'faz17_d2'
sys.path.insert(0, str(_APP_ROOT))

from app.main import main   # noqa: E402
sys.exit(main())
