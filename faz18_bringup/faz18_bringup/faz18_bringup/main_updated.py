"""
app/main.py — Application Entry Point
========================================
Bootstraps QApplication, applies dark industrial theme, instantiates
FilamentWindingApp main window, installs SIGINT handler for clean
shutdown on Ctrl-C, runs Qt event loop.

Run examples:
    python -m app.main                              # mock link (default)
    python -m app.main --link real                  # real link, default /dev/ttyUSB0
    python -m app.main --link real --port /dev/ttyUSB1 --baud 921600
    FW_LINK_KIND=real FW_LINK_PORT=COM3 python -m app.main      # via env vars

Environment variables are read by LinkConfig.from_env when no CLI args
override them.
"""
from __future__ import annotations
import argparse
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
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    timer = QTimer()
    timer.start(200)
    timer.timeout.connect(lambda: None)
    app._sigint_timer = timer  # type: ignore[attr-defined]


def _apply_theme(app: QApplication) -> None:
    from app.themes.dark_industrial import stylesheet
    app.setStyleSheet(stylesheet())
    font = QFont()
    font.setPointSize(10)
    app.setFont(font)


def _parse_args(argv):
    """argparse-based CLI for link selection."""
    parser = argparse.ArgumentParser(
        description="Filament Winding Desktop Control")
    parser.add_argument(
        "--link", choices=("mock", "real"), default=None,
        help="Backend link kind. If omitted, reads FW_LINK_KIND env "
             "or defaults to mock.")
    parser.add_argument(
        "--port", default=None,
        help="Serial port path (real link only). Default /dev/ttyUSB0 "
             "or FW_LINK_PORT env.")
    parser.add_argument(
        "--baud", type=int, default=None,
        help="Serial baud rate (real link only). Default 921600.")
    parser.add_argument(
        "--no-auto-reconnect", action="store_true",
        help="Disable auto-reconnect for real link.")
    parser.add_argument(
        "--watchdog-s", type=float, default=None,
        help="Real link watchdog period in seconds. Default 1.0.")
    # Filter Qt's own argv -- argparse can clash if Qt is consuming, but
    # we just parse known args and pass the full argv to QApplication.
    args, _qt_args = parser.parse_known_args(argv[1:])
    return args


def _link_config_from_args(args):
    """Build LinkConfig from CLI args, falling back to env then defaults."""
    from app.link_factory import LinkConfig
    cfg = LinkConfig.from_env()
    if args.link is not None:
        cfg.kind = args.link
    if args.port is not None:
        cfg.port = args.port
        # If --port given without --link, assume the user wants real
        if args.link is None:
            cfg.kind = "real"
    if args.baud is not None:
        cfg.baud = args.baud
    if args.no_auto_reconnect:
        cfg.auto_reconnect = False
    if args.watchdog_s is not None:
        cfg.watchdog_s = args.watchdog_s
    return cfg


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv
    args = _parse_args(argv)

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication(argv)
    app.setApplicationName("Filament Winding Control")
    app.setOrganizationName("FaramentWinding")
    app.setOrganizationDomain("filament-winding.local")

    _apply_theme(app)
    _install_signal_handlers(app)

    cfg = _link_config_from_args(args)
    print(f"[main] link={cfg.kind}  port={cfg.port}  baud={cfg.baud}  "
          f"watchdog={cfg.watchdog_s}s  auto_reconnect={cfg.auto_reconnect}",
          file=sys.stderr)

    from app.main_window import FilamentWindingApp
    win = FilamentWindingApp(link_config=cfg)
    win.show()

    rc = app.exec()
    return rc


if __name__ == "__main__":
    sys.exit(main())
