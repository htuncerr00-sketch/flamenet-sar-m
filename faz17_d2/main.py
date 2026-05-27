"""
app/main.py — Application Entry Point
========================================
Bootstraps QApplication, applies dark industrial theme, instantiates
FilamentWindingApp main window, installs SIGINT handler for clean
shutdown on Ctrl-C, runs Qt event loop.

Run:
    python -m app.main
or:
    cd faz17_d2 && python app/main.py
"""
from __future__ import annotations
import os
import signal
import sys
from pathlib import Path

# Make sure relative imports work regardless of invocation style
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_ROOT))

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication


def _install_signal_handlers(app: QApplication) -> None:
    """Make Ctrl-C in terminal quit the Qt app cleanly."""
    # SIGINT (Ctrl-C) → app.quit
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    # Keep Python interpreter responsive so signals are processed
    timer = QTimer()
    timer.start(200)
    timer.timeout.connect(lambda: None)
    # Hold the timer on the app so it isn't GC'd
    app._sigint_timer = timer  # type: ignore[attr-defined]


def _apply_theme(app: QApplication) -> None:
    from app.themes.dark_industrial import stylesheet
    app.setStyleSheet(stylesheet())
    # Slightly larger UI font for industrial readability
    font = QFont()
    font.setPointSize(10)
    app.setFont(font)


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv
    # High-DPI handling (Qt 6 enables automatically; this is just safety)
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication(argv)
    app.setApplicationName("Filament Winding Control")
    app.setOrganizationName("FaramentWinding")
    app.setOrganizationDomain("filament-winding.local")

    _apply_theme(app)
    _install_signal_handlers(app)

    # Import after QApplication exists (panels use Qt widgets)
    from app.main_window import FilamentWindingApp
    win = FilamentWindingApp()
    win.show()

    rc = app.exec()
    return rc


if __name__ == "__main__":
    sys.exit(main())
