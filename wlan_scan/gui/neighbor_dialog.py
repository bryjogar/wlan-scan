"""Neighbor report — ARP scan and network device discovery.

Shows all devices responding on the local subnet to help gauge
whether client isolation is active on the Wi-Fi network.
Correlates ARP entries with known BSSIDs from the Wi-Fi scan.

On Windows, uses `arp -a` for cached entries and a threaded ping sweep
to discover all devices on the subnet.
"""

import ipaddress
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QFont, QColor, QBrush
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QProgressBar, QFrame, QAbstractItemView,
)

from ..vendor_lookup import lookup_vendor
from .styles import DARK_THEME

# Regex to extract local IP and subnet from ipconfig output
_IPCONFIG_IP_RE = re.compile(
    r"IPv4 Address[.\s]*:\s*([\d.]+)", re.IGNORECASE
)
_IPCONFIG_SUBNET_RE = re.compile(
    r"Subnet Mask[.\s]*:\s*([\d.]+)", re.IGNORECASE
)
_IPCONFIG_GATEWAY_RE = re.compile(
    r"Default Gateway[.\s]*:\s*([\d.]+)", re.IGNORECASE
)

# Regex for arp -a output lines
# Format: 192.168.1.1          00-11-22-33-44-55     dynamic
# Or:     192.168.1.1          00-11-22-33-44-55     static
_ARP_LINE_RE = re.compile(
    r"^\s*([\d.]+)\s+((?:[0-9a-fA-F]{2}[-:]){5}[0-9a-fA-F]{2})\s+(\S+)",
    re.MULTILINE,
)


def _get_subnet_info() -> dict:
    """Detect local IP, subnet mask, gateway, and compute network CIDR.

    Returns dict with keys: ip, mask, gateway, network, cidr, error.
    """
    info = {"ip": "", "mask": "", "gateway": "", "network": "", "cidr": "", "error": ""}
    if sys.platform != "win32":
        info["error"] = "Subnet detection only supported on Windows."
        return info

    try:
        result = subprocess.run(
            ["ipconfig"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        output = result.stdout

        m_ip = _IPCONFIG_IP_RE.search(output)
        m_mask = _IPCONFIG_SUBNET_RE.search(output)
        m_gw = _IPCONFIG_GATEWAY_RE.search(output)

        if m_ip:
            info["ip"] = m_ip.group(1)
        if m_mask:
            info["mask"] = m_mask.group(1)
        if m_gw:
            info["gateway"] = m_gw.group(1)

        if info["ip"] and info["mask"]:
            try:
                net = ipaddress.IPv4Network(
                    f"{info['ip']}/{info['mask']}", strict=False
                )
                info["network"] = str(net.network_address)
                info["cidr"] = str(net)
            except Exception:
                info["error"] = "Could not determine network from IP/mask."
        else:
            info["error"] = "Could not detect IP/subnet from ipconfig."

    except Exception as e:
        info["error"] = str(e)

    return info


def _run_arp_cache() -> list[dict]:
    """Run `arp -a` and parse cached entries.

    Returns list of dicts with keys: ip, mac, mac_normalized, type, vendor.
    """
    entries: list[dict] = []
    try:
        result = subprocess.run(
            ["arp", "-a"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        output = result.stdout

        for match in _ARP_LINE_RE.finditer(output):
            ip = match.group(1)
            mac_raw = match.group(2)
            entry_type = match.group(3)

            # Normalize MAC to XX:XX:XX:XX:XX:XX
            mac = mac_raw.replace("-", ":").upper()
            vendor = lookup_vendor(mac)

            entries.append({
                "ip": ip,
                "mac": mac,
                "mac_normalized": mac,
                "type": entry_type,
                "vendor": vendor,
            })

    except Exception:
        pass

    return entries


def _ping_ip(ip: str, timeout_ms: int = 600) -> dict | None:
    """Ping a single IP once. Returns dict if it responds, None otherwise."""
    try:
        result = subprocess.run(
            ["ping", "-n", "1", "-w", str(timeout_ms), ip],
            capture_output=True, text=True, timeout=max(2, timeout_ms / 500),
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode == 0:
            # Extract response time
            m = re.search(r"time[=<]\s*<?(\d+)\s*ms", result.stdout, re.IGNORECASE)
            ms = int(m.group(1)) if m else 0
            return {"ip": ip, "responded": True, "latency_ms": ms}
    except Exception:
        pass
    return None


class NeighborScanWorker(QThread):
    """Background thread: ping sweep the subnet, then collect ARP cache."""

    progress = Signal(int, int)  # (done, total)
    device_found = Signal(dict)  # single device entry
    scan_complete = Signal(list)  # all entries
    status_message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._subnet: ipaddress.IPv4Network | None = None
        self._max_workers: int = 40
        self._running: bool = False

    def start_scan(self, subnet: ipaddress.IPv4Network):
        self._subnet = subnet
        self._running = True
        self.start()

    def stop(self):
        self._running = False

    def run(self):
        if not self._subnet:
            return

        # Get the list of IPs to scan (skip network and broadcast)
        hosts = list(self._subnet.hosts())
        total = len(hosts)

        self.status_message.emit(f"Scanning {total} hosts on {self._subnet} ...")

        # Phase 1: Fast parallel ping sweep
        responded_ips: set[str] = set()
        done = 0

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {executor.submit(_ping_ip, str(ip)): ip for ip in hosts}
            for future in as_completed(futures):
                if not self._running:
                    break
                done += 1
                self.progress.emit(done, total)
                result = future.result()
                if result and result["responded"]:
                    responded_ips.add(result["ip"])

        # Phase 2: Get ARP cache (now populated by the ping sweep)
        self.status_message.emit("Collecting ARP cache ...")

        all_entries = _run_arp_cache()

        # Annotate with response info
        for entry in all_entries:
            entry["responded"] = entry["ip"] in responded_ips

        self.scan_complete.emit(all_entries)


class NeighborDialog(QDialog):
    """Pop-out dialog showing network neighbors (ARP scan + client isolation check)."""

    COL_IP = 0
    COL_MAC = 1
    COL_VENDOR = 2
    COL_TYPE = 3
    COL_ROLE = 4
    COL_LATENCY = 5

    def __init__(self, known_bssids: set[str] | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Neighbor Report")
        self.setMinimumSize(720, 440)
        self.resize(860, 560)
        self.setStyleSheet(DARK_THEME)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        self._known_bssids: set[str] = known_bssids or set()
        self._subnet_info: dict = {}
        self._entries: list[dict] = []

        self._worker = NeighborScanWorker(self)
        self._worker.progress.connect(self._on_progress)
        self._worker.device_found.connect(self._on_device_found)
        self._worker.scan_complete.connect(self._on_scan_complete)
        self._worker.status_message.connect(self._on_status)

        self._setup_ui()
        self._detect_and_display()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        # ── Top bar ──
        top = QHBoxLayout()

        self._subnet_label = QLabel("Detecting subnet ...")
        self._subnet_label.setStyleSheet(
            "color: #c0c0c0; font-size: 13px; font-weight: bold;"
            "padding: 4px 8px; background-color: #13151f; border: 1px solid #252836; border-radius: 4px;"
        )
        top.addWidget(self._subnet_label, stretch=1)

        self._scan_btn = QPushButton("🔄 Scan Subnet")
        self._scan_btn.setToolTip("Ping-sweep all hosts on the subnet to populate ARP cache")
        self._scan_btn.clicked.connect(self._start_scan)
        self._scan_btn.setStyleSheet(
            "QPushButton { background-color: #1a3d5c; color: #60a5fa; padding: 6px 16px;"
            "border: 1px solid #2d5a7f; border-radius: 4px; font-weight: bold; font-size: 12px; }"
            "QPushButton:hover { background-color: #2d5a7f; }"
            "QPushButton:disabled { background-color: #1a1d2e; color: #444; }"
        )
        top.addWidget(self._scan_btn)

        self._refresh_arp_btn = QPushButton("📋 Refresh ARP")
        self._refresh_arp_btn.setToolTip("Re-read the ARP cache without pinging")
        self._refresh_arp_btn.clicked.connect(self._refresh_arp)
        self._refresh_arp_btn.setStyleSheet(
            "QPushButton { background-color: #252836; color: #c0c0c0; padding: 6px 12px;"
            "border: 1px solid #353848; border-radius: 4px; }"
            "QPushButton:hover { background-color: #353848; }"
        )
        top.addWidget(self._refresh_arp_btn)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.clicked.connect(self._clear_table)
        self._clear_btn.setStyleSheet(
            "QPushButton { background-color: #252836; color: #c0c0c0; padding: 6px 12px;"
            "border: 1px solid #353848; border-radius: 4px; }"
            "QPushButton:hover { background-color: #353848; }"
        )
        top.addWidget(self._clear_btn)

        layout.addLayout(top)

        # ── Status / isolation summary ──
        self._isolation_label = QLabel("")
        self._isolation_label.setStyleSheet(
            "color: #808080; font-size: 12px; padding: 4px 8px;"
            "background-color: #13151f; border: 1px solid #252836; border-radius: 4px;"
        )
        layout.addWidget(self._isolation_label)

        # ── Progress bar ──
        self._progress = QProgressBar()
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        self._progress.setFormat("")
        self._progress.setFixedHeight(18)
        self._progress.setStyleSheet("""
            QProgressBar {
                background-color: #13151f; border: 1px solid #252836;
                border-radius: 3px; text-align: center; color: #808080;
            }
            QProgressBar::chunk {
                background-color: #3b82f6; border-radius: 3px;
            }
        """)
        layout.addWidget(self._progress)

        # ── Table ──
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels([
            "IP Address", "MAC Address", "Vendor", "Type", "Role", "Latency",
        ])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.setFont(QFont("Consolas", 10))
        self._table.setStyleSheet("""
            QTableWidget {
                background-color: #0a0c14; color: #e0e0e0;
                border: 1px solid #252836; border-radius: 4px;
                gridline-color: #1a1d2e; alternate-background-color: #13151f;
            }
            QTableWidget::item { padding: 2px 6px; }
            QTableWidget::item:selected { background-color: #1a3d5c; color: #e0e0e0; }
            QHeaderView::section {
                background-color: #1a1d2e; color: #a0a0a0;
                padding: 4px 6px; border: none; border-bottom: 2px solid #252836;
                font-weight: bold; font-size: 11px;
            }
        """)

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(self.COL_IP, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_MAC, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_VENDOR, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.COL_TYPE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_ROLE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_LATENCY, QHeaderView.ResizeMode.ResizeToContents)

        layout.addWidget(self._table, stretch=1)

        # ── Bottom stats ──
        bottom = QHBoxLayout()
        self._count_label = QLabel("")
        self._count_label.setStyleSheet("color: #555; font-size: 11px;")
        bottom.addWidget(self._count_label)
        bottom.addStretch()

        # Legend
        legend = QLabel("● AP/BSSID  ● Client  ● Gateway  ● Broadcast")
        legend.setStyleSheet("color: #555; font-size: 11px;")
        bottom.addWidget(legend)

        layout.addLayout(bottom)

    def _detect_and_display(self):
        """Detect subnet and show initial ARP cache."""
        info = _get_subnet_info()
        self._subnet_info = info

        if info["error"]:
            self._subnet_label.setText(f"⚠ {info['error']}")
            return

        self._subnet_label.setText(
            f"📡 {info['cidr']}  |  Local: {info['ip']}  |  Gateway: {info['gateway']}"
        )

        # Show existing ARP cache immediately
        cached = _run_arp_cache()
        if cached:
            self._populate_table(cached)

    def _start_scan(self):
        """Run a full subnet ping sweep + ARP collection."""
        if self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(1000)
            self._scan_btn.setText("🔄 Scan Subnet")
            self._scan_btn.setEnabled(True)
            self._progress.setRange(0, 1)
            self._progress.setValue(0)
            self._progress.setFormat("")
            return

        if not self._subnet_info.get("network"):
            QMessageBox.warning(self, "Neighbor Scan", "No subnet detected. Check your network connection.")
            return

        try:
            net = ipaddress.IPv4Network(self._subnet_info["cidr"], strict=False)
        except Exception as e:
            QMessageBox.warning(self, "Neighbor Scan", f"Invalid subnet: {e}")
            return

        self._scan_btn.setText("■ Stop")
        self._scan_btn.setStyleSheet(
            "QPushButton { background-color: #7c2d2d; color: #ef4444; padding: 6px 16px;"
            "border: 1px solid #944; border-radius: 4px; font-weight: bold; font-size: 12px; }"
            "QPushButton:hover { background-color: #944; }"
        )

        hosts = list(net.hosts())
        self._progress.setRange(0, len(hosts))
        self._progress.setValue(0)
        self._progress.setFormat("Starting scan ...")

        self._worker.start_scan(net)

    def _clear_table(self):
        self._table.setRowCount(0)
        self._entries = []
        self._count_label.setText("")
        self._isolation_label.setText("")

    def _refresh_arp(self):
        """Quick re-read of the ARP cache — no ping sweep."""
        cached = _run_arp_cache()
        if cached:
            self._merge_entries(cached)
        else:
            self._isolation_label.setText("ARP cache is empty — try Scan Subnet to populate it")
            self._isolation_label.setStyleSheet(
                "color: #808080; font-size: 12px; padding: 4px 8px;"
                "background-color: #13151f; border: 1px solid #252836; border-radius: 4px;"
            )

    def _merge_entries(self, new_entries: list[dict]):
        """Merge new scan entries into existing table, preserving existing data.

        Entries are keyed by MAC address. New entries add/update the table;
        existing entries not in the new scan are kept.
        """
        # Build lookup by MAC from existing entries
        existing_by_mac: dict[str, dict] = {}
        for e in self._entries:
            mac = e.get("mac", "")
            if mac:
                existing_by_mac[mac] = e

        # Merge new entries (overwrite or add)
        for entry in new_entries:
            mac = entry.get("mac", "")
            if mac:
                if mac in existing_by_mac:
                    # Update responded flag if new scan found it
                    if entry.get("responded"):
                        existing_by_mac[mac]["responded"] = True
                        if "latency_ms" in entry:
                            existing_by_mac[mac]["latency_ms"] = entry["latency_ms"]
                else:
                    existing_by_mac[mac] = entry

        # Also mark entries NOT in new scan as not responded if a scan ran
        # (but only if we have new_entries — a pure refresh shouldn't change state)

        merged = list(existing_by_mac.values())
        self._populate_table(merged)

    def _populate_table(self, entries: list[dict]):
        """Populate the table with ARP entries."""
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        self._entries = entries

        # Deduplicate by MAC
        seen_macs: set[str] = set()
        unique_entries: list[dict] = []
        for e in entries:
            mac = e.get("mac", "")
            if mac and mac in seen_macs:
                continue
            seen_macs.add(mac)
            unique_entries.append(e)

        self._table.setRowCount(len(unique_entries))

        known_macs = {m.upper() for m in self._known_bssids}
        gateway_ip = self._subnet_info.get("gateway", "")
        local_ip = self._subnet_info.get("ip", "")

        ap_count = 0
        client_count = 0
        other_count = 0

        for row, entry in enumerate(unique_entries):
            ip = entry.get("ip", "")
            mac = entry.get("mac", "")
            vendor = entry.get("vendor", "")
            entry_type = entry.get("type", "")
            latency = entry.get("latency_ms") if "latency_ms" in entry else None
            responded = entry.get("responded", True)

            # Determine role
            is_ap = mac.upper() in known_macs
            is_gateway = ip == gateway_ip
            is_local = ip == local_ip
            is_broadcast = ip.endswith(".255")

            if is_ap:
                role = "AP / BSSID"
                ap_count += 1
            elif is_gateway:
                role = "Gateway"
            elif is_local:
                role = "This Device"
            elif is_broadcast:
                role = "Broadcast"
            else:
                role = "Client"
                client_count += 1

            if not any([is_ap, is_gateway, is_local, is_broadcast]):
                if role == "Client":
                    pass  # already counted
                else:
                    other_count += 1

            # IP
            ip_item = QTableWidgetItem(ip)
            if not responded:
                ip_item.setForeground(QColor("#555555"))
            elif is_ap:
                ip_item.setForeground(QColor("#60a5fa"))
                ip_item.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
            elif is_gateway:
                ip_item.setForeground(QColor("#fbbf24"))
                ip_item.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
            elif is_local:
                ip_item.setForeground(QColor("#4ade80"))
            self._table.setItem(row, self.COL_IP, ip_item)

            # MAC
            mac_item = QTableWidgetItem(mac)
            if is_ap:
                mac_item.setForeground(QColor("#60a5fa"))
                mac_item.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
            self._table.setItem(row, self.COL_MAC, mac_item)

            # Vendor
            vendor_item = QTableWidgetItem(vendor)
            vendor_item.setForeground(QColor("#a0a0a0"))
            self._table.setItem(row, self.COL_VENDOR, vendor_item)

            # Type (dynamic/static)
            type_item = QTableWidgetItem(entry_type)
            type_item.setForeground(QColor("#808080"))
            self._table.setItem(row, self.COL_TYPE, type_item)

            # Role
            role_item = QTableWidgetItem(role)
            if is_ap:
                role_item.setForeground(QColor("#60a5fa"))
            elif is_gateway:
                role_item.setForeground(QColor("#fbbf24"))
            elif is_local:
                role_item.setForeground(QColor("#4ade80"))
            else:
                role_item.setForeground(QColor("#c0c0c0"))
            self._table.setItem(row, self.COL_ROLE, role_item)

            # Latency
            if latency is not None:
                lat_text = f"{latency} ms"
                lat_item = QTableWidgetItem(lat_text)
                if latency < 10:
                    lat_item.setForeground(QColor("#4ade80"))
                elif latency < 50:
                    lat_item.setForeground(QColor("#fbbf24"))
                else:
                    lat_item.setForeground(QColor("#ef4444"))
                self._table.setItem(row, self.COL_LATENCY, lat_item)
            else:
                lat_item = QTableWidgetItem("—")
                lat_item.setForeground(QColor("#555"))
                self._table.setItem(row, self.COL_LATENCY, lat_item)

        self._table.setSortingEnabled(True)

        # Update counts
        total = len(unique_entries)
        self._count_label.setText(
            f"{total} device{'s' if total != 1 else ''}  "
            f"|  APs: {ap_count}  |  Clients: {client_count}"
        )

        # Client isolation assessment
        if client_count == 0 and total > 1:
            isolation_text = "✅ Client isolation appears ON — no client devices visible to each other"
            isolation_color = "#4ade80"
        elif client_count > 0:
            isolation_text = f"⚠ Client isolation appears OFF — {client_count} client{'s' if client_count != 1 else ''} visible"
            isolation_color = "#fbbf24"
        elif total <= 1:
            isolation_text = "Insufficient data to assess client isolation"
            isolation_color = "#808080"
        else:
            isolation_text = ""
            isolation_color = "#808080"

        self._isolation_label.setText(isolation_text)
        self._isolation_label.setStyleSheet(
            f"color: {isolation_color}; font-size: 12px; padding: 4px 8px;"
            "background-color: #13151f; border: 1px solid #252836; border-radius: 4px;"
        )

    def _on_progress(self, done: int, total: int):
        self._progress.setValue(done)
        self._progress.setFormat(f"Scanning ... {done}/{total}")

    def _on_device_found(self, entry: dict):
        pass  # We wait for the complete pass to populate

    def _on_scan_complete(self, entries: list[dict]):
        """Merge scan results with existing table entries."""
        self._merge_entries(entries)
        self._progress.setFormat(f"Complete — {len(entries)} entries")
        self._scan_btn.setText("🔄 Scan Subnet")
        self._scan_btn.setEnabled(True)
        self._scan_btn.setStyleSheet(
            "QPushButton { background-color: #1a3d5c; color: #60a5fa; padding: 6px 16px;"
            "border: 1px solid #2d5a7f; border-radius: 4px; font-weight: bold; font-size: 12px; }"
            "QPushButton:hover { background-color: #2d5a7f; }"
        )

    def _on_status(self, msg: str):
        self._progress.setFormat(msg)

    def closeEvent(self, event):
        if self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(2000)
        event.accept()
