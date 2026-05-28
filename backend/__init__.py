"""
backend — Proper Python package re-exporting faz17_d1.
Sets up sys.path so all backend.* imports resolve without sys.modules aliasing.
"""
import sys
from pathlib import Path

_BACKEND_SRC = Path(__file__).parent.parent / 'faz17_d1' / 'faz17_d1_backend'
if str(_BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SRC))
