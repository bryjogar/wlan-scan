"""Detail panel — shows expanded information for a selected network."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QScrollArea,
    QSizePolicy,
)

from ..models import BSSEntry
from .styles import signal_color, signal_label, BAND_COLORS


class DetailPanel(QWidget):
    """Right-side panel showing expanded BSS details."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(280)
        self.setMaximumWidth(380)

        self._content = QWidget()
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(16, 16, 16, 16)
        self._layout.setSpacing(8)

        self._title = QLabel("Select a network")
        self._title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        self._title.setWordWrap(True)
        self._title.setStyleSheet("color: #e0e0e0;")
        self._layout.addWidget(self._title)

        self._subtitle = QLabel("")
        self._subtitle.setFont(QFont("Segoe UI", 11))
        self._subtitle.setStyleSheet("color: #707080;")
        self._layout.addWidget(self._subtitle)

        self._layout.addWidget(self._divider())

        # Signal meter placeholder
        self._signal_section = QVBoxLayout()
        self._layout.addLayout(self._signal_section)

        # Fields
        self._fields_layout = QVBoxLayout()
        self._fields_layout.setSpacing(4)
        self._layout.addLayout(self._fields_layout)

        self._layout.addStretch()

        # Scroll
        scroll = QScrollArea()
        scroll.setWidget(self._content)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def show_bss(self, bss: BSSEntry):
        """Display details for a BSS entry."""
        # Clear previous fields
        while self._signal_section.count():
            item = self._signal_section.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        while self._fields_layout.count():
            item = self._fields_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

        if bss is None:
            self._title.setText("Select a network")
            self._subtitle.setText("")
            return

        ssid = bss.ssid or "Hidden Network"
        self._title.setText(ssid)
        self._subtitle.setText(bss.bssid)

        # ── Signal meter ──
        rssi = bss.rssi
        pct = min(100, max(0, int(2 * (rssi + 100))))
        color = signal_color(rssi)
        label = signal_label(rssi)

        signal_label_w = QLabel(f"{rssi} dBm · {label}")
        signal_label_w.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        signal_label_w.setStyleSheet(f"color: {color};")
        signal_label_w.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._signal_section.addWidget(signal_label_w)

        # Signal bar
        bar = QFrame()
        bar.setFixedHeight(8)
        bar.setStyleSheet(
            f"background-color: #252836; border-radius: 4px; "
            f"border: none;"
        )
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(0, 0, 0, 0)
        fill = QFrame()
        fill.setStyleSheet(
            f"background-color: {color}; border-radius: 4px; border: none;"
        )
        fill.setFixedSize(max(4, int(pct * 2.8)), 8)  # ~280px max
        bar_layout.addWidget(fill)
        bar_layout.addStretch()
        self._signal_section.addWidget(bar)

        pct_label = QLabel(f"Signal Strength: {pct}%")
        pct_label.setFont(QFont("Segoe UI", 10))
        pct_label.setStyleSheet("color: #808080;")
        pct_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._signal_section.addWidget(pct_label)

        self._signal_section.addWidget(self._divider())

        # ── Detail fields ──
        band_color = BAND_COLORS.get(bss.band, "#e0e0e0")
        fields = [
            ("Wi-Fi", bss.wifi_generation or _generation_from_phy(bss.phy_type),
             _gen_color(bss.wifi_generation)),
            ("Modes", bss.supported_modes or _modes_from_phy(bss.phy_type, bss.band), None),
            ("Band", bss.band, band_color),
            ("Channel", str(bss.channel), None),
            ("Channel Width", f"{bss.channel_width} MHz" if bss.channel_width > 0 else "—", None),
            ("PHY Mode", bss.phy_type, None),
            ("Security", bss.security,
             "#10b981" if "WPA" in (bss.security or "") else "#ef4444"),
            ("Cipher", bss.cipher or "—", None),
            ("Auth", bss.auth_algo or "—", None),
            ("Max Rate", f"{bss.max_rate:.0f} Mbps" if bss.max_rate > 0 else "—", None),
            ("Beacon Period", f"{bss.beacon_period} TU", None),
            ("Center Freq", f"{bss.center_frequency} MHz" if bss.center_frequency > 0 else "—", None),
            ("Vendor", bss.vendor or "Unknown", None),
        ]

        for name, value, color_override in fields:
            self._fields_layout.addWidget(self._field_row(name, value, color_override))

    def _field_row(self, name: str, value: str, color: str | None = None) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 2, 0, 2)

        name_lbl = QLabel(name)
        name_lbl.setFont(QFont("Segoe UI", 10))
        name_lbl.setStyleSheet("color: #707080;")

        value_lbl = QLabel(value)
        value_lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Medium))
        value_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        if color:
            value_lbl.setStyleSheet(f"color: {color};")
        else:
            value_lbl.setStyleSheet("color: #e0e0e0;")

        layout.addWidget(name_lbl)
        layout.addStretch()
        layout.addWidget(value_lbl)

        return row

    def _divider(self) -> QFrame:
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet("color: #252836; background-color: #252836;")
        div.setFixedHeight(1)
        return div

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())


# ── Helpers for backward compat / fallback ──

def _generation_from_phy(phy: str) -> str:
    """Fallback: derive Wi-Fi generation from PHY string."""
    phy = (phy or "").lower()
    if "ax" in phy or "802.11ax" in phy:
        return "Wi-Fi 6"
    if "ac" in phy:
        return "Wi-Fi 5"
    if "n" in phy:
        return "Wi-Fi 4"
    return "Legacy"


def _modes_from_phy(phy: str, band: str) -> str:
    """Fallback: derive supported modes from PHY and band."""
    is_5 = "5" in (band or "") or "6" in (band or "")
    phy = (phy or "").lower()
    if "ax" in phy:
        return "a/n/ac/ax" if is_5 else "b/g/n/ax"
    if "ac" in phy:
        return "a/n/ac"
    if "n" in phy:
        return "a/n" if is_5 else "b/g/n"
    return "a" if is_5 else "b/g"


def _gen_color(gen: str) -> str:
    if "6E" in gen:
        return "#a78bfa"
    if "6" in gen:
        return "#60a5fa"
    if "5" in gen:
        return "#34d399"
    if "4" in gen:
        return "#fbbf24"
    return "#9ca3af"
