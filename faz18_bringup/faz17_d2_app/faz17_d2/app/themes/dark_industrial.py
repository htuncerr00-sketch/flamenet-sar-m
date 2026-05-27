"""
themes/dark_industrial.py — Dark Industrial HMI Theme
========================================================
Production-grade HMI: high contrast, color-coded status, no eye strain
under fluorescent shop lighting. WCAG AA contrast minimums.

Color philosophy:
  Backgrounds: warm dark gray (not pure black — reduces flicker fatigue)
  Primary text: off-white
  Accent: cyan (information), amber (warning), red (critical), green (OK)
  Borders: subtle dark blue-gray
"""

# Color palette — single source of truth
COLOR = {
    # Backgrounds
    "bg_window":      "#1e2228",   # main window
    "bg_panel":       "#252a32",   # panel background
    "bg_widget":      "#2a3038",   # input/widget bg
    "bg_hover":       "#323a44",
    "bg_selected":    "#2a4d6e",
    # Text
    "text_primary":   "#e8eaed",
    "text_secondary": "#a0a8b0",
    "text_disabled":  "#5a6068",
    # Borders
    "border":         "#3a4048",
    "border_focus":   "#4a90e2",
    # Status colors
    "ok":             "#5cb85c",
    "info":           "#5bc0de",
    "warn":           "#f0ad4e",
    "crit":           "#d9534f",
    "fatal":          "#9e2a2b",
    # Accents
    "accent":         "#5dade2",
    "accent_bright":  "#85c1e9",
    # Charts
    "chart_grid":     "#3a4048",
    "chart_tension":  "#5cb85c",
    "chart_rpm":      "#5dade2",
    "chart_temp":     "#f0ad4e",
    "chart_vib":      "#bb86fc",
    "chart_twin":     "#7a7a7a",
}


def stylesheet() -> str:
    """Returns full Qt stylesheet (QSS) for the application."""
    c = COLOR
    return f"""
/* ── Global ── */
QWidget {{
    background-color: {c['bg_window']};
    color: {c['text_primary']};
    font-family: "Segoe UI", "Helvetica", "DejaVu Sans", sans-serif;
    font-size: 10pt;
}}

QMainWindow, QDialog {{
    background-color: {c['bg_window']};
}}

/* ── Frames / Panels ── */
QFrame, QGroupBox {{
    background-color: {c['bg_panel']};
    border: 1px solid {c['border']};
    border-radius: 4px;
}}

QGroupBox {{
    margin-top: 14px;
    padding-top: 10px;
    font-weight: 600;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 6px;
    color: {c['text_secondary']};
    background-color: {c['bg_panel']};
}}

/* ── Buttons ── */
QPushButton {{
    background-color: {c['bg_widget']};
    border: 1px solid {c['border']};
    border-radius: 3px;
    padding: 6px 14px;
    color: {c['text_primary']};
    min-height: 22px;
}}
QPushButton:hover {{
    background-color: {c['bg_hover']};
    border-color: {c['accent']};
}}
QPushButton:pressed {{
    background-color: {c['bg_selected']};
}}
QPushButton:disabled {{
    color: {c['text_disabled']};
    background-color: {c['bg_panel']};
}}
QPushButton[role="primary"] {{
    background-color: {c['accent']};
    color: #fff;
    border-color: {c['accent']};
}}
QPushButton[role="primary"]:hover {{
    background-color: {c['accent_bright']};
}}
QPushButton[role="danger"] {{
    background-color: {c['crit']};
    color: #fff;
    border-color: {c['crit']};
    font-weight: 700;
}}
QPushButton[role="danger"]:hover {{
    background-color: #e06762;
}}
QPushButton[role="success"] {{
    background-color: {c['ok']};
    color: #fff;
    border-color: {c['ok']};
}}

/* ── Inputs ── */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background-color: {c['bg_widget']};
    border: 1px solid {c['border']};
    border-radius: 3px;
    padding: 4px 6px;
    color: {c['text_primary']};
    selection-background-color: {c['bg_selected']};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QComboBox:focus, QPlainTextEdit:focus {{
    border-color: {c['border_focus']};
}}
QLineEdit:disabled {{
    color: {c['text_disabled']};
}}

QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox::down-arrow {{
    image: none; border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {c['text_secondary']};
    margin-right: 4px;
}}
QComboBox QAbstractItemView {{
    background: {c['bg_widget']};
    border: 1px solid {c['border']};
    selection-background-color: {c['bg_selected']};
}}

/* ── Labels ── */
QLabel {{
    background: transparent;
    border: none;
}}
QLabel[role="header"] {{
    font-size: 13pt;
    font-weight: 600;
    color: {c['accent']};
    padding-bottom: 4px;
}}
QLabel[role="metric"] {{
    font-size: 18pt;
    font-weight: 700;
    color: {c['text_primary']};
}}
QLabel[role="metric-large"] {{
    font-size: 24pt;
    font-weight: 700;
    color: {c['text_primary']};
}}
QLabel[role="unit"] {{
    font-size: 10pt;
    color: {c['text_secondary']};
}}
QLabel[role="caption"] {{
    color: {c['text_secondary']};
    font-size: 9pt;
}}
QLabel[status="ok"]    {{ color: {c['ok']};   font-weight: 600; }}
QLabel[status="info"]  {{ color: {c['info']}; font-weight: 600; }}
QLabel[status="warn"]  {{ color: {c['warn']}; font-weight: 600; }}
QLabel[status="crit"]  {{ color: {c['crit']}; font-weight: 700; }}
QLabel[status="fatal"] {{ color: {c['fatal']};font-weight: 700; }}

/* ── Tabs / Docks ── */
QTabWidget::pane {{
    background-color: {c['bg_panel']};
    border: 1px solid {c['border']};
    border-top: none;
}}
QTabBar::tab {{
    background-color: {c['bg_widget']};
    color: {c['text_secondary']};
    padding: 8px 16px;
    border: 1px solid {c['border']};
    border-bottom: none;
}}
QTabBar::tab:selected {{
    background-color: {c['bg_panel']};
    color: {c['accent']};
    border-bottom: 2px solid {c['accent']};
}}
QTabBar::tab:hover:!selected {{
    background-color: {c['bg_hover']};
}}

QDockWidget {{
    color: {c['text_primary']};
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
}}
QDockWidget::title {{
    background-color: {c['bg_widget']};
    padding: 6px;
    border: 1px solid {c['border']};
}}

/* ── Tables ── */
QTableView, QTreeView, QListView {{
    background-color: {c['bg_widget']};
    alternate-background-color: {c['bg_panel']};
    border: 1px solid {c['border']};
    selection-background-color: {c['bg_selected']};
    selection-color: {c['text_primary']};
    gridline-color: {c['border']};
}}
QHeaderView::section {{
    background-color: {c['bg_panel']};
    color: {c['text_secondary']};
    padding: 6px;
    border: none;
    border-right: 1px solid {c['border']};
    border-bottom: 1px solid {c['border']};
    font-weight: 600;
}}

/* ── Scrollbars ── */
QScrollBar:vertical {{
    background: {c['bg_window']};
    width: 12px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {c['bg_hover']};
    border-radius: 3px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {c['border_focus']};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    border: none; background: none; height: 0;
}}
QScrollBar:horizontal {{
    background: {c['bg_window']};
    height: 12px;
}}
QScrollBar::handle:horizontal {{
    background: {c['bg_hover']};
    border-radius: 3px;
    min-width: 30px;
}}

/* ── Progress bars ── */
QProgressBar {{
    background-color: {c['bg_widget']};
    border: 1px solid {c['border']};
    border-radius: 3px;
    text-align: center;
    color: {c['text_primary']};
}}
QProgressBar::chunk {{
    background-color: {c['accent']};
    border-radius: 2px;
}}

/* ── Sliders ── */
QSlider::groove:horizontal {{
    background: {c['bg_widget']};
    height: 6px;
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {c['accent']};
    width: 16px;
    margin: -5px 0;
    border-radius: 8px;
}}
QSlider::handle:horizontal:hover {{
    background: {c['accent_bright']};
}}
QSlider::sub-page:horizontal {{
    background: {c['accent']};
    border-radius: 3px;
}}

/* ── Menus ── */
QMenuBar {{
    background-color: {c['bg_panel']};
    border-bottom: 1px solid {c['border']};
}}
QMenuBar::item {{
    padding: 6px 12px;
    background: transparent;
}}
QMenuBar::item:selected {{
    background-color: {c['bg_hover']};
}}
QMenu {{
    background-color: {c['bg_panel']};
    border: 1px solid {c['border']};
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 24px;
}}
QMenu::item:selected {{
    background-color: {c['bg_selected']};
}}
QMenu::separator {{
    height: 1px;
    background: {c['border']};
    margin: 4px 8px;
}}

/* ── Toolbar ── */
QToolBar {{
    background-color: {c['bg_panel']};
    border-bottom: 1px solid {c['border']};
    spacing: 4px;
    padding: 4px;
}}
QToolButton {{
    background-color: transparent;
    color: {c['text_primary']};
    border: 1px solid transparent;
    padding: 6px 10px;
    border-radius: 3px;
}}
QToolButton:hover {{
    background-color: {c['bg_hover']};
    border-color: {c['border']};
}}
QToolButton:checked {{
    background-color: {c['bg_selected']};
    border-color: {c['accent']};
}}

/* ── Status bar ── */
QStatusBar {{
    background-color: {c['bg_panel']};
    border-top: 1px solid {c['border']};
    color: {c['text_secondary']};
}}
QStatusBar::item {{ border: none; }}

/* ── Checkboxes / Radio ── */
QCheckBox, QRadioButton {{ background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px;
}}
QCheckBox::indicator:unchecked {{
    border: 1px solid {c['border']};
    background: {c['bg_widget']};
    border-radius: 2px;
}}
QCheckBox::indicator:checked {{
    border: 1px solid {c['accent']};
    background: {c['accent']};
    border-radius: 2px;
}}

/* ── Splitter ── */
QSplitter::handle {{
    background: {c['border']};
}}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical   {{ height: 1px; }}
"""


def chart_pen_colors():
    """Color tuples for pyqtgraph plot pens."""
    return {
        "tension": COLOR["chart_tension"],
        "rpm":     COLOR["chart_rpm"],
        "temp":    COLOR["chart_temp"],
        "vib":     COLOR["chart_vib"],
        "twin":    COLOR["chart_twin"],
    }
