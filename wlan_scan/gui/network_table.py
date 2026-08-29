"""Network table widget — sortable, color-coded list of all discovered networks."""

from PySide6.QtCore import Qt, QTimer, Signal, QPoint
from PySide6.QtGui import QColor, QFont, QBrush, QAction
from PySide6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QWidget, QVBoxLayout, QMenu,
)

from ..models import Network, BSSEntry
from .styles import signal_color, signal_label


class FilterHeaderView(QHeaderView):
    """Horizontal header with right-click column filter menus."""

    filter_applied = Signal(int, str)  # col_index, filter_value ("" = clear)

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._active_filters: dict[int, str] = {}
        self._column_values: dict[int, list[str]] = {}
        self.setSectionsClickable(True)

    def set_column_values(self, col: int, values: list[str]):
        """Update the set of unique values shown in the filter menu."""
        self._column_values[col] = sorted(set(v for v in values if v))

    def active_filters(self) -> dict[int, str]:
        return dict(self._active_filters)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            idx = self.logicalIndexAt(event.pos())
            if idx >= 0:
                self._show_filter_menu(idx, event.globalPosition().toPoint())
                return
        super().mousePressEvent(event)

    def _show_filter_menu(self, col: int, pos: QPoint):
        menu = QMenu(self)
        values = self._column_values.get(col, [])
        active = self._active_filters.get(col, "")

        all_action = menu.addAction("— Show All —")
        all_action.setCheckable(True)
        all_action.setChecked(not active)
        all_action.setFont(QFont(all_action.font().family(), -1, QFont.Weight.Bold))

        if values:
            menu.addSeparator()
            for val in values[:40]:
                act = menu.addAction(val)
                act.setCheckable(True)
                act.setChecked(val == active)

        chosen = menu.exec(pos)
        if chosen is None:
            return
        if chosen is all_action:
            self._active_filters.pop(col, None)
            self.filter_applied.emit(col, "")
        elif chosen.text() in values:
            self._active_filters[col] = chosen.text()
            self.filter_applied.emit(col, chosen.text())


class NetworkTableWidget(QWidget):
    """Compound widget: filter-able table of Wi-Fi networks."""

    filters_changed = Signal(dict)  # {col_name: filter_value}
    selection_changed = Signal()

    COLUMNS = [
        "SSID", "BSSID", "RSSI", "Signal",
        "Band", "Channel", "Width", "Security",
        "PHY", "Vendor", "Utilization", "Clients",
    ]

    # Columns offering a right-click filter menu
    HEADER_FILTER_COLS = {0, 1, 4, 5, 7, 8, 9}  # SSID, BSSID, Band, Ch, Sec, PHY, Vendor

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bssids: dict[str, int] = {}
        self._block_selection = False
        self._connected_bssid = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._table = QTableWidget()
        layout.addWidget(self._table)
        self.setup_ui()

    def setup_ui(self):
        t = self._table
        t.setColumnCount(len(self.COLUMNS))
        t.setHorizontalHeaderLabels(self.COLUMNS)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        t.setAlternatingRowColors(True)
        t.setSortingEnabled(True)
        t.setShowGrid(True)
        t.verticalHeader().setVisible(False)
        t.setFont(QFont("Segoe UI", 10))

        # Custom header with filter menus
        self._header = FilterHeaderView()
        t.setHorizontalHeader(self._header)
        self._header.filter_applied.connect(self._on_header_filter)

        # Column sizing
        self._header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, len(self.COLUMNS)):
            self._header.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)

        t.itemSelectionChanged.connect(self._on_selection_changed)

    def _on_selection_changed(self):
        self.selection_changed.emit()

    def _on_header_filter(self, col_idx: int, value: str):
        """Rebuild filter dict from header active filters, emit."""
        filters = {}
        for c, v in self._header.active_filters().items():
            if v and c < len(self.COLUMNS):
                filters[self.COLUMNS[c]] = v
        self.filters_changed.emit(filters)

    def populate(self, networks: list[Network], connected_bssid: str = ""):
        """Update table in-place — preserves scroll position and selection."""
        t = self._table
        t.setSortingEnabled(False)
        selected_bssid = self._current_selected_bssid()
        self._connected_bssid = (connected_bssid or "").upper()

        new_entries: list[tuple[str, str, BSSEntry]] = []
        for net in networks:
            for bss in net.bss_list:
                new_entries.append((net.ssid or "<Hidden>", bss.bssid, bss))

        new_bssids = {e[1] for e in new_entries}
        old_bssids = set(self._bssids.keys())

        removed = old_bssids - new_bssids
        if removed:
            self._block_selection = True
            for bssid in sorted(removed, key=lambda b: self._bssids[b], reverse=True):
                row = self._bssids.pop(bssid)
                t.removeRow(row)
                for bid, r in list(self._bssids.items()):
                    if r > row:
                        self._bssids[bid] = r - 1
            self._block_selection = False

        added = new_bssids - old_bssids
        for bssid in added:
            row = t.rowCount()
            t.insertRow(row)
            self._bssids[bssid] = row

        self._block_selection = True
        for ssid, bssid, bss in new_entries:
            row = self._bssids.get(bssid)
            if row is None:
                continue

            rssi_color = self._rssi_color(bss.rssi)
            sec_color = self._security_color(bss.security)
            util_color = self._util_color(bss.channel_utilization)

            updates = [
                (
                    ("● " + ssid) if bssid == self._connected_bssid else ssid,
                    None,
                ),
                (bssid, None),
                (str(bss.rssi), rssi_color),
                (signal_label(bss.rssi), rssi_color),
                (bss.band, None),
                (str(bss.channel), None),
                (f"{bss.channel_width} MHz" if bss.channel_width > 0 else "—", None),
                (bss.security, sec_color),
                (bss.phy_type, None),
                (bss.vendor or "", None),
                (
                    f"{bss.channel_utilization:.0f}%"
                    if bss.channel_utilization > 0
                    else "—",
                    util_color,
                ),
                (str(bss.station_count) if bss.station_count > 0 else "—", None),
            ]

            for col, (text, color) in enumerate(updates):
                item = t.item(row, col)
                if item is None:
                    item = QTableWidgetItem()
                    t.setItem(row, col, item)
                item.setText(text)
                item.setData(Qt.ItemDataRole.UserRole, bss)
                if color:
                    item.setForeground(QBrush(color))
                else:
                    item.setData(Qt.ItemDataRole.ForegroundRole, None)

                # Connected BSSID: bold the SSID cell + tooltip
                if col == 0 and bssid == self._connected_bssid:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                    item.setToolTip("Connected AP (this machine is associated)")
                elif col == 0:
                    item.setToolTip("")

        self._block_selection = False
        t.setSortingEnabled(True)

        if selected_bssid and selected_bssid in self._bssids:
            t.selectRow(self._bssids[selected_bssid])
        elif t.rowCount() > 0:
            t.selectRow(0)

        # Refresh header filter menus
        self._update_header_filters(new_entries)

    def _update_header_filters(self, entries: list[tuple[str, str, BSSEntry]]):
        """Update unique values for each filterable column."""
        col_map: dict[int, set[str]] = {}
        for ssid, bssid, bss in entries:
            row_data = [
                ssid, bssid, str(bss.rssi), signal_label(bss.rssi),
                bss.band, str(bss.channel),
                f"{bss.channel_width} MHz" if bss.channel_width > 0 else "—",
                bss.security, bss.phy_type, bss.vendor or "",
            ]
            for c in self.HEADER_FILTER_COLS:
                if c < len(row_data):
                    col_map.setdefault(c, set()).add(row_data[c])

        for c, vals in col_map.items():
            self._header.set_column_values(c, list(vals))

    def _current_selected_bssid(self) -> str | None:
        bss = self.get_selected_bss()
        return bss.bssid if bss else None

    def get_selected_bss(self):
        t = self._table
        selected = t.selectedItems()
        if not selected:
            return None
        row = selected[0].row()
        item = t.item(row, 0)
        if item:
            return item.data(Qt.ItemDataRole.UserRole)
        return None

    @staticmethod
    def _rssi_color(rssi: int) -> QColor:
        return QColor(signal_color(rssi))

    @staticmethod
    def _security_color(sec: str) -> QColor:
        if "WPA3" in sec or "SAE" in sec:
            return QColor("#10b981")
        elif "WPA2" in sec:
            return QColor("#22c55e")
        elif "WPA" in sec:
            return QColor("#f59e0b")
        elif "WEP" in sec:
            return QColor("#ef4444")
        elif sec == "Open":
            return QColor("#ef4444")
        return QColor("#e0e0e0")

    @staticmethod
    def _util_color(util: float) -> QColor:
        if util > 70:
            return QColor("#ef4444")
        elif util > 40:
            return QColor("#f59e0b")
        elif util > 0:
            return QColor("#22c55e")
        return QColor("#e0e0e0")
