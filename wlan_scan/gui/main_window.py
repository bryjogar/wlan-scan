"""Main application window for WLAN Scan."""

import sys
import threading
import time
import os
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal, Slot, QThread, QSize, QEvent
from PySide6.QtGui import QFont, QAction, QKeySequence, QColor, QIcon
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QToolBar, QStatusBar, QLabel, QComboBox, QCheckBox,
    QTabWidget, QMessageBox, QFileDialog, QLineEdit, QApplication, QFrame,
)

from ..models import Network, ScanResult
from ..scanner import WiFiScanner
from ..logging_setup import get_logger
from .. import netsh_scanner
from ..pwsh_scanner import PwshScanner
from .styles import DARK_THEME, BAND_COLORS
from .channel_map import ChannelMapWidget
from .signal_graph import SignalGraphWidget
from .network_table import NetworkTableWidget
from .detail_panel import DetailPanel
from .bssid_tracker import BSSIDTracker
from .ping_dialog import PingDialog
from .neighbor_dialog import NeighborDialog
from .speedtest_dialog import SpeedtestDialog


class UpdateAvailableEvent(QEvent):
    """Custom event posted from background update-check thread."""
    _event_type = QEvent.Type(QEvent.registerEventType())

    def __init__(self, update_info):
        super().__init__(self._event_type)
        self.update_info = update_info


class ScannerWorker(QThread):
    """Background thread for Wi-Fi scanning."""

    scan_complete = Signal(object)  # ScanResult
    scan_error = Signal(str)
    scan_started = Signal()
    debug_message = Signal(str)
    capability_detected = Signal(object)  # InterfaceCapability

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._paused = False
        self._interval = 3.0  # seconds between scans

    def run(self):
        self._running = True
        use_netsh = False
        use_pwsh = False
        use_mac = False
        scanner = None
        pwsh_scanner = None
        mac_scanner = None

        # macOS: use the airport CLI scanner
        if sys.platform == "darwin":
            from ..macos_scanner import MacScanner, is_available
            if not is_available():
                self.debug_message.emit("✗ airport CLI not found on this macOS.")
            else:
                use_mac = True
                mac_scanner = MacScanner()
                mac_scanner.set_debug_callback(
                    lambda msg: self.debug_message.emit(msg))
                self.debug_message.emit("Using macOS airport CLI scanner")

        # ARM64 Windows: ctypes wlanapi is broken (libffi issue).
        # Use .NET P/Invoke bridge instead.
        if sys.platform == "win32":
            try:
                import platform
                arch = platform.machine().lower()
                if "arm" in arch or "aarch" in arch:
                    self.debug_message.emit(
                        f"ARM64 detected ({arch}) — using PowerShell/.NET P/Invoke bridge")
                    use_pwsh = True
            except Exception:
                pass

        if use_pwsh:
            pwsh_scanner = PwshScanner()
            pwsh_scanner.set_debug_callback(
                lambda msg: self.debug_message.emit(msg))
        else:
            # Try ctypes wlanapi scanner first
            try:
                scanner = WiFiScanner()
                scanner.set_debug_callback(
                    lambda msg: self.debug_message.emit(msg))
                scanner.open()

                # Run diagnostics
                self.debug_message.emit("─── Scanner Diagnostics ───")
                diag = scanner.run_diagnostics()
                for line in diag:
                    self.debug_message.emit(line)
                self.debug_message.emit("─── End Diagnostics ───")

                # Detect interface capabilities (bands, Wi-Fi generations)
                try:
                    cap = scanner.detect_capability()
                    if cap and cap.supported_bands:
                        self.capability_detected.emit(cap)
                except Exception as cap_e:
                    self.debug_message.emit(f"Capability detection: {cap_e}")

                # Check if ANY call succeeded
                any_ok = any(line.startswith("✓") for line in diag)
                if not any_ok:
                    self.debug_message.emit(
                        "⚠ wlanapi unavailable — falling back to netsh wlan")
                    scanner.close()
                    scanner = None
                    use_netsh = True
            except Exception as e:
                self.debug_message.emit(f"wlanapi init failed: {e}")
                self.debug_message.emit("Falling back to netsh wlan...")
                use_netsh = True
                if scanner:
                    try:
                        scanner.close()
                    except Exception:
                        pass
                scanner = None

        while self._running:
            if not self._paused:
                try:
                    self.scan_started.emit()
                    if use_mac:
                        result = mac_scanner.scan()
                    elif use_pwsh:
                        try:
                            result = pwsh_scanner.scan()
                        except Exception as pwsh_err:
                            self.debug_message.emit(
                                f"PowerShell scanner failed: {pwsh_err}")
                            self.debug_message.emit(
                                "Falling back to netsh wlan...")
                            result = netsh_scanner.scan()
                            use_pwsh = False  # don't keep retrying
                    elif use_netsh:
                        result = netsh_scanner.scan()
                    else:
                        result = scanner.get_scan_results()
                    self.scan_complete.emit(result)
                except Exception as e:
                    # If netsh also fails, try it as a last resort
                    if use_pwsh:
                        try:
                            self.debug_message.emit(
                                "Last resort: trying netsh wlan...")
                            result = netsh_scanner.scan()
                            self.scan_complete.emit(result)
                            use_pwsh = False
                            continue
                        except Exception:
                            pass
                    self.scan_error.emit(str(e))
            # Wait for next scan
            slept = 0
            while self._running and slept < self._interval:
                time.sleep(0.1)
                slept += 0.1

        if scanner:
            try:
                scanner.close()
            except Exception:
                pass
        if mac_scanner:
            try:
                mac_scanner.close()
            except Exception:
                pass

    def stop(self):
        self._running = False

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    def set_interval(self, seconds: float):
        self._interval = max(0.25, seconds)


class MainWindow(QMainWindow):
    """WLAN Scan main application window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("WLAN Scan")
        self.setWindowIcon(self._app_icon())
        self.setMinimumSize(1200, 800)
        self.resize(1400, 900)
        self.setStyleSheet(DARK_THEME)

        self._last_result: ScanResult | None = None
        self._scan_count: int = 0
        self._conn_bssid: str | None = None
        self._conn_bssid_ts: float = 0
        self._column_filters: dict = {}
        self._start_time = datetime.now()

        self._setup_ui()
        self._setup_statusbar()
        self._setup_toolbar()
        self._setup_scanner()

        # Check for updates in background (non-blocking)
        self._check_for_updates()

    @staticmethod
    def _app_icon() -> QIcon:
        """Resolve app icon (works in dev and PyInstaller bundles)."""
        candidates = []
        if getattr(sys, 'frozen', False):
            candidates.append(os.path.join(sys._MEIPASS, 'wlan_scan.ico'))
        # Dev paths
        candidates.append(
            os.path.join(os.path.dirname(__file__), '..', '..', 'wlan_scan.ico')
        )
        candidates.append(
            os.path.join(os.path.dirname(sys.executable), 'wlan_scan.ico')
            if getattr(sys, 'frozen', False) else ''
        )
        for path in candidates:
            if path and os.path.exists(path):
                return QIcon(path)
        return QIcon()  # fallback — blank icon

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        # ── Top section: Scan button + band selector ──
        top_bar = QHBoxLayout()

        self._scan_btn = QPushButton("▶  Scan")
        self._scan_btn.setObjectName("scanButton")
        self._scan_btn.setFixedSize(140, 42)
        self._scan_btn.clicked.connect(self._toggle_scan)
        top_bar.addWidget(self._scan_btn)

        top_bar.addSpacing(8)

        self._scan_count_lbl = QLabel("Ready")
        self._scan_count_lbl.setStyleSheet("color: #808080; font-size: 12px;")
        top_bar.addWidget(self._scan_count_lbl)

        top_bar.addStretch()

        self._band_combo = QComboBox()
        self._band_combo.addItems(["2.4 GHz", "5 GHz", "6 GHz", "All Bands"])
        self._band_combo.setCurrentText("All Bands")
        self._band_combo.setStyleSheet("""
            QComboBox {
                background-color: #252836;
                border: 1px solid #353848;
                border-radius: 6px;
                padding: 6px 12px;
                color: #e0e0e0;
                font-size: 12px;
            }
            QComboBox:hover { border-color: #3b82f6; }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background-color: #1a1d2e;
                border: 1px solid #2a2d3e;
                selection-background-color: #3b82f6;
                color: #e0e0e0;
            }
        """)
        self._band_combo.currentTextChanged.connect(self._on_band_changed)
        top_bar.addWidget(QLabel("Band:"))
        top_bar.addWidget(self._band_combo)

        main_layout.addLayout(top_bar)

        # ── Channel map ──
        self._channel_map = ChannelMapWidget()
        self._channel_map.setMinimumHeight(260)
        self._channel_map.setStyleSheet(
            "background-color: #13151f; border: 1px solid #252836; border-radius: 8px;"
        )
        main_layout.addWidget(self._channel_map)

        # ── SSID filter checkboxes ──
        self._ssid_filter_widget = QWidget()
        self._ssid_filter_layout = QHBoxLayout(self._ssid_filter_widget)
        self._ssid_filter_layout.setContentsMargins(4, 2, 4, 2)
        self._ssid_filter_layout.setSpacing(4)
        self._ssid_filter_layout.addWidget(QLabel("Filter:"))
        main_layout.addWidget(self._ssid_filter_widget)
        self._ssid_checkboxes: dict[str, QCheckBox] = {}

        # ── Bottom section: splitter with table + graph + details ──
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: table + graph (vertical split)
        left_splitter = QSplitter(Qt.Orientation.Vertical)

        self._network_table = NetworkTableWidget()
        self._network_table.selection_changed.connect(self._on_selection_changed)
        self._network_table.filters_changed.connect(self._on_column_filters_changed)
        left_splitter.addWidget(self._network_table)

        self._signal_graph = SignalGraphWidget()
        left_splitter.addWidget(self._signal_graph)

        # BSSID tracker (beeping site survey tool)
        self._bssid_tracker = BSSIDTracker()
        self._bssid_tracker.status_changed.connect(self._on_tracker_status)

        left_splitter.setSizes([400, 250])

        splitter.addWidget(left_splitter)

        # Right: detail panel
        self._detail_panel = DetailPanel()
        splitter.addWidget(self._detail_panel)

        splitter.setSizes([900, 350])

        main_layout.addWidget(splitter, stretch=1)

        # ── Debug log (collapsed by default) ──
        from PySide6.QtWidgets import QTextEdit
        self._debug_log = QTextEdit()
        self._debug_log.setReadOnly(True)
        self._debug_log.setMaximumHeight(100)
        self._debug_log.setVisible(False)
        self._debug_log.setFont(QFont("Consolas", 10))
        self._debug_log.setStyleSheet(
            "QTextEdit { background-color: #0a0c14; color: #10b981; "
            "border: 1px solid #1e2130; border-radius: 4px; padding: 4px; }"
        )
        main_layout.addWidget(self._debug_log)

    def _setup_toolbar(self):
        toolbar = self.addToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(18, 18))

        # Auto-scan toggle
        self._auto_scan_cb = QCheckBox("Auto-scan")
        self._auto_scan_cb.setChecked(True)
        self._auto_scan_cb.toggled.connect(self._on_auto_scan_toggled)
        toolbar.addWidget(self._auto_scan_cb)

        toolbar.addSeparator()

        # Interval selector
        toolbar.addWidget(QLabel("  Interval: "))
        self._interval_combo = QComboBox()
        self._interval_combo.addItems(["0.25s", "0.5s", "1s", "2s", "3s", "5s", "10s", "30s"])
        self._interval_combo.setCurrentText("3s")
        self._interval_combo.currentTextChanged.connect(self._on_interval_changed)
        self._interval_combo.setStyleSheet("""
            QComboBox {
                background-color: #252836;
                border: 1px solid #353848;
                border-radius: 4px;
                padding: 4px 8px;
                color: #e0e0e0;
            }
        """)
        toolbar.addWidget(self._interval_combo)

        toolbar.addSeparator()

        # Track selection
        self._track_cb = QCheckBox("Trace Signal")
        self._track_cb.setChecked(True)
        self._track_cb.toggled.connect(self._on_track_toggled)
        toolbar.addWidget(self._track_cb)

        # BSSID beep tracker
        self._bssid_track_btn = QPushButton("📍 Locate BSSID")
        self._bssid_track_btn.setToolTip("Sonar-mode: beeps faster/louder as you approach the selected BSSID")
        self._bssid_track_btn.clicked.connect(self._on_track_bssid_clicked)
        self._bssid_track_btn.setEnabled(False)
        self._bssid_track_btn.setStyleSheet(
            "QPushButton { background-color: #2d3348; color: #e0e0e0; padding: 4px 10px;"
            "border: 1px solid #3b3f55; border-radius: 4px; font-size: 12px; }"
            "QPushButton:hover { background-color: #3b3f55; }"
            "QPushButton:disabled { color: #555; background-color: #1e2130; }"
        )
        toolbar.addWidget(self._bssid_track_btn)

        # Ping tool (always available)
        self._ping_btn = QPushButton("📶 Ping")
        self._ping_btn.setToolTip("Open ping test window")
        self._ping_btn.clicked.connect(self._on_ping_clicked)
        self._ping_btn.setStyleSheet(
            "QPushButton { background-color: #2d3348; color: #e0e0e0; padding: 4px 10px;"
            "border: 1px solid #3b3f55; border-radius: 4px; font-size: 12px; }"
            "QPushButton:hover { background-color: #3b3f55; }"
        )
        toolbar.addWidget(self._ping_btn)

        # Neighbor report
        self._neighbor_btn = QPushButton("🌐 Neighbors")
        self._neighbor_btn.setToolTip("Scan subnet for all responding devices (client isolation check)")
        self._neighbor_btn.clicked.connect(self._on_neighbor_clicked)
        self._neighbor_btn.setStyleSheet(
            "QPushButton { background-color: #2d3348; color: #e0e0e0; padding: 4px 10px;"
            "border: 1px solid #3b3f55; border-radius: 4px; font-size: 12px; }"
            "QPushButton:hover { background-color: #3b3f55; }"
        )
        toolbar.addWidget(self._neighbor_btn)

        # Speedtest
        self._speedtest_btn = QPushButton("⚡ Speedtest")
        self._speedtest_btn.setToolTip("Run Ookla speedtest (requires speedtest CLI)")
        self._speedtest_btn.clicked.connect(self._on_speedtest_clicked)
        self._speedtest_btn.setStyleSheet(
            "QPushButton { background-color: #2d3348; color: #e0e0e0; padding: 4px 10px;"
            "border: 1px solid #3b3f55; border-radius: 4px; font-size: 12px; }"
            "QPushButton:hover { background-color: #3b3f55; }"
        )
        toolbar.addWidget(self._speedtest_btn)

        # BSSID tracker status
        self._bssid_track_status = QLabel("")
        self._bssid_track_status.setStyleSheet("color: #888; font-size: 11px;")
        toolbar.addWidget(self._bssid_track_status)

        toolbar.addSeparator()

        # Debug toggle
        debug_action = QAction("Debug Log", self)
        debug_action.setCheckable(True)
        debug_action.setShortcut(QKeySequence("Ctrl+D"))
        debug_action.toggled.connect(
            lambda v: self._debug_log.setVisible(v))
        toolbar.addAction(debug_action)

        toolbar.addSeparator()
        export_action = QAction("Export CSV", self)
        export_action.triggered.connect(self._export_csv)
        toolbar.addAction(export_action)

        # Clear graph action
        clear_action = QAction("Clear Graph", self)
        clear_action.triggered.connect(self._signal_graph.clear_tracking)
        toolbar.addAction(clear_action)

    def _setup_statusbar(self):
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)

        self._status_interface = QLabel("")
        self._status_interface.setStyleSheet("color: #808080; padding: 2px 8px;")
        self._statusbar.addWidget(self._status_interface)

        self._status_networks = QLabel("")
        self._status_networks.setStyleSheet("color: #808080; padding: 2px 8px;")
        self._statusbar.addPermanentWidget(self._status_networks)

        self._status_scan = QLabel("")
        self._status_scan.setStyleSheet("color: #808080; padding: 2px 8px;")
        self._statusbar.addPermanentWidget(self._status_scan)

        self._status_capability = QLabel("")
        self._status_capability.setStyleSheet("color: #808080; padding: 2px 8px;")
        self._statusbar.addPermanentWidget(self._status_capability)

        # Clickable update notification (hidden until update available)
        self._status_update = QLabel("")
        self._status_update.setStyleSheet(
            "color: #60a5fa; padding: 2px 8px; font-weight: bold;"
        )
        self._status_update.setOpenExternalLinks(True)
        self._status_update.hide()
        self._statusbar.addWidget(self._status_update)

    def _setup_scanner(self):
        # Setup file logging
        self._logger = get_logger()
        self._logger.info("WLAN Scan GUI starting")

        self._worker = ScannerWorker()
        self._worker.scan_complete.connect(self._on_scan_complete)
        self._worker.scan_error.connect(self._on_scan_error)
        self._worker.scan_started.connect(self._on_scan_started)
        self._worker.debug_message.connect(self._on_debug_message)
        self._worker.capability_detected.connect(self._on_capability_detected)

        # Check platform
        if sys.platform != "win32":
            self._statusbar.showMessage(
                "⚠  Running on non-Windows platform — scanner is unavailable. "
                "Use for UI development only.",
                0,
            )

        # Start scanning if auto-scan is on
        if self._auto_scan_cb.isChecked():
            self._start_scanning()

    def _check_for_updates(self):
        """Check GitHub for a newer version — runs in background."""
        from threading import Thread
        from ..updater import check_for_updates

        try:
            from ..version import __version_sha__
        except ImportError:
            __version_sha__ = "unknown"

        def _run():
            info = check_for_updates(__version_sha__)
            if info:
                # Show update notification on main thread
                QApplication.instance().postEvent(
                    self, UpdateAvailableEvent(info)
                )

        Thread(target=_run, daemon=True).start()

    def event(self, event: QEvent) -> bool:
        """Handle custom events including UpdateAvailableEvent."""
        if event.type() == UpdateAvailableEvent._event_type:
            info = event.update_info
            self._status_update.setText(
                f'📦 <a href="{info.url}" style="color: #60a5fa; text-decoration: underline;">'
                f'Update available: {info.current_sha[:7]} → {info.latest_sha[:7]}</a>'
                f' — {info.message[:60]}…'
            )
            self._status_update.show()
            return True
        return super().event(event)

    # ── Actions ──

    def _toggle_scan(self):
        if hasattr(self, '_worker') and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(2000)
            self._scan_btn.setText("▶  Scan")
            self._scan_btn.setStyleSheet(
                "QPushButton#scanButton { background-color: #3b82f6; }"
            )
        else:
            self._start_scanning()

    def _start_scanning(self):
        self._scan_btn.setText("⏸  Stop")
        self._scan_btn.setStyleSheet(
            "QPushButton#scanButton { background-color: #ef4444; }"
            "QPushButton#scanButton:hover { background-color: #dc2626; }"
        )
        interval = float(self._interval_combo.currentText().replace("s", ""))
        self._worker.set_interval(interval)
        self._worker.start()

    @Slot(object)
    def _on_capability_detected(self, cap):
        """Display interface capability info in the status bar."""
        if cap and cap.supported_bands:
            bands = " + ".join(cap.supported_bands)
            gens = ", ".join(cap.supported_generations)
            text = f"🖧 {bands}  |  {gens}"
            if cap.missing_6ghz:
                text += "  ⚠ No 6 GHz support"
            if cap.missing_5ghz:
                text += "  ⚠ No 5 GHz support"
            self._status_capability.setText(text)
            self._status_capability.setStyleSheet(
                "color: #4ade80; padding: 2px 8px;"
                if not (cap.missing_5ghz or cap.missing_6ghz)
                else "color: #fbbf24; padding: 2px 8px;"
            )
        else:
            self._status_capability.setText("🖧 Capability: unknown")
            self._status_capability.setStyleSheet("color: #808080; padding: 2px 8px;")

    @Slot(object)
    def _on_scan_complete(self, result: ScanResult):
        self._last_result = result
        self._scan_count += 1
        elapsed = (datetime.now() - self._start_time).seconds

        # Feed all BSS to the beep tracker
        feed_list = [
            (b.bssid, b.rssi, n.ssid)
            for n in result.networks
            for b in n.bss_list
        ]
        self._bssid_tracker.feed_scan(feed_list)

        # Update signal graph
        band = self._band_combo.currentText()
        self._signal_graph.add_sample(self._get_band_filtered(result.networks))

        # Refresh everything
        self._refresh_table()

        # Update SSID filter checkboxes
        band = self._band_combo.currentText()
        current_band = band if band != "All Bands" else "2.4 GHz"
        current_ssids = set()
        for net in result.networks:
            if net.ssid:
                current_ssids.add(net.ssid)
        existing = set(self._ssid_checkboxes.keys())
        for ssid in existing - current_ssids:
            cb = self._ssid_checkboxes.pop(ssid)
            self._ssid_filter_layout.removeWidget(cb)
            cb.deleteLater()
        for ssid in sorted(current_ssids - existing):
            cb = QCheckBox(ssid)
            cb.setChecked(True)
            cb.setStyleSheet(
                "QCheckBox { color: #e0e0e0; font-size: 11px; spacing: 4px; }"
                "QCheckBox::indicator { width: 14px; height: 14px; }"
            )
            cb.toggled.connect(lambda checked, s=ssid: self._channel_map.toggle_ssid(s, checked))
            self._ssid_filter_layout.addWidget(cb)
            self._ssid_checkboxes[ssid] = cb

        # Status bar
        total_bss = sum(len(n.bss_list) for n in result.networks)
        self._status_interface.setText(f"📡 {result.interface_name[:50]}")
        self._status_networks.setText(
            f"Networks: {len(result.networks)}  |  BSS: {total_bss}"
        )
        self._status_scan.setText(
            f"Scan #{self._scan_count}  |  {elapsed}s elapsed"
        )
        self._scan_count_lbl.setText(
            f"Scan #{self._scan_count} · {total_bss} BSS found"
        )

    @Slot(str)
    def _on_scan_error(self, error: str):
        """Handle scan errors — log to file, show in debug panel, status bar."""
        self._logger.error(error)
        self._debug_log.append(f"[ERROR] {error}")
        # Flash the error in status bar (persists for 30s)
        self._statusbar.showMessage(f"⚠ {error}", 30000)
        # If debug panel isn't visible, make it flash
        if not self._debug_log.isVisible():
            self._status_scan.setText(f"⚠ Error — press Ctrl+D for details")
            self._status_scan.setStyleSheet("color: #ef4444; padding: 2px 8px;")

    @Slot()
    def _on_scan_started(self):
        self._status_scan.setText(f"Scanning...")

    # ── Filtering / refresh ──

    def _get_band_filtered(self, networks: list) -> list:
        """Filter networks to the selected band."""
        band = self._band_combo.currentText()
        if band == "All Bands":
            result = [
                Network(ssid=n.ssid, bss_list=list(n.bss_list))
                for n in networks
            ]
            for n in result:
                n.refresh_summary()
            return result
        result = []
        for n in networks:
            matching = [b for b in n.bss_list if b.band == band]
            if matching:
                net = Network(ssid=n.ssid, bss_list=matching)
                net.refresh_summary()
                result.append(net)
        return result

    def _refresh_table(self):
        """Re-filter and update table + channel map from cached scan result."""
        if not self._last_result:
            return
        band = self._band_combo.currentText()
        filtered = self._get_band_filtered(self._last_result.networks)
        filtered = self._apply_column_filters(filtered)
        self._channel_map.set_data(
            filtered, band if band != "All Bands" else "2.4 GHz"
        )
        self._network_table.populate(filtered, self._connected_bssid())

    def _connected_bssid(self) -> str:
        """Connected BSSID, refreshed at most every 10s (cheap subprocess)."""
        from ..connection import connected_bssid

        now = time.time()
        if (self._conn_bssid_ts or 0) + 10 < now or self._conn_bssid is None:
            self._conn_bssid = connected_bssid() or ""
            self._conn_bssid_ts = now
        return self._conn_bssid

    @Slot(str)
    def _on_debug_message(self, msg: str):
        """Append debug message to log panel and file."""
        self._logger.debug(msg)
        ts = datetime.now().strftime("%H:%M:%S")
        self._debug_log.append(f"[{ts}] {msg}")
        # Auto-scroll to bottom
        scrollbar = self._debug_log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _on_band_changed(self, band: str):
        """Band dropdown changed — refresh table and channel map."""
        if self._last_result:
            self._refresh_table()

    def _on_selection_changed(self):
        if self._network_table._block_selection:
            return
        bss = self._network_table.get_selected_bss()
        self._detail_panel.show_bss(bss)
        if bss and self._track_cb.isChecked():
            self._signal_graph.track_ssid(bss.ssid)

        # Enable/disable BSSID track button
        if self._bssid_tracker.is_tracking:
            self._bssid_track_btn.setText("🔇 Stop Locating")
            self._bssid_track_btn.setEnabled(True)
            self._bssid_track_btn.setStyleSheet(
                "QPushButton { background-color: #7c2d2d; color: #fbb; padding: 4px 10px;"
                "border: 1px solid #944; border-radius: 4px; font-size: 12px; }"
                "QPushButton:hover { background-color: #944; }"
            )
        else:
            if bss and bss.bssid:
                self._bssid_track_btn.setText(f"📍 Locate {bss.bssid[:17]}")
                self._bssid_track_btn.setEnabled(True)
                self._bssid_track_btn.setStyleSheet(
                    "QPushButton { background-color: #2d3348; color: #e0e0e0; padding: 4px 10px;"
                    "border: 1px solid #3b3f55; border-radius: 4px; font-size: 12px; }"
                    "QPushButton:hover { background-color: #3b3f55; }"
                )
            else:
                self._bssid_track_btn.setText("📍 Locate BSSID")
                self._bssid_track_btn.setEnabled(False)
                self._bssid_track_btn.setStyleSheet(
                    "QPushButton { background-color: #2d3348; color: #e0e0e0; padding: 4px 10px;"
                    "border: 1px solid #3b3f55; border-radius: 4px; font-size: 12px; }"
                    "QPushButton:disabled { color: #555; background-color: #1e2130; }"
                )

    def _on_auto_scan_toggled(self, checked: bool):
        if checked:
            if not self._worker.isRunning():
                self._start_scanning()
        else:
            pass  # keep scanning until user clicks stop

    def _on_interval_changed(self, text: str):
        interval = float(text.replace("s", ""))
        if hasattr(self, '_worker') and self._worker.isRunning():
            self._worker.set_interval(interval)

    def _on_column_filters_changed(self, filters: dict):
        """Called when any column filter input changes."""
        self._column_filters = filters
        if self._last_result:
            self._refresh_table()

    def _apply_column_filters(self, networks: list) -> list:
        """Filter BSS entries by column-specific filters (AND logic)."""
        if not self._column_filters:
            return networks
        result = []
        for net in networks:
            matching = []
            for bss in net.bss_list:
                if self._bss_matches_filters(net.ssid, bss):
                    matching.append(bss)
            if matching:
                net_copy = Network(ssid=net.ssid)
                net_copy.bss_list = matching
                net_copy.refresh_summary()
                result.append(net_copy)
        return result

    def _bss_matches_filters(self, ssid: str, bss) -> bool:
        """Check if a BSS matches all active column filters."""
        for col, ft in self._column_filters.items():
            ft = ft.lower()
            if col == "SSID" and ft not in (ssid or "").lower():
                return False
            if col == "BSSID" and ft not in (bss.bssid or "").lower():
                return False
            if col == "Security" and ft not in (bss.security or "").lower():
                return False
            if col == "Channel" and ft not in str(bss.channel):
                return False
            if col == "Vendor" and ft not in (bss.vendor or "").lower():
                return False
            if col == "Band" and ft not in (bss.band or "").lower():
                return False
        return True

    def _on_track_toggled(self, checked: bool):
        if not checked:
            self._signal_graph.clear_tracking()

    def _on_ping_clicked(self):
        """Open the ping test dialog."""
        # Pre-fill with selected BSS info if available
        default_target = ""
        bss = self._network_table.get_selected_bss()
        if bss:
            # Try to detect gateway from route print (fast Windows call)
            default_target = self._detect_gateway()
        dialog = PingDialog(target=default_target, parent=self)
        dialog.show()
        # Keep reference so it isn't garbage collected
        if not hasattr(self, '_ping_dialogs'):
            self._ping_dialogs = []
        self._ping_dialogs.append(dialog)
        # Clean up closed dialogs
        self._ping_dialogs = [d for d in self._ping_dialogs if d.isVisible()]

    @staticmethod
    def _detect_gateway() -> str:
        """Try to detect the default gateway via Windows route print."""
        import subprocess
        if sys.platform != "win32":
            return ""
        try:
            result = subprocess.run(
                ["route", "print", "0.0.0.0"],
                capture_output=True, text=True, timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            for line in result.stdout.splitlines():
                if "0.0.0.0" in line:
                    parts = line.split()
                    # Format: Network Destination  Netmask  Gateway  Interface  Metric
                    # Gateway is typically the 3rd column
                    for i, p in enumerate(parts):
                        if p == "0.0.0.0":
                            # Next non-0.0.0.0 IP is likely the gateway
                            for j in range(i + 1, min(i + 4, len(parts))):
                                if _is_ip(parts[j]) and parts[j] != "0.0.0.0":
                                    return parts[j]
        except Exception:
            pass
        return ""

    def _on_track_bssid_clicked(self):
        """Toggle BSSID tracking on/off."""
        if self._bssid_tracker.is_tracking:
            self._bssid_tracker.untrack()
        else:
            bss = self._network_table.get_selected_bss()
            if bss and bss.bssid:
                self._bssid_tracker.track(bss.bssid)
        self._on_selection_changed()  # refresh button state

    def _on_neighbor_clicked(self):
        """Open the neighbor report (ARP scanner)."""
        known_bssids: set[str] = set()
        if self._last_result:
            for net in self._last_result.networks:
                for bss in net.bss_list:
                    if bss.bssid:
                        known_bssids.add(bss.bssid.upper())

        dialog = NeighborDialog(known_bssids=known_bssids, parent=self)
        dialog.show()
        if not hasattr(self, '_neighbor_dialogs'):
            self._neighbor_dialogs = []
        self._neighbor_dialogs.append(dialog)
        self._neighbor_dialogs = [d for d in self._neighbor_dialogs if d.isVisible()]

    def _on_speedtest_clicked(self):
        """Open the speedtest dialog."""
        dialog = SpeedtestDialog(parent=self)
        dialog.show()
        if not hasattr(self, '_speedtest_dialogs'):
            self._speedtest_dialogs = []
        self._speedtest_dialogs.append(dialog)
        self._speedtest_dialogs = [d for d in self._speedtest_dialogs if d.isVisible()]

    @Slot(str, int, bool)
    def _on_tracker_status(self, bssid: str, rssi: int, found: bool):
        """Update the BSSID tracker status display."""
        if not bssid:
            self._bssid_track_status.setText("")
            return
        if found:
            bars = "█" * max(1, (rssi + 100) // 10)
            self._bssid_track_status.setText(
                f"📍 {bssid}  RSSI: {rssi} dBm  {bars}"
            )
            self._bssid_track_status.setStyleSheet("color: #4ade80; font-size: 11px;")
        else:
            self._bssid_track_status.setText(
                f"❌ {bssid}  — lost signal"
            )
            self._bssid_track_status.setStyleSheet("color: #ef4444; font-size: 11px;")

    def _export_csv(self):
        """Export current scan results to CSV via save dialog."""
        if not self._last_result:
            QMessageBox.information(self, "Export", "No scan data to export.")
            return

        from pathlib import Path
        import csv
        from datetime import datetime

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"wifi_scan_{ts}.csv"
        default_dir = str(Path.home() / "Documents")

        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Export Wi-Fi Scan",
            str(Path(default_dir) / default_name),
            "CSV Files (*.csv);;All Files (*)",
        )
        if not path_str:
            return  # user cancelled

        path = Path(path_str)
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "SSID", "BSSID", "RSSI", "Channel", "Band", "Channel Width",
                    "Security", "PHY", "Vendor", "Max Rate (Mbps)",
                    "Channel Utilization %", "Clients", "Center Freq (MHz)",
                ])
                for net in self._last_result.networks:
                    for bss in net.bss_list:
                        writer.writerow([
                            net.ssid, bss.bssid, bss.rssi, bss.channel,
                            bss.band, bss.channel_width,
                            bss.security, bss.phy_type, bss.vendor or "",
                            f"{bss.max_rate:.1f}" if bss.max_rate > 0 else "",
                            f"{bss.channel_utilization:.1f}" if bss.channel_utilization > 0 else "",
                            bss.station_count if bss.station_count > 0 else "",
                            bss.center_frequency if bss.center_frequency > 0 else "",
                        ])

            QMessageBox.information(self, "Export", f"Exported {self._scan_count} scans to:\n{path}")
        except OSError as e:
            QMessageBox.critical(self, "Export Error", str(e))

    # ── Window management ──

    def closeEvent(self, event):
        if hasattr(self, '_worker') and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(3000)
        event.accept()


def _is_ip(s: str) -> bool:
    """Return True if s looks like an IPv4 address."""
    import ipaddress
    try:
        ipaddress.IPv4Address(s)
        return True
    except Exception:
        return False


def main():
    """Launch the WLAN Scan GUI application."""
    app = QApplication(sys.argv)
    app.setApplicationName("WLAN Scan")
    app.setOrganizationName("WLANScan")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())

