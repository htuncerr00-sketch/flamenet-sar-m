"""
app_launcher.py — Filament Winding CAM v2
==========================================
Production-quality entry point.  Double-click START_FILAMENT_CAM.bat.

Startup sequence
----------------
1. Init logging (logs/YYYYMMDD/startup_HHMMSS.log)
2. Wire 'backend' package alias (replaces missing symlink)
3. Show splash screen + run dependency checks in background thread
4. First-run workspace setup (workspace/ dirs, default recipe)
5. Show home screen with 5 mode buttons + live winding preview
6. Launch main FilamentWindingApp on selection
"""
from __future__ import annotations
import importlib, importlib.util, logging, math, os, sys, time, traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# 0 · Logging (happens BEFORE PySide6 so crash logs capture everything)
# ─────────────────────────────────────────────────────────────────────────────
REPO_ROOT  = Path(__file__).resolve().parent
_LOG_ROOT  = REPO_ROOT / 'logs' / datetime.now().strftime('%Y%m%d')
_LOG_ROOT.mkdir(parents=True, exist_ok=True)
_LOG_FILE  = _LOG_ROOT / f"startup_{datetime.now().strftime('%H%M%S')}.log"

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)-8s %(name)s: %(message)s',
    handlers=[
        logging.FileHandler(_LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stderr),
    ])
log = logging.getLogger('launcher')
log.info("=== Filament Winding CAM startup ===")
log.info("Log: %s", _LOG_FILE)
log.info("Python %s on %s", sys.version.split()[0], sys.platform)


def _crash_hook(exc_type, exc_value, exc_tb):
    crash_path = _LOG_ROOT / f"crash_{datetime.now().strftime('%H%M%S')}.log"
    msg = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
    log.critical("UNCAUGHT EXCEPTION\n%s", msg)
    crash_path.write_text(msg, encoding='utf-8')
    sys.__excepthook__(exc_type, exc_value, exc_tb)

sys.excepthook = _crash_hook

# ─────────────────────────────────────────────────────────────────────────────
# 1 · Backend package aliasing
# ─────────────────────────────────────────────────────────────────────────────
sys.path.insert(0, str(REPO_ROOT / 'faz17_d1' / 'faz17_d1_backend'))
try:
    import faz17_d1, faz17_d1.hardware, faz17_d1.core, faz17_d1.ai, faz17_d1.persistence
    sys.modules.update({
        'backend':             faz17_d1,
        'backend.hardware':    faz17_d1.hardware,
        'backend.core':        faz17_d1.core,
        'backend.ai':          faz17_d1.ai,
        'backend.persistence': faz17_d1.persistence,
    })
    importlib.import_module('backend.hardware.esp32_link')
    importlib.import_module('backend.hardware.telemetry_stream')
    _rl = REPO_ROOT / 'faz18_bringup' / 'real_esp32_link.py'
    _s  = importlib.util.spec_from_file_location('backend.hardware.real_esp32_link', str(_rl))
    _m  = importlib.util.module_from_spec(_s); _m.__package__ = 'backend.hardware'
    sys.modules['backend.hardware.real_esp32_link'] = _m; _s.loader.exec_module(_m)
    log.info("Backend aliasing OK")
except Exception as e:
    log.critical("Backend aliasing failed: %s", e)

_APP_ROOT = REPO_ROOT / 'faz17_d2' / 'faz17_d2_app' / 'faz17_d2'
sys.path.insert(0, str(_APP_ROOT))

# ─────────────────────────────────────────────────────────────────────────────
# 2 · PySide6 imports
# ─────────────────────────────────────────────────────────────────────────────
from PySide6.QtCore    import Qt, QThread, Signal, QTimer, QSettings, QSize
from PySide6.QtGui     import (QPainter, QColor, QPen, QBrush, QLinearGradient,
                                QFont, QPalette)
from PySide6.QtWidgets import (QApplication, QDialog, QWidget, QVBoxLayout,
                                QHBoxLayout, QLabel, QPushButton, QFrame,
                                QProgressBar, QFileDialog, QMessageBox,
                                QComboBox, QDialogButtonBox, QPlainTextEdit,
                                QSizePolicy, QLineEdit, QCheckBox, QGroupBox,
                                QFormLayout, QScrollArea)
import PySide6

# ─────────────────────────────────────────────────────────────────────────────
# 3 · Constants
# ─────────────────────────────────────────────────────────────────────────────
APP_NAME    = "Filament Winding CAM"
APP_VERSION = "1.0"
WORKSPACE   = REPO_ROOT / 'workspace'
SETTINGS_ORG = "FilamentWinding"
SETTINGS_APP = "CAM"

C = {
    'bg':       '#1e2228', 'bg_dark':  '#0d1117', 'bg_panel': '#252a32',
    'text':     '#e8eaed', 'dim':      '#a0a8b0',  'muted':   '#5a6068',
    'accent':   '#5dade2', 'ok':       '#5cb85c',  'warn':    '#f0ad4e',
    'crit':     '#d9534f', 'border':   '#3a4048',
}

# ─────────────────────────────────────────────────────────────────────────────
# 4 · Workspace management
# ─────────────────────────────────────────────────────────────────────────────
class WorkspaceManager:
    DIRS = ['gcode', 'projects', 'logs', 'exports']
    FIRST_RUN_FLAG = WORKSPACE / '.initialized'

    @classmethod
    def setup(cls) -> bool:
        """Create workspace structure. Returns True if first run."""
        first_run = not cls.FIRST_RUN_FLAG.exists()
        WORKSPACE.mkdir(parents=True, exist_ok=True)
        for d in cls.DIRS:
            (WORKSPACE / d).mkdir(exist_ok=True)
        if first_run:
            cls._create_default_project()
            cls.FIRST_RUN_FLAG.touch()
            log.info("First-run workspace initialised at %s", WORKSPACE)
        return first_run

    @classmethod
    def _create_default_project(cls):
        gcode_dir = WORKSPACE / 'gcode'
        try:
            from backend.core.winding_planner import WindingParams, generate_helical
            prog = generate_helical(WindingParams(
                mandrel_R_mm=75, mandrel_L_mm=300, alpha_deg=55,
                n_layers=4, tow_width_mm=6, fiber_tension_N=15, feed_mm_s=80))
            (gcode_dir / 'sample_55deg_4layer.nc').write_text(
                '\n'.join(prog.lines), encoding='utf-8')
            log.info("Default G-code sample written")
        except Exception as e:
            log.warning("Could not generate default sample: %s", e)

# ─────────────────────────────────────────────────────────────────────────────
# 5 · Dependency checks (run in background thread during splash)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class CheckResult:
    name: str
    ok: bool
    message: str
    optional: bool = False


class DependencyChecker(QThread):
    check_progress = Signal(str, bool, str, bool)   # name, ok, msg, optional
    all_done       = Signal(bool, list)              # overall_ok, results

    def run(self):
        checks = [
            ('Python 3.11+',      self._py_version,  False),
            ('PySide6 (Qt6)',     self._pyside6,     False),
            ('pyqtgraph',         self._pyqtgraph,   False),
            ('NumPy',             self._numpy,       False),
            ('Backend modules',   self._backend,     False),
            ('OpenGL (3D view)',  self._opengl,      True),
            ('pyserial (ESP32)',  self._pyserial,    True),
        ]
        results, all_ok = [], True
        for name, fn, optional in checks:
            ok, msg = fn()
            log.info("  %-22s %s  %s", name, 'OK' if ok else 'FAIL', msg)
            self.check_progress.emit(name, ok, msg, optional)
            r = CheckResult(name, ok, msg, optional)
            results.append(r)
            if not ok and not optional:
                all_ok = False
            self.msleep(120)
        self.all_done.emit(all_ok, results)

    def _py_version(self):
        v = sys.version_info
        ok = (v.major, v.minor) >= (3, 11)
        return ok, f"{v.major}.{v.minor}.{v.micro}"

    def _pyside6(self):
        return True, PySide6.__version__

    def _pyqtgraph(self):
        try:
            import pyqtgraph as pg
            return True, pg.__version__
        except ImportError:
            return False, "pip install pyqtgraph"

    def _numpy(self):
        try:
            import numpy as np
            return True, np.__version__
        except ImportError:
            return False, "pip install numpy"

    def _backend(self):
        try:
            from backend.core.winding_planner import WindingParams
            from backend.hardware.esp32_link  import MockESP32Link
            return True, "faz17_d1 resolved"
        except Exception as e:
            return False, str(e)[:60]

    def _opengl(self):
        try:
            import OpenGL
            return True, OpenGL.__version__
        except ImportError:
            return True, "not installed (3D uses software fallback)"

    def _pyserial(self):
        try:
            import serial
            return True, serial.__version__
        except ImportError:
            return True, "not installed (simulation mode only)"

# ─────────────────────────────────────────────────────────────────────────────
# 6 · Splash screen
# ─────────────────────────────────────────────────────────────────────────────
class SplashScreen(QDialog):
    ready = Signal(bool, list)   # forwarded from checker

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(540, 300)
        self._n_done = 0
        self._n_total = 7
        self._build()
        self._center()
        self._checker = DependencyChecker(self)
        self._checker.check_progress.connect(self._on_check)
        self._checker.all_done.connect(self._on_done)

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background:{C['bg_dark']};
                border:1px solid {C['border']};
                border-radius:12px;
            }}
        """)
        outer.addWidget(frame)

        lay = QVBoxLayout(frame)
        lay.setContentsMargins(40, 36, 40, 28)
        lay.setSpacing(10)

        title = QLabel(APP_NAME)
        title.setStyleSheet(f"color:{C['accent']}; font:bold 22pt 'Segoe UI';")
        title.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)

        sub = QLabel("Advanced Composite Manufacturing Platform")
        sub.setStyleSheet(f"color:{C['dim']}; font:10pt 'Segoe UI';")
        sub.setAlignment(Qt.AlignCenter)
        lay.addWidget(sub)

        lay.addSpacing(16)

        self._progress = QProgressBar()
        self._progress.setRange(0, self._n_total)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(6)
        self._progress.setStyleSheet(f"""
            QProgressBar {{
                background:{C['border']}; border-radius:3px; border:none;
            }}
            QProgressBar::chunk {{
                background:{C['accent']}; border-radius:3px;
            }}
        """)
        lay.addWidget(self._progress)

        self._status = QLabel("Initialising…")
        self._status.setStyleSheet(f"color:{C['dim']}; font:9pt Consolas;")
        self._status.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._status)

        lay.addStretch()

        ver = QLabel(f"v{APP_VERSION}")
        ver.setStyleSheet(f"color:{C['muted']}; font:8pt 'Segoe UI';")
        ver.setAlignment(Qt.AlignRight)
        lay.addWidget(ver)

    def _center(self):
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.center() - self.rect().center())

    def start_checks(self):
        self._checker.start()

    def _on_check(self, name, ok, msg, optional):
        self._n_done += 1
        self._progress.setValue(self._n_done)
        icon = '✓' if ok else ('!' if optional else '✗')
        self._status.setText(f"{icon} {name}  {msg}")
        color = C['ok'] if ok else (C['warn'] if optional else C['crit'])
        self._status.setStyleSheet(f"color:{color}; font:9pt Consolas;")

    def _on_done(self, all_ok, results):
        if all_ok:
            self._status.setText("✓  Ready to launch")
            self._status.setStyleSheet(f"color:{C['ok']}; font:bold 9pt Consolas;")
        else:
            self._status.setText("✗  Missing required packages — see TROUBLESHOOTING.md")
            self._status.setStyleSheet(f"color:{C['crit']}; font:bold 9pt Consolas;")
        QTimer.singleShot(600, lambda: self.ready.emit(all_ok, results))

# ─────────────────────────────────────────────────────────────────────────────
# 7 · Winding preview widget
# ─────────────────────────────────────────────────────────────────────────────
class WindingPreviewWidget(QWidget):
    LAYERS = [
        (QColor(93, 173, 226),  1,  8),
        (QColor(255, 165,  50), -1, 8),
        (QColor( 80, 200, 120),  1, 4),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._phase = 0.0
        t = QTimer(self); t.timeout.connect(self._tick); t.start(33)
        self.setMinimumSize(300, 200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def _tick(self):
        self._phase += 0.018; self.update()

    def paintEvent(self, ev):  # noqa: N802
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()
        p.fillRect(0, 0, W, H, QColor(12, 16, 21))
        cx, cy = W // 2, H // 2
        mw, mh = int(W * 0.74), int(H * 0.40)
        ea = max(int(mh * 0.25), 5)
        x0, x1 = cx - mw // 2, cx + mw // 2

        g = QLinearGradient(cx, cy - mh // 2, cx, cy + mh // 2)
        g.setColorAt(0.0, QColor(88, 93, 98)); g.setColorAt(0.3, QColor(112, 117, 122))
        g.setColorAt(0.65, QColor(68, 73, 78)); g.setColorAt(1.0, QColor(42, 47, 52))
        p.setBrush(QBrush(g)); p.setPen(Qt.NoPen)
        p.drawRect(x0, cy - mh // 2, mw, mh)

        for color, direction, n_circ in self.LAYERS:
            R_s = mh / 2.0 * 0.66; prev = None; prev_c = None
            ph = direction * self._phase
            for i in range(181):
                t = i / 180; sx = x0 + t * (x1 - x0)
                theta = direction * n_circ * 2 * math.pi * t + ph
                sy = cy + math.sin(theta) * R_s; fc = math.cos(theta)
                fade = min(t * 10, (1 - t) * 10, 1.0)
                a = int(255 * fade * (0.18 + 0.82 * max(0.0, fc)))
                qc = QColor(color); qc.setAlpha(a)
                p.setPen(QPen(qc, 1.4))
                pt_now = (int(sx), int(sy))
                if prev and abs(sy - prev[1]) < R_s * 1.5 and not (prev_c and prev_c > 0.1 and fc < -0.1):
                    p.drawLine(prev[0], prev[1], pt_now[0], pt_now[1])
                prev = pt_now; prev_c = fc

        p.setBrush(QBrush(QColor(48, 53, 58))); p.setPen(QPen(QColor(38, 43, 48), 1))
        p.drawEllipse(x0 - ea, cy - mh // 2, ea * 2, mh)
        g2 = QLinearGradient(x1 - ea, cy, x1 + ea, cy)
        g2.setColorAt(0, QColor(83, 88, 93)); g2.setColorAt(1, QColor(48, 53, 58))
        p.setBrush(QBrush(g2)); p.setPen(QPen(QColor(63, 68, 73), 1))
        p.drawEllipse(x1 - ea, cy - mh // 2, ea * 2, mh)
        p.setPen(QColor(60, 70, 80)); p.setFont(QFont("Consolas", 8))
        p.drawText(8, H - 8, "α=55°  3-layer  R=75mm  L=300mm")

# ─────────────────────────────────────────────────────────────────────────────
# 8 · Dialogs (port picker, G-code viewer, settings)
# ─────────────────────────────────────────────────────────────────────────────
class PortPickerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connect to ESP32")
        self.setFixedSize(380, 190)
        self.port = None; self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Select COM port:"))
        self._combo = QComboBox(); self._refresh()
        lay.addWidget(self._combo)
        r = QPushButton("Refresh"); r.clicked.connect(self._refresh)
        lay.addWidget(r)
        note = QLabel("Baud: 921600 (fixed)  ·  Driver: CP210x or CH340")
        note.setStyleSheet(f"color:{C['dim']}; font-size:9pt;")
        lay.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _refresh(self):
        self._combo.clear()
        try:
            import serial.tools.list_ports
            ports = [p.device for p in serial.tools.list_ports.comports()]
        except ImportError:
            import glob
            ports = glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*') + [f'COM{i}' for i in range(1, 17)]
        for p in (ports or ['COM3', '/dev/ttyUSB0']):
            self._combo.addItem(p)

    def _ok(self):
        self.port = self._combo.currentText(); self.accept()


class GCodeDialog(QDialog):
    def __init__(self, prog, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sample G-code Preview"); self.setMinimumSize(660, 500)
        self._text = '\n'.join(prog.lines); lay = QVBoxLayout(self)
        stats = (f"Circuits: {prog.n_circuits}  ·  "
                 f"Length: {prog.total_length_mm/1000:.2f} m  ·  "
                 f"Est. time: {prog.estimated_time_s/60:.1f} min")
        lbl = QLabel(stats); lbl.setStyleSheet(f"color:{C['dim']}; font:9pt Consolas;")
        lay.addWidget(lbl)
        ed = QPlainTextEdit(); ed.setReadOnly(True); ed.setPlainText(self._text)
        ed.setFont(QFont("Consolas", 9))
        ed.setStyleSheet(f"background:{C['bg_dark']}; color:{C['text']}; border:1px solid {C['border']};")
        lay.addWidget(ed)
        row = QHBoxLayout()
        cb = QPushButton("Copy"); cb.clicked.connect(lambda: QApplication.clipboard().setText(self._text))
        sb = QPushButton("Save…"); sb.clicked.connect(self._save)
        cl = QPushButton("Close"); cl.clicked.connect(self.accept)
        row.addWidget(cb); row.addWidget(sb); row.addStretch(); row.addWidget(cl)
        lay.addLayout(row)

    def _save(self):
        p, _ = QFileDialog.getSaveFileName(self, "Save G-code", "winding.nc",
                                           "G-code (*.nc *.gcode);;All files (*)")
        if p: Path(p).write_text(self._text, encoding='utf-8')


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings"); self.setFixedSize(460, 340)
        self._s = QSettings(SETTINGS_ORG, SETTINGS_APP)
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)

        hw = QGroupBox("Hardware")
        hf = QFormLayout(hw)
        self._port = QLineEdit(self._s.value("port", "COM3"))
        hf.addRow("Default port:", self._port)
        self._autoconn = QCheckBox("Auto-connect on startup")
        self._autoconn.setChecked(self._s.value("autoconn", False, bool))
        hf.addRow("", self._autoconn)
        lay.addWidget(hw)

        ws_box = QGroupBox("Workspace")
        wf = QFormLayout(ws_box)
        self._ws_path = QLineEdit(str(WORKSPACE))
        self._ws_path.setReadOnly(True)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_ws)
        wr = QHBoxLayout(); wr.addWidget(self._ws_path); wr.addWidget(browse)
        wf.addRow("Path:", wr)
        lay.addWidget(ws_box)

        disp = QGroupBox("Display")
        df = QFormLayout(disp)
        self._opengl = QCheckBox("Enable OpenGL 3D rendering")
        self._opengl.setChecked(self._s.value("opengl", True, bool))
        df.addRow("", self._opengl)
        lay.addWidget(disp)

        lay.addStretch()
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _browse_ws(self):
        d = QFileDialog.getExistingDirectory(self, "Select Workspace", str(WORKSPACE))
        if d: self._ws_path.setText(d)

    def _save(self):
        self._s.setValue("port", self._port.text())
        self._s.setValue("autoconn", self._autoconn.isChecked())
        self._s.setValue("opengl", self._opengl.isChecked())
        self.accept()

# ─────────────────────────────────────────────────────────────────────────────
# 9 · Home screen
# ─────────────────────────────────────────────────────────────────────────────
_BTN = """
QPushButton {{
    background:{bg}; color:{fg}; border:1px solid {b};
    border-radius:6px; padding:0 16px;
    font-size:11pt; font-weight:bold; text-align:left;
}}
QPushButton:hover {{ background:{hv}; border-color:{fg}; }}
QPushButton:pressed {{ background:{bg}; }}
"""

def _bs(bg, fg='#e8eaed', border=None, hover=None):
    b = border or bg; hv = hover or bg
    return _BTN.format(bg=bg, fg=fg, b=b, hv=hv)


class _ModeBtn(QPushButton):
    def __init__(self, icon, title, sub, style, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(68)
        self.setStyleSheet(style)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 10)
        outer.setSpacing(2)
        top = QHBoxLayout(); top.setSpacing(8)
        ic = QLabel(icon); ic.setStyleSheet("font-size:16pt; background:transparent; border:none;")
        tl = QLabel(title); tl.setStyleSheet(f"font:bold 11pt 'Segoe UI'; color:#e8eaed; background:transparent; border:none;")
        top.addWidget(ic); top.addWidget(tl); top.addStretch()
        sl = QLabel(sub); sl.setStyleSheet(f"font:8pt 'Segoe UI'; color:{C['dim']}; background:transparent; border:none;")
        outer.addLayout(top); outer.addWidget(sl)


class HomeScreen(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME}  –  v{APP_VERSION}")
        self.setMinimumSize(980, 580)
        self.setStyleSheet(f"background:{C['bg']}; color:{C['text']};")
        self._main_window = None
        self._build()

    def _build(self):
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QFrame()
        hdr.setFixedHeight(58)
        hdr.setStyleSheet(f"background:#10151c; border-bottom:1px solid {C['border']};")
        hl = QHBoxLayout(hdr); hl.setContentsMargins(22, 0, 22, 0)
        tl = QLabel(f"⚙  {APP_NAME}")
        tl.setStyleSheet(f"font:bold 17pt 'Segoe UI'; color:{C['accent']};")
        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet(f"color:{C['ok']}; font-size:14pt;")
        self._status_txt = QLabel("Simulation ready")
        self._status_txt.setStyleSheet(f"color:{C['dim']}; font:9pt 'Segoe UI';")
        hl.addWidget(tl); hl.addSpacing(12)
        hl.addWidget(self._status_dot); hl.addWidget(self._status_txt)
        hl.addStretch()
        settings_btn = QPushButton("⚙")
        settings_btn.setFixedSize(34, 34)
        settings_btn.setStyleSheet(f"background:{C['bg_panel']}; color:{C['dim']}; border:1px solid {C['border']}; border-radius:4px; font-size:13pt;")
        settings_btn.clicked.connect(self._on_settings)
        hl.addWidget(settings_btn)
        root.addWidget(hdr)

        # ── Body ──────────────────────────────────────────────────────────────
        body = QHBoxLayout(); body.setContentsMargins(22, 18, 22, 14); body.setSpacing(22)
        root.addLayout(body, stretch=1)

        # Left: winding preview
        pf = QFrame()
        pf.setStyleSheet(f"background:#10151c; border:1px solid {C['border']}; border-radius:8px;")
        pfl = QVBoxLayout(pf); pfl.setContentsMargins(8, 8, 8, 8)
        pl = QLabel("Live Path Preview")
        pl.setStyleSheet(f"color:{C['muted']}; font:8pt 'Segoe UI'; border:none;")
        pfl.addWidget(pl)
        self._preview = WindingPreviewWidget()
        pfl.addWidget(self._preview, stretch=1)
        body.addWidget(pf, stretch=3)

        # Right: buttons + recent
        right = QVBoxLayout(); right.setSpacing(10)
        body.addLayout(right, stretch=2)

        ml = QLabel("Select Mode")
        ml.setStyleSheet(f"font:bold 10pt 'Segoe UI'; color:{C['dim']}; border-bottom:1px solid {C['border']}; padding-bottom:4px;")
        right.addWidget(ml)

        modes = [
            ("▶", "Simulation Mode",   "Full app · no hardware required",       '#1a3a5c', '#5dade2', '#1e4470'),
            ("⚡", "Connect ESP32",     "Select COM port · live telemetry",      '#1a3a2a', '#5cb85c', '#1e4030'),
            ("≡", "G-code Generator",  "Build winding programs · export .nc",   '#2a3038', '#e8eaed', '#323a44'),
            ("◉", "3D Visualizer",     "Inspect fiber path on mandrel",         '#2a3038', '#e8eaed', '#323a44'),
            ("⚙", "Settings",          "Workspace · port · display options",    '#2a3038', '#a0a8b0', '#323a44'),
        ]
        self._btns = []
        for icon, title, sub, bg, fg, hv in modes:
            b = _ModeBtn(icon, title, sub, _bs(bg, fg, hover=hv))
            right.addWidget(b)
            self._btns.append(b)

        self._btns[0].clicked.connect(self._on_simulation)
        self._btns[1].clicked.connect(self._on_hardware)
        self._btns[2].clicked.connect(self._on_gcode_gen)
        self._btns[3].clicked.connect(lambda: self._launch(tab=1))
        self._btns[4].clicked.connect(self._on_settings)

        right.addStretch()

        # ── Status bar ────────────────────────────────────────────────────────
        sb = QFrame(); sb.setFixedHeight(26)
        sb.setStyleSheet(f"background:#0a0e13; border-top:1px solid #1e2228;")
        sl = QHBoxLayout(sb); sl.setContentsMargins(16, 0, 16, 0)
        py_v  = sys.version.split()[0]
        ps_v  = PySide6.__version__
        ws_s  = str(WORKSPACE)
        parts = [f"Python {py_v}", f"PySide6 {ps_v}", f"Workspace: {ws_s}", f"Log: {_LOG_FILE.name}"]
        info  = QLabel("  ·  ".join(parts))
        info.setStyleSheet(f"color:{C['muted']}; font:7pt Consolas;")
        sl.addWidget(info); sl.addStretch()
        root.addWidget(sb)

    # ── Handlers ──────────────────────────────────────────────────────────────
    def _on_simulation(self):
        os.environ['FW_LINK_KIND'] = 'mock'
        self._launch(tab=1)

    def _on_hardware(self):
        dlg = PortPickerDialog(self)
        self._style_child(dlg)
        if dlg.exec() != QDialog.Accepted or not dlg.port:
            return
        os.environ.update({'FW_LINK_KIND': 'real', 'FW_LINK_PORT': dlg.port, 'FW_LINK_BAUD': '921600'})
        self._status_dot.setStyleSheet(f"color:{C['ok']}; font-size:14pt;")
        self._status_txt.setText(f"Connected: {dlg.port}")
        log.info("Hardware mode selected: %s", dlg.port)
        self._launch(tab=0)

    def _on_gcode_gen(self):
        os.environ['FW_LINK_KIND'] = 'mock'
        self._launch(tab=4)

    def _on_settings(self):
        dlg = SettingsDialog(self)
        self._style_child(dlg)
        dlg.exec()

    def _style_child(self, dlg):
        dlg.setStyleSheet(
            f"background:{C['bg']}; color:{C['text']};"
            f"QGroupBox{{border:1px solid {C['border']}; margin-top:10px; border-radius:4px;}}"
            f"QGroupBox::title{{subcontrol-origin:margin; left:8px; color:{C['dim']};}}"
            f"QLineEdit,QComboBox{{background:{C['bg_panel']}; border:1px solid {C['border']}; color:{C['text']}; border-radius:3px; padding:4px;}}"
            f"QPushButton{{background:{C['bg_panel']}; color:{C['text']}; border:1px solid {C['border']}; border-radius:4px; padding:5px 10px;}}"
            f"QPushButton:hover{{background:#323a44;}}"
        )

    def _launch(self, tab: int = 1):
        self.hide()
        try:
            from app.main_window import FilamentWindingApp
            from app.link_factory import LinkConfig, make_link
            os.environ.setdefault('FW_LINK_KIND', 'mock')
            link = make_link(LinkConfig.from_env())
            self._main_window = FilamentWindingApp(link=link)
            self._main_window._tabs.setCurrentIndex(tab)
            self._main_window.show()
            self._main_window.destroyed.connect(QApplication.quit)
            log.info("Main app launched (tab=%d, link=%s)", tab, type(link).__name__)
        except Exception as e:
            log.exception("Failed to launch main app")
            self.show()
            QMessageBox.critical(self, "Launch failed",
                f"Could not start application:\n\n{e}\n\n"
                "Check logs/ for details or see TROUBLESHOOTING.md.")

# ─────────────────────────────────────────────────────────────────────────────
# 10 · Entry point
# ─────────────────────────────────────────────────────────────────────────────
def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(SETTINGS_ORG)
    app.setStyle("Fusion")

    pal = QPalette()
    for role, hex_col in [
        (QPalette.Window,          '#1e2228'), (QPalette.WindowText,      '#e8eaed'),
        (QPalette.Base,            '#0d1117'), (QPalette.AlternateBase,   '#252a32'),
        (QPalette.Text,            '#e8eaed'), (QPalette.Button,          '#2a3038'),
        (QPalette.ButtonText,      '#e8eaed'), (QPalette.Highlight,       '#5dade2'),
        (QPalette.HighlightedText, '#0d1117'), (QPalette.ToolTipBase,     '#252a32'),
        (QPalette.ToolTipText,     '#e8eaed'),
    ]:
        pal.setColor(role, QColor(hex_col))
    app.setPalette(pal)

    first_run = WorkspaceManager.setup()
    log.info("Workspace: %s  (first_run=%s)", WORKSPACE, first_run)

    splash = SplashScreen()
    home   = HomeScreen()

    def _on_ready(ok, results):
        splash.close()
        fails = [r for r in results if not r.ok and not r.optional]
        if fails:
            names = ', '.join(r.name for r in fails)
            reply = QMessageBox.critical(None, "Missing dependencies",
                f"Required packages not found:\n  {names}\n\n"
                "Run install_deps_windows.bat then restart.\n"
                "See TROUBLESHOOTING.md for details.",
                QMessageBox.Ok | QMessageBox.Ignore)
            if reply == QMessageBox.Ok:
                log.error("Startup aborted — missing dependencies: %s", names)
                app.quit(); return
        home.show()
        log.info("Home screen shown")

    splash.ready.connect(_on_ready)
    splash.show()
    QTimer.singleShot(200, splash.start_checks)

    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
