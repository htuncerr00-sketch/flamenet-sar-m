"""
app_launcher.py — Filament Winding CAM Launcher
=================================================
Entry point for end users.  Double-click START_FILAMENT_CAM.bat (or .ps1).

Shows a launcher window with a live winding preview and four large buttons:
  • Simulation Mode   — mock ESP32, no hardware required
  • Hardware Mode     — pick COM port, connect real firmware
  • Open Last Project — loads the most recently saved recipe
  • Generate G-code   — instant sample G-code without starting the full app

Also wires the 'backend' package alias so the PySide6 app imports resolve
on any machine regardless of symlink state.
"""
from __future__ import annotations
import importlib, importlib.util, math, os, sys
from pathlib import Path

# ── 1. Backend aliasing ───────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT / 'faz17_d1' / 'faz17_d1_backend'))

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

_APP_ROOT = REPO_ROOT / 'faz17_d2' / 'faz17_d2_app' / 'faz17_d2'
sys.path.insert(0, str(_APP_ROOT))

# ── 2. PySide6 ────────────────────────────────────────────────────────────────
from PySide6.QtCore    import Qt, QTimer, QSize, QPoint
from PySide6.QtGui     import (QPainter, QColor, QPen, QBrush, QLinearGradient,
                                QFont, QFontMetrics, QIcon, QClipboard)
from PySide6.QtWidgets import (QApplication, QDialog, QVBoxLayout, QHBoxLayout,
                                QGridLayout, QPushButton, QLabel, QWidget,
                                QFrame, QTextEdit, QFileDialog, QMessageBox,
                                QComboBox, QDialogButtonBox, QSizePolicy,
                                QPlainTextEdit, QProgressBar)
import PySide6

_DB_PATH = str(REPO_ROOT / 'recipes.db')


# ─────────────────────────────────────────────────────────────────────────────
# Winding preview widget
# ─────────────────────────────────────────────────────────────────────────────
class WindingPreviewWidget(QWidget):
    """Animated 2-D helical-path preview — draws with QPainter, no OpenGL."""

    LAYERS = [
        (QColor(93, 173, 226),  55.0,  1, 8),   # blue,   55°, fwd, 8 circuits
        (QColor(255, 165,  50), 55.0, -1, 8),   # orange, 55°, rev
        (QColor( 80, 200, 120), 20.0,  1, 5),   # green,  20° hoop
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)
        self.setMinimumSize(380, 240)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def _tick(self):
        self._phase += 0.018
        self.update()

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()

        # Background
        p.fillRect(0, 0, W, H, QColor(15, 20, 25))

        cx, cy = W // 2, H // 2
        mw = int(W * 0.72)
        mh = int(H * 0.40)
        ea = max(int(mh * 0.25), 6)
        x0 = cx - mw // 2
        x1 = cx + mw // 2

        # Mandrel body
        grad = QLinearGradient(cx, cy - mh // 2, cx, cy + mh // 2)
        grad.setColorAt(0.00, QColor(90, 95, 100))
        grad.setColorAt(0.30, QColor(115, 120, 125))
        grad.setColorAt(0.65, QColor(70, 75, 80))
        grad.setColorAt(1.00, QColor(45, 50, 55))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.NoPen)
        p.drawRect(x0, cy - mh // 2, mw, mh)

        # Fiber paths
        for color, alpha_deg, direction, n_circ in self.LAYERS:
            self._draw_layer(p, x0, x1, cx, cy, mh, color, alpha_deg,
                             direction, n_circ)

        # Left end cap
        p.setBrush(QBrush(QColor(50, 55, 60)))
        p.setPen(QPen(QColor(40, 45, 50), 1))
        p.drawEllipse(x0 - ea, cy - mh // 2, ea * 2, mh)

        # Right end cap
        grad2 = QLinearGradient(x1 - ea, cy, x1 + ea, cy)
        grad2.setColorAt(0.0, QColor(85, 90, 95))
        grad2.setColorAt(1.0, QColor(50, 55, 60))
        p.setBrush(QBrush(grad2))
        p.setPen(QPen(QColor(65, 70, 75), 1))
        p.drawEllipse(x1 - ea, cy - mh // 2, ea * 2, mh)

        # Axis label
        p.setPen(QColor(70, 80, 90))
        p.setFont(QFont("Consolas", 8))
        p.drawText(8, H - 8, "α=55°  3-layer helical  R=75 mm  L=300 mm")

    def _draw_layer(self, p, x0, x1, cx, cy, mh,
                    color, alpha_deg, direction, n_circ):
        R_s  = mh / 2.0 * 0.68
        N    = 180
        ph   = direction * self._phase
        prev_pt  = None
        prev_cos = None

        for i in range(N + 1):
            t     = i / N
            sx    = x0 + t * (x1 - x0)
            theta = direction * n_circ * 2 * math.pi * t + ph
            fy    = math.sin(theta)
            fc    = math.cos(theta)
            sy    = cy + fy * R_s

            fade  = min(t * 10, (1 - t) * 10, 1.0)
            depth = 0.18 + 0.82 * max(0.0, fc)
            alpha = int(255 * fade * depth)

            qc = QColor(color)
            qc.setAlpha(alpha)
            pen = QPen(qc, 1.4)
            p.setPen(pen)

            pt = QPoint(int(sx), int(sy))
            if (prev_pt is not None and
                    abs(sy - prev_pt.y()) < R_s * 1.5 and
                    # don't draw line that crosses "behind" the cylinder edge
                    not (prev_cos is not None and
                         prev_cos > 0.1 and fc < -0.1)):
                p.drawLine(prev_pt, pt)
            prev_pt  = pt
            prev_cos = fc


# ─────────────────────────────────────────────────────────────────────────────
# G-code dialog
# ─────────────────────────────────────────────────────────────────────────────
class GCodeDialog(QDialog):
    def __init__(self, gcode_text: str, stats: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Generated G-code")
        self.setMinimumSize(680, 520)
        self._text = gcode_text
        self._build(gcode_text, stats)

    def _build(self, gcode_text, stats):
        layout = QVBoxLayout(self)

        stats_lbl = QLabel(stats)
        stats_lbl.setStyleSheet("color:#a0a8b0; font:10pt Consolas;")
        layout.addWidget(stats_lbl)

        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(gcode_text)
        editor.setFont(QFont("Consolas", 9))
        editor.setStyleSheet(
            "background:#0d1117; color:#e8eaed; border:1px solid #3a4048;")
        layout.addWidget(editor)

        btn_row = QHBoxLayout()
        copy_btn = QPushButton("Copy to Clipboard")
        copy_btn.clicked.connect(
            lambda: QApplication.clipboard().setText(self._text))
        save_btn = QPushButton("Save to File…")
        save_btn.setProperty("role", "primary")
        save_btn.clicked.connect(self._save)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(copy_btn)
        btn_row.addWidget(save_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save G-code", "winding_program.nc",
            "G-code files (*.nc *.gcode *.txt);;All files (*)")
        if path:
            Path(path).write_text(self._text, encoding='utf-8')
            QMessageBox.information(self, "Saved", f"G-code saved to:\n{path}")


# ─────────────────────────────────────────────────────────────────────────────
# Port picker (hardware mode)
# ─────────────────────────────────────────────────────────────────────────────
class PortPickerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connect to Hardware")
        self.setFixedSize(380, 180)
        self.port = None
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select ESP32 COM port:"))

        self._combo = QComboBox()
        self._refresh_ports()
        layout.addWidget(self._combo)

        refresh_btn = QPushButton("Refresh port list")
        refresh_btn.clicked.connect(self._refresh_ports)
        layout.addWidget(refresh_btn)

        note = QLabel(
            "Baud rate: 921600 (fixed)\n"
            "USB driver: CP210x or CH340 required")
        note.setStyleSheet("color:#a0a8b0; font-size:9pt;")
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _refresh_ports(self):
        self._combo.clear()
        import glob
        candidates = (
            glob.glob('/dev/ttyUSB*') +
            glob.glob('/dev/ttyACM*') +
            [f'COM{i}' for i in range(1, 21)]
        )
        # On Windows use serial.tools.list_ports if available
        try:
            import serial.tools.list_ports
            candidates = [p.device for p in
                          serial.tools.list_ports.comports()]
        except ImportError:
            pass
        for c in (candidates or ['/dev/ttyUSB0', 'COM3']):
            self._combo.addItem(c)

    def _on_ok(self):
        self.port = self._combo.currentText()
        self.accept()


# ─────────────────────────────────────────────────────────────────────────────
# Launcher window
# ─────────────────────────────────────────────────────────────────────────────
_BTN_STYLE = """
QPushButton {{
    background-color: {bg};
    color: {fg};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 14px 20px;
    font-size: 13pt;
    font-weight: bold;
    text-align: left;
}}
QPushButton:hover {{
    background-color: {hover};
    border-color: {fg};
}}
QPushButton:pressed {{
    background-color: {bg};
}}
"""

def _btn_css(bg, fg='#e8eaed', border=None, hover=None):
    return _BTN_STYLE.format(
        bg=bg, fg=fg,
        border=border or bg,
        hover=hover or bg)


class LauncherWindow(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Filament Winding CAM")
        self.setMinimumSize(960, 560)
        self.setStyleSheet("background-color:#1e2228; color:#e8eaed;")
        self._main_window = None
        self._build()

    # ── Layout ────────────────────────────────────────────────────────────────
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header bar
        header = QFrame()
        header.setFixedHeight(60)
        header.setStyleSheet("background:#151a20; border-bottom:1px solid #3a4048;")
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(24, 0, 24, 0)
        title_lbl = QLabel("⚙  Filament Winding CAM")
        title_lbl.setStyleSheet("font-size:18pt; font-weight:bold; color:#5dade2;")
        subtitle = QLabel("Advanced Composite Manufacturing Platform")
        subtitle.setStyleSheet("font-size:10pt; color:#a0a8b0;")
        hlay.addWidget(title_lbl)
        hlay.addSpacing(16)
        hlay.addWidget(subtitle)
        hlay.addStretch()
        ver_lbl = QLabel("v1.0")
        ver_lbl.setStyleSheet("color:#5a6068; font-size:9pt;")
        hlay.addWidget(ver_lbl)
        root.addWidget(header)

        # Body
        body = QHBoxLayout()
        body.setContentsMargins(24, 20, 24, 16)
        body.setSpacing(24)
        root.addLayout(body, stretch=1)

        # Left: preview
        preview_frame = QFrame()
        preview_frame.setStyleSheet(
            "background:#151a20; border:1px solid #3a4048; border-radius:8px;")
        pf_lay = QVBoxLayout(preview_frame)
        pf_lay.setContentsMargins(8, 8, 8, 8)
        preview_title = QLabel("Live Path Preview")
        preview_title.setStyleSheet("color:#a0a8b0; font-size:9pt;")
        pf_lay.addWidget(preview_title)
        self._preview = WindingPreviewWidget()
        pf_lay.addWidget(self._preview, stretch=1)
        body.addWidget(preview_frame, stretch=3)

        # Right: buttons
        right_col = QVBoxLayout()
        right_col.setSpacing(12)

        mode_lbl = QLabel("Select Mode")
        mode_lbl.setStyleSheet(
            "font-size:11pt; font-weight:bold; color:#a0a8b0;"
            "border-bottom:1px solid #3a4048; padding-bottom:6px;")
        right_col.addWidget(mode_lbl)

        sim_btn = QPushButton("  Simulation Mode\n  No hardware required")
        sim_btn.setStyleSheet(_btn_css('#1a3a5c', '#5dade2', '#2a5a8c', '#1e4470'))
        sim_btn.setMinimumHeight(72)
        sim_btn.clicked.connect(self._on_simulation)
        right_col.addWidget(sim_btn)

        hw_btn = QPushButton("  Hardware Mode\n  Connect real ESP32")
        hw_btn.setStyleSheet(_btn_css('#1a3a2a', '#5cb85c', '#2a5a3a', '#1e4030'))
        hw_btn.setMinimumHeight(72)
        hw_btn.clicked.connect(self._on_hardware)
        right_col.addWidget(hw_btn)

        proj_btn = QPushButton("  Open Last Project\n  Resume saved recipe")
        proj_btn.setStyleSheet(_btn_css('#2a3038', '#e8eaed', '#3a4048', '#323a44'))
        proj_btn.setMinimumHeight(72)
        proj_btn.clicked.connect(self._on_last_project)
        right_col.addWidget(proj_btn)

        gcode_btn = QPushButton("  Generate Sample G-code\n  Preview without opening app")
        gcode_btn.setStyleSheet(_btn_css('#3a2a10', '#f0ad4e', '#5a4010', '#4a3010'))
        gcode_btn.setMinimumHeight(72)
        gcode_btn.clicked.connect(self._on_gcode)
        right_col.addWidget(gcode_btn)

        right_col.addStretch()
        body.addLayout(right_col, stretch=2)

        # Status bar
        status_bar = QFrame()
        status_bar.setFixedHeight(28)
        status_bar.setStyleSheet(
            "background:#0f141a; border-top:1px solid #2a3038;")
        sb_lay = QHBoxLayout(status_bar)
        sb_lay.setContentsMargins(16, 0, 16, 0)
        py_ver = sys.version.split()[0]
        pyside_ver = PySide6.__version__
        self._status_lbl = QLabel(
            f"Python {py_ver}  ·  PySide6 {pyside_ver}  ·  Simulation mode ready")
        self._status_lbl.setStyleSheet("color:#5a6068; font-size:8pt;")
        sb_lay.addWidget(self._status_lbl)
        sb_lay.addStretch()
        root.addWidget(status_bar)

    # ── Button handlers ───────────────────────────────────────────────────────
    def _on_simulation(self):
        os.environ['FW_LINK_KIND'] = 'mock'
        self._launch_main()

    def _on_hardware(self):
        dlg = PortPickerDialog(self)
        self._apply_child_theme(dlg)
        if dlg.exec() != QDialog.Accepted or not dlg.port:
            return
        os.environ['FW_LINK_KIND'] = 'real'
        os.environ['FW_LINK_PORT'] = dlg.port
        os.environ['FW_LINK_BAUD'] = '921600'
        self._launch_main()

    def _on_last_project(self):
        try:
            from backend.persistence.recipe_db import RecipeDB
            db = RecipeDB(_DB_PATH)
            recipes = db.list_recipes()
            if not recipes:
                QMessageBox.information(
                    self, "No saved projects",
                    "No recipes found.\n\nSave a recipe in the Recipe Editor tab "
                    "first, then use this button to reopen it.")
                return
        except Exception:
            pass
        os.environ['FW_LINK_KIND'] = 'mock'
        os.environ.setdefault('OPEN_LAST_RECIPE', '1')
        self._launch_main(open_recipe_tab=True)

    def _on_gcode(self):
        try:
            from backend.core.winding_planner import WindingParams, generate_helical
            params = WindingParams(
                mandrel_R_mm=75.0, mandrel_L_mm=300.0,
                alpha_deg=55.0, n_layers=4,
                tow_width_mm=6.0, fiber_tension_N=15.0,
                feed_mm_s=80.0)
            prog = generate_helical(params)
        except Exception as e:
            QMessageBox.critical(self, "G-code error", str(e))
            return

        stats = (
            f"Circuits: {prog.n_circuits}  ·  "
            f"Total length: {prog.total_length_mm / 1000:.2f} m  ·  "
            f"Est. time: {prog.estimated_time_s / 60:.1f} min  ·  "
            f"Lines: {len(prog.lines)}"
        )
        dlg = GCodeDialog('\n'.join(prog.lines), stats, self)
        self._apply_child_theme(dlg)
        dlg.exec()

    def _apply_child_theme(self, dlg):
        dlg.setStyleSheet(
            "background:#1e2228; color:#e8eaed;"
            "QComboBox{background:#2a3038; border:1px solid #3a4048;}"
            "QPushButton{background:#2a3038; color:#e8eaed;"
            "  border:1px solid #3a4048; border-radius:4px; padding:6px 12px;}"
            "QPushButton:hover{background:#323a44;}"
        )

    def _launch_main(self, open_recipe_tab: bool = False):
        """Hide launcher and open the main FilamentWindingApp window."""
        self.hide()
        try:
            from app.main_window import FilamentWindingApp
            from app.link_factory import LinkConfig, make_link

            cfg  = LinkConfig.from_env()
            link = make_link(cfg)

            self._main_window = FilamentWindingApp(link=link)
            if open_recipe_tab:
                # Switch to Recipe Editor tab (index 4)
                self._main_window._tabs.setCurrentIndex(4)
            self._main_window.show()
            # When main window closes, close the whole app
            self._main_window.destroyed.connect(QApplication.quit)
        except Exception as e:
            self.show()
            QMessageBox.critical(self, "Launch failed",
                f"Could not start application:\n\n{e}\n\n"
                "Check TROUBLESHOOTING.md for common fixes.")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Filament Winding CAM")
    app.setOrganizationName("FilamentWinding")
    app.setStyle("Fusion")

    # Apply base dark palette
    from PySide6.QtGui import QPalette
    palette = QPalette()
    palette.setColor(QPalette.Window,          QColor(30, 34, 40))
    palette.setColor(QPalette.WindowText,      QColor(232, 234, 237))
    palette.setColor(QPalette.Base,            QColor(21, 26, 32))
    palette.setColor(QPalette.AlternateBase,   QColor(37, 42, 50))
    palette.setColor(QPalette.Text,            QColor(232, 234, 237))
    palette.setColor(QPalette.Button,          QColor(42, 48, 56))
    palette.setColor(QPalette.ButtonText,      QColor(232, 234, 237))
    palette.setColor(QPalette.Highlight,       QColor(93, 173, 226))
    palette.setColor(QPalette.HighlightedText, QColor(15, 20, 25))
    app.setPalette(palette)

    window = LauncherWindow()
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
