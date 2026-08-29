"""Stylesheet and theming for WLAN Scan."""

DARK_THEME = """
/* ── Global ── */
QMainWindow {
    background-color: #0f1117;
}
QWidget {
    background-color: #0f1117;
    color: #e0e0e0;
    font-family: "Segoe UI", "SF Pro Display", "Helvetica Neue", sans-serif;
    font-size: 13px;
}

/* ── Menu bar ── */
QMenuBar {
    background-color: #161822;
    color: #c0c0c0;
    border-bottom: 1px solid #252836;
    padding: 2px 0;
}
QMenuBar::item:selected {
    background-color: #252836;
    border-radius: 4px;
}
QMenu {
    background-color: #1a1d2e;
    border: 1px solid #2a2d3e;
    border-radius: 6px;
    padding: 4px;
}
QMenu::item {
    padding: 6px 28px 6px 12px;
    border-radius: 4px;
}
QMenu::item:selected {
    background-color: #3b82f6;
}

/* ── Toolbar ── */
QToolBar {
    background-color: #161822;
    border-bottom: 1px solid #252836;
    spacing: 4px;
    padding: 4px;
}
QToolButton {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 6px 12px;
    color: #c0c0c0;
}
QToolButton:hover {
    background-color: #252836;
    border-color: #3b82f6;
    color: #ffffff;
}
QToolButton:pressed {
    background-color: #1e2a4a;
}
QToolButton:checked {
    background-color: #3b82f6;
    color: white;
}

/* ── Status bar ── */
QStatusBar {
    background-color: #161822;
    color: #808080;
    border-top: 1px solid #252836;
    font-size: 12px;
}

/* ── Table views ── */
QTableView, QTableWidget {
    background-color: #13151f;
    alternate-background-color: #181b26;
    border: 1px solid #252836;
    border-radius: 8px;
    gridline-color: #1e2130;
    selection-background-color: #1e3a5f;
    selection-color: #ffffff;
}
QHeaderView::section {
    background-color: #1a1d2e;
    color: #909090;
    border: none;
    border-right: 1px solid #252836;
    border-bottom: 2px solid #3b82f6;
    padding: 8px 12px;
    font-weight: 600;
    font-size: 12px;
    text-transform: uppercase;
}
QTableCornerButton::section {
    background-color: #1a1d2e;
    border-bottom: 2px solid #3b82f6;
}

/* ── Scrollbars ── */
QScrollBar:vertical {
    background: #0f1117;
    width: 8px;
    border-radius: 4px;
}
QScrollBar::handle:vertical {
    background: #353848;
    border-radius: 4px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: #4a4d60;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QScrollBar:horizontal {
    background: #0f1117;
    height: 8px;
    border-radius: 4px;
}
QScrollBar::handle:horizontal {
    background: #353848;
    border-radius: 4px;
    min-width: 30px;
}
QScrollBar::handle:horizontal:hover {
    background: #4a4d60;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}

/* ── Splitter ── */
QSplitter::handle {
    background-color: #252836;
    width: 2px;
    height: 2px;
}

/* ── Labels ── */
QLabel {
    color: #e0e0e0;
}

/* ── Group boxes ── */
QGroupBox {
    border: 1px solid #252836;
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 16px;
    font-weight: 600;
    color: #a0a0a0;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 8px;
}

/* ── Buttons ── */
QPushButton {
    background-color: #252836;
    border: 1px solid #353848;
    border-radius: 6px;
    padding: 6px 16px;
    color: #e0e0e0;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #2e3144;
    border-color: #3b82f6;
}
QPushButton:pressed {
    background-color: #1e2a4a;
}
QPushButton:disabled {
    background-color: #1a1d2e;
    color: #606060;
}

/* ── Primary button ── */
QPushButton#scanButton {
    background-color: #3b82f6;
    border-color: #3b82f6;
    color: white;
    font-weight: 600;
    font-size: 14px;
    padding: 10px 24px;
}
QPushButton#scanButton:hover {
    background-color: #2563eb;
    border-color: #2563eb;
}
QPushButton#scanButton:pressed {
    background-color: #1d4ed8;
}

/* ── Checkboxes ── */
QCheckBox {
    spacing: 8px;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 2px solid #505060;
    background-color: transparent;
}
QCheckBox::indicator:checked {
    background-color: #3b82f6;
    border-color: #3b82f6;
}
QCheckBox::indicator:hover {
    border-color: #3b82f6;
}

/* ── Band tabs ── */
QTabWidget::pane {
    border: 1px solid #252836;
    border-radius: 8px;
    background-color: #13151f;
}
QTabBar::tab {
    background-color: #1a1d2e;
    border: 1px solid #252836;
    padding: 8px 20px;
    margin-right: 2px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    color: #808080;
}
QTabBar::tab:selected {
    background-color: #13151f;
    border-bottom-color: #13151f;
    color: #3b82f6;
    font-weight: 600;
}
QTabBar::tab:hover:!selected {
    background-color: #252836;
    color: #c0c0c0;
}

/* ── Signal meter colors (used in code) ── */
/* Excellent: #10b981 (green) */
/* Good:      #22c55e (light green) */
/* Fair:      #f59e0b (amber) */
/* Weak:      #f97316 (orange) */
/* Poor:      #ef4444 (red) */

/* ── Chart styling ── */
QChart {
    background-color: #13151f;
}
"""

# Band colors for visualization
BAND_COLORS = {
    "2.4 GHz": "#f59e0b",  # amber
    "5 GHz": "#3b82f6",    # blue
    "6 GHz": "#8b5cf6",    # purple
}

# Signal strength colors
SIGNAL_COLORS = [
    (-30, "#10b981"),   # excellent (green)
    (-50, "#22c55e"),   # good (light green)
    (-60, "#f59e0b"),   # fair (amber)
    (-70, "#f97316"),   # weak (orange)
    (-100, "#ef4444"),  # poor (red)
]


def signal_color(rssi: int) -> str:
    """Get color for a given RSSI value."""
    for threshold, color in SIGNAL_COLORS:
        if rssi >= threshold:
            return color
    return SIGNAL_COLORS[-1][1]


def signal_label(rssi: int) -> str:
    """Get human-readable signal label."""
    if rssi >= -50:
        return "Excellent"
    elif rssi >= -60:
        return "Good"
    elif rssi >= -70:
        return "Fair"
    elif rssi >= -80:
        return "Weak"
    return "Poor"
