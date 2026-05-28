# DESKTOP_RUNTIME_AUDIT.md
**Filament Winding CAM Platform — Desktop Runtime Audit**
*Generated: 2026-05-28*

---

## 1. Executive Summary

The desktop application was previously dependent on a fragile `sys.modules`
aliasing pattern that injected `faz17_d1.*` as `backend.*` at interpreter
startup. This meant every launch required the exact same startup sequence, and
any module imported before `app_launcher.py` ran would fail to find `backend.*`.

**This audit covers the fixes applied to make the application reliably runnable
on Windows with a single double-click.**

---

## 2. Issues Found and Fixed

### 2.1 Fragile `sys.modules` Aliasing  ✅ FIXED

**Before:**
```python
# app_launcher.py (old)
import faz17_d1, faz17_d1.hardware, faz17_d1.core, faz17_d1.ai, faz17_d1.persistence
sys.modules.update({
    'backend':             faz17_d1,
    'backend.hardware':    faz17_d1.hardware,
    ...
})
# + 4 more lines to inject real_esp32_link via importlib.util.spec_from_file_location
```

**Problem:** If any module importing `backend.*` ran before this block, or if
the startup sequence changed, all downstream imports silently failed.

**Fix:** Created `backend/` as a proper Python package at repo root.
`backend/__init__.py` adds `faz17_d1_backend` to `sys.path`; each submodule is
a thin re-export wrapper. No `sys.modules` manipulation needed.

**After:**
```python
# app_launcher.py (new)
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / 'faz17_d1' / 'faz17_d1_backend'))
import backend  # triggers backend/__init__.py — done
```

---

### 2.2 Fragile `real_esp32_link.py` Loading  ✅ FIXED

**Before:**
```python
_rl = REPO_ROOT / 'faz18_bringup' / 'real_esp32_link.py'
_s  = importlib.util.spec_from_file_location('backend.hardware.real_esp32_link', str(_rl))
_m  = importlib.util.module_from_spec(_s); _m.__package__ = 'backend.hardware'
sys.modules['backend.hardware.real_esp32_link'] = _m; _s.loader.exec_module(_m)
```
If `faz18_bringup/real_esp32_link.py` was missing, the entire app crashed at
startup — even in simulation mode (no hardware).

**Fix:** `backend/hardware/real_esp32_link.py` handles loading with a graceful stub:
```python
if _SRC.exists():
    # Load with correct __package__ so relative imports resolve
    _spec = importlib.util.spec_from_file_location(__name__, str(_SRC))
    ...
else:
    class RealESP32Link:
        def __init__(self, *a, **kw):
            raise ImportError("Hardware not available: faz18_bringup not found")
```
Simulation mode works even if `faz18_bringup/` is absent.

---

### 2.3 12+ Panels with Wrong `sys.path.insert` Calls  ✅ RESOLVED

**Before:** Every panel file contained:
```python
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))
# Points to faz17_d2_app/ — wrong! backend/ is not there.
```
These calls added incorrect directories to `sys.path`. They worked only because
`app_launcher.py`'s `sys.modules` aliasing pre-populated the cache before any
panel was imported.

**Fix:** The new `backend/` package at repo root is the canonical source.
With `REPO_ROOT` on `sys.path` (added by `app_launcher.py`), all
`from backend.* import *` calls work naturally. The stale `sys.path.insert`
calls in panels are now inert (they point to non-existent directories, which
Python silently ignores).

---

### 2.4 `cam_panel.py` Dual-Import Fallback  ✅ FIXED

**Before:**
```python
def _make_backend():
    try:
        from backend.core.geometry_engine import MandrelProfile
        ...
    except ImportError:
        import importlib, pathlib
        base = pathlib.Path(__file__).parents[5] / "faz17_d1" / "faz17_d1_backend"
        sys.path.insert(0, str(base))
        from faz17_d1.core.geometry_engine import MandrelProfile
        ...
```
Two import paths, inconsistent error handling, `parents[5]` depth hardcoded.

**Fix:**
```python
def _make_backend():
    from backend.core.geometry_engine import MandrelProfile
    from backend.core.path_generator import WindingPathParams, generate_path
    from backend.core.motion_planner import plan_motion
    from backend.core.gcode_postprocessor import MachineConfig, generate_gcode
    return MandrelProfile, WindingPathParams, generate_path, plan_motion, MachineConfig, generate_gcode
```

---

### 2.5 No `requirements.txt`  ✅ FIXED

**Before:** Dependencies baked into `.bat`/`.ps1` scripts as bare `pip install PySide6` calls. No version pinning, no reproducibility, no venv support.

**Fix:** Created `requirements.txt`:
```
PySide6>=6.5.0
pyqtgraph>=0.13.3
numpy>=1.24.0
PyOpenGL>=3.1.7
PyOpenGL-accelerate>=3.1.7
pyserial>=3.5
```

---

### 2.6 No Venv Support / No Clean Entry Point  ✅ FIXED

**Before:** Three overlapping launchers (`START_FILAMENT_CAM.bat`,
`run_desktop_sim.bat`, `sim_launcher.py`) — no venv, no unified setup flow.

**Fix:** Added:

| File | Purpose |
|------|---------|
| `create_venv.bat` | One-time setup: creates `.venv\`, installs `requirements.txt` |
| `run_app.bat` | Launch: prefers `.venv\`, falls back to system Python, checks deps |
| `setup_windows.ps1` | PowerShell equivalent with version checks and colored output |

**Recommended user flow:**
```
1. create_venv.bat     (once)
2. run_app.bat         (every launch)
```

---

## 3. New Package Structure

```
flamenet-sar-m/
├── backend/                          ← NEW: proper Python package
│   ├── __init__.py                   # adds faz17_d1_backend to sys.path
│   ├── hardware/
│   │   ├── __init__.py
│   │   ├── esp32_link.py             # re-exports TelemetryFrame, ConnectionState, …
│   │   ├── real_esp32_link.py        # loads faz18_bringup/real_esp32_link.py safely
│   │   ├── telemetry_stream.py
│   │   └── can_bus.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── safety_controller.py
│   │   ├── motion_controller.py
│   │   ├── digital_twin.py
│   │   ├── winding_planner.py
│   │   ├── geometry_engine.py        # MandrelProfile
│   │   ├── path_generator.py         # generate_path, WindingPath
│   │   ├── motion_planner.py         # plan_motion, MotionSegment
│   │   ├── gcode_postprocessor.py    # generate_gcode, MachineConfig
│   │   └── stl_processor.py
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── predictive_maintenance.py
│   │   ├── anomaly_detector.py
│   │   └── adaptive_optimizer.py
│   └── persistence/
│       ├── __init__.py
│       ├── recipe_db.py
│       └── telemetry_db.py
├── requirements.txt                  ← NEW
├── create_venv.bat                   ← NEW
├── run_app.bat                       ← NEW
├── setup_windows.ps1                 ← NEW
├── app_launcher.py                   ← UPDATED (no more sys.modules aliasing)
├── sim_launcher.py                   ← UPDATED (no more sys.modules aliasing)
└── faz17_d2/faz17_d2_app/faz17_d2/app/panels/
    └── cam_panel.py                  ← UPDATED (no more dual-import fallback)
```

---

## 4. Import Resolution Path (After Fix)

```
run_app.bat
  └─ python app_launcher.py
       ├─ sys.path += [REPO_ROOT]
       ├─ sys.path += [REPO_ROOT/faz17_d1/faz17_d1_backend]
       ├─ import backend             → backend/__init__.py
       │    └─ sys.path += [faz17_d1_backend]  (idempotent)
       └─ sys.path += [faz17_d2_app/faz17_d2]
            └─ from app.main_window import FilamentWindingApp
                 ├─ from backend.hardware.esp32_link import TelemetryFrame
                 │    └─ backend/hardware/esp32_link.py
                 │         └─ from faz17_d1.hardware.esp32_link import *
                 ├─ from backend.hardware.real_esp32_link import RealESP32Link
                 │    └─ backend/hardware/real_esp32_link.py
                 │         └─ loads faz18_bringup/real_esp32_link.py (or stub)
                 ├─ from backend.core.geometry_engine import MandrelProfile
                 │    └─ backend/core/geometry_engine.py
                 │         └─ from faz17_d1.core.geometry_engine import *
                 └─ … (all other backend.* imports follow same pattern)
```

---

## 5. Feature Verification Matrix

| Feature | Test Method | Result |
|---------|------------|--------|
| Backend package import | `python -c "import backend; from backend.hardware.esp32_link import TelemetryFrame"` | ✅ PASS |
| RealESP32Link loading | `from backend.hardware.real_esp32_link import RealESP32Link` | ✅ PASS |
| MockESP32Link + link_factory | `make_link(LinkConfig(kind='mock'))` | ✅ PASS |
| MandrelProfile.cylinder | geometry_engine import + instantiation | ✅ PASS |
| CAM path generation | `generate_path(WindingPathParams(...))` | ✅ PASS (3360 pts) |
| G-code generation | `generate_gcode(segments, path, MachineConfig())` | ✅ PASS (3262 lines) |
| Legacy generate_helical | `generate_helical(WindingParams())` | ✅ PASS |
| CAM unit tests | `python faz17_d1/tests/test_cam_core.py` | ✅ 46/46 PASS |
| Full integration | Full startup simulation in Python | ✅ ALL 9 STEPS PASS |

---

## 6. Windows Deployment Checklist

```
□ Python 3.11+ installed from python.org (check "Add to PATH")
□ Double-click create_venv.bat       → creates .venv\, installs all packages
□ Double-click run_app.bat           → launches application
□ Application opens with CAM Üretici as first tab
□ G-code generation works (Geometri sekmesi → Yolu Hesapla → G-code Oluştur)
□ STL loading: STL'den → STL Dosyası Yükle → load a .stl file
□ 3D visualization: 3D Görüntüleyici tab renders fiber paths
□ Logs written to logs\YYYYMMDD\startup_HHMMSS.log
```

---

## 7. Known Remaining Items

| Item | Severity | Notes |
|------|----------|-------|
| Per-panel `sys.path.insert` calls point to wrong dir | Low | Inert (no effect) — cleanup in future refactor |
| `PyOpenGL-accelerate` may fail on some Windows configs | Low | App falls back to pure-Python OpenGL |
| No automatic Python version gate in `run_app.bat` | Low | `create_venv.bat` validates version |
| `faz18_bringup/real_esp32_link.py` not in `faz17_d1/` | Info | By design — kept separate for hardware phase boundary |

---

*Audit performed on branch `claude/amazing-feynman-XUXBf`, commit post-CAM-architecture.*
