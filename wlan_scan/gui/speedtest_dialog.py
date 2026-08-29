"""Speedtest dialog using Ookla's official CLI (speedtest.exe).

Runs the Ookla CLI in a background thread, parses JSON output,
and displays download/upload speed, latency, jitter, and server info.

If speedtest.exe is not found, offers to download it from speedtest.net.
"""

import json
import os
import subprocess
import sys
import re
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QFont, QColor, QTextCursor, QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QPlainTextEdit, QProgressBar, QMessageBox,
    QGroupBox, QGridLayout, QFrame, QApplication,
)
from PySide6.QtCore import QUrl

from .styles import DARK_THEME


# Common install paths for Ookla CLI
_OOKLA_PATHS_WIN = [
    "speedtest.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\speedtest\speedtest.exe"),
    os.path.expandvars(r"%PROGRAMFILES%\speedtest\speedtest.exe"),
    os.path.expandvars(r"%PROGRAMFILES(X86)%\speedtest\speedtest.exe"),
    str(Path.home() / "speedtest" / "speedtest.exe"),
    str(Path.home() / "Downloads" / "speedtest.exe"),
]

_OOKLA_PATHS_LINUX = [
    "speedtest",
    "/usr/local/bin/speedtest",
    "/usr/bin/speedtest",
    str(Path.home() / "speedtest" / "speedtest"),
]

_OOKLA_DOWNLOAD_URL = "https://www.speedtest.net/apps/cli"


def _find_speedtest() -> str | None:
    """Find the Ookla speedtest CLI binary. Returns path or None."""
    paths = _OOKLA_PATHS_WIN if sys.platform == "win32" else _OOKLA_PATHS_LINUX
    for p in paths:
        if p == "speedtest.exe" or p == "speedtest":
            # Check if in PATH
            import shutil
            found = shutil.which(p)
            if found:
                return found
        elif os.path.isfile(p):
            return p
    return None


class SpeedtestWorker(QThread):
    """Background thread that runs Ookla speedtest CLI."""

    progress_line = Signal(str)
    test_complete = Signal(dict)  # parsed JSON result
    test_error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._binary: str = ""
        self._server_id: str = ""
        self._running: bool = False
        self._process: subprocess.Popen | None = None

    def start_test(self, binary: str, server_id: str = ""):
        self._binary = binary
        self._server_id = server_id
        self._running = True
        self.start()

    def stop(self):
        self._running = False
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.kill()

    def run(self):
        try:
            cmd = [self._binary, "--format=json", "--progress=yes"]
            if self._server_id:
                cmd.extend(["--server-id", self._server_id])

            self.progress_line.emit("Starting speedtest ...")

            # Use CREATE_NO_WINDOW to avoid console popup on Windows
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=creationflags,
            )

            output_lines: list[str] = []
            for line in self._process.stdout:
                if not self._running:
                    break
                line = line.rstrip("\n").rstrip("\r")
                if not line:
                    continue
                output_lines.append(line)
                self.progress_line.emit(line)

            self._process.wait(timeout=30)
            self._process = None

            if not self._running:
                return

            # Parse the combined output — the last JSON object is the result
            full_output = "\n".join(output_lines)

            # Ookla CLI outputs progress lines + a final JSON object.
            # Try to find the result JSON (starts with { and is the last big JSON blob)
            result = self._parse_result(full_output)
            if result:
                self.test_complete.emit(result)
            else:
                self.test_error.emit("Could not parse speedtest results.\n\nRaw output:\n" + full_output[-500:])

        except FileNotFoundError:
            self.test_error.emit("speedtest CLI not found. Please download it from:\n" + _OOKLA_DOWNLOAD_URL)
        except Exception as e:
            self.test_error.emit(f"Speedtest error: {e}")

    def _parse_result(self, output: str) -> dict | None:
        """Extract the speedtest result JSON object from CLI output."""
        # Strategy: find the last { that starts a valid JSON object containing "type":"result"
        # The CLI outputs progress lines first, then a final JSON result object.

        # Try last line first (most common)
        lines = output.strip().splitlines()
        for i in range(len(lines) - 1, -1, -1):
            line = lines[i].strip()
            if line.startswith("{") and '"type"' in line and '"result"' in line:
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    pass

        # Try finding a JSON block via regex: { ... } containing "type":"result"
        m = re.search(r'(\{[^{}]*"type"\s*:\s*"result"[^{}]*\})', output)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass

        # Last resort: extract everything from last { to last }
        # and try to parse as JSON
        start = output.rfind('{"type"')
        if start >= 0:
            end = output.rfind('}')
            if end > start:
                try:
                    return json.loads(output[start:end+1])
                except json.JSONDecodeError:
                    pass

        return None


class SpeedtestDialog(QDialog):
    """Pop-out dialog for running Ookla speedtest."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Speedtest")
        self.setMinimumSize(560, 520)
        self.resize(600, 600)
        self.setStyleSheet(DARK_THEME)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        self._binary: str | None = _find_speedtest()
        self._worker = SpeedtestWorker(self)
        self._worker.progress_line.connect(self._on_progress)
        self._worker.test_complete.connect(self._on_complete)
        self._worker.test_error.connect(self._on_error)
        self._worker.finished.connect(self._on_worker_finished)

        self._setup_ui()
        self._check_binary()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # ── Top: CLI status + controls ──
        top = QHBoxLayout()

        self._cli_status = QLabel("")
        self._cli_status.setStyleSheet("font-size: 12px; padding: 4px 8px;")
        top.addWidget(self._cli_status, stretch=1)

        self._download_btn = QPushButton("📥 Download CLI")
        self._download_btn.clicked.connect(self._open_download)
        self._download_btn.setStyleSheet(
            "QPushButton { background-color: #2d3348; color: #60a5fa; padding: 5px 12px;"
            "border: 1px solid #3b3f55; border-radius: 4px; font-size: 11px; }"
            "QPushButton:hover { background-color: #3b3f55; }"
        )
        self._download_btn.setVisible(False)
        top.addWidget(self._download_btn)

        layout.addLayout(top)

        # ── Results cards ──
        results_group = QGroupBox("Results")
        results_group.setStyleSheet(
            "QGroupBox { color: #c0c0c0; font-weight: bold; font-size: 12px;"
            "border: 1px solid #252836; border-radius: 6px; margin-top: 8px; padding-top: 16px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 6px; }"
        )
        results_grid = QGridLayout(results_group)
        results_grid.setVerticalSpacing(8)
        results_grid.setHorizontalSpacing(20)

        # Ping
        results_grid.addWidget(QLabel("Ping"), 0, 0)
        self._ping_value = QLabel("— ms")
        self._ping_value.setFont(QFont("Segoe UI", 22, QFont.Weight.Bold))
        self._ping_value.setStyleSheet("color: #4ade80;")
        results_grid.addWidget(self._ping_value, 1, 0, Qt.AlignmentFlag.AlignCenter)

        # Jitter
        results_grid.addWidget(QLabel("Jitter"), 0, 1)
        self._jitter_value = QLabel("— ms")
        self._jitter_value.setFont(QFont("Segoe UI", 22, QFont.Weight.Bold))
        self._jitter_value.setStyleSheet("color: #fbbf24;")
        results_grid.addWidget(self._jitter_value, 1, 1, Qt.AlignmentFlag.AlignCenter)

        # Download
        results_grid.addWidget(QLabel("Download"), 0, 2)
        self._dl_value = QLabel("—")
        self._dl_value.setFont(QFont("Segoe UI", 28, QFont.Weight.Bold))
        self._dl_value.setStyleSheet("color: #3b82f6;")
        results_grid.addWidget(self._dl_value, 1, 2, Qt.AlignmentFlag.AlignCenter)

        # Upload
        results_grid.addWidget(QLabel("Upload"), 0, 3)
        self._ul_value = QLabel("—")
        self._ul_value.setFont(QFont("Segoe UI", 28, QFont.Weight.Bold))
        self._ul_value.setStyleSheet("color: #a78bfa;")
        results_grid.addWidget(self._ul_value, 1, 3, Qt.AlignmentFlag.AlignCenter)

        # Server / ISP info
        self._server_label = QLabel("")
        self._server_label.setStyleSheet("color: #808080; font-size: 11px;")
        results_grid.addWidget(self._server_label, 2, 0, 1, 4, Qt.AlignmentFlag.AlignCenter)

        self._isp_label = QLabel("")
        self._isp_label.setStyleSheet("color: #808080; font-size: 11px;")
        results_grid.addWidget(self._isp_label, 3, 0, 1, 4, Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(results_group)

        # ── Progress bar ──
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminate
        self._progress.setFixedHeight(6)
        self._progress.setTextVisible(False)
        self._progress.setVisible(False)
        self._progress.setStyleSheet("""
            QProgressBar {
                background-color: #13151f; border: none; border-radius: 3px;
            }
            QProgressBar::chunk {
                background-color: #3b82f6; border-radius: 3px;
            }
        """)
        layout.addWidget(self._progress)

        # ── Log output (collapsible) ──
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Consolas", 9))
        self._log.setMaximumHeight(180)
        self._log.setStyleSheet(
            "QPlainTextEdit { background-color: #0a0c14; color: #808080;"
            "border: 1px solid #252836; border-radius: 4px; padding: 4px; }"
        )
        layout.addWidget(self._log)

        # ── Bottom buttons ──
        bottom = QHBoxLayout()

        self._run_btn = QPushButton("🚀 Run Speedtest")
        self._run_btn.clicked.connect(self._toggle_test)
        self._run_btn.setStyleSheet(
            "QPushButton { background-color: #1a3d5c; color: #60a5fa; padding: 8px 20px;"
            "border: 1px solid #2d5a7f; border-radius: 6px; font-weight: bold; font-size: 13px; }"
            "QPushButton:hover { background-color: #2d5a7f; }"
            "QPushButton:disabled { background-color: #1a1d2e; color: #444; }"
        )
        bottom.addWidget(self._run_btn)

        bottom.addStretch()

        self._share_btn = QPushButton("🔗 Share Result")
        self._share_btn.clicked.connect(self._share_result)
        self._share_btn.setEnabled(False)
        self._share_btn.setStyleSheet(
            "QPushButton { background-color: #2d3348; color: #c0c0c0; padding: 6px 14px;"
            "border: 1px solid #3b3f55; border-radius: 4px; }"
            "QPushButton:hover { background-color: #3b3f55; }"
            "QPushButton:disabled { color: #444; }"
        )
        bottom.addWidget(self._share_btn)

        layout.addLayout(bottom)

    def _check_binary(self):
        if self._binary:
            self._cli_status.setText(f"✅ Ookla CLI: {self._binary}")
            self._cli_status.setStyleSheet(
                "color: #4ade80; font-size: 12px; padding: 4px 8px;"
            )
            self._download_btn.setVisible(False)
            self._run_btn.setEnabled(True)
        else:
            self._cli_status.setText(
                "⚠ Ookla CLI not found. Download from speedtest.net/apps/cli"
            )
            self._cli_status.setStyleSheet(
                "color: #fbbf24; font-size: 12px; padding: 4px 8px;"
            )
            self._download_btn.setVisible(True)
            self._run_btn.setEnabled(False)

    def _open_download(self):
        QDesktopServices.openUrl(QUrl(_OOKLA_DOWNLOAD_URL))

    def _toggle_test(self):
        if self._worker.isRunning():
            self._worker.stop()
            return

        if not self._binary:
            self._binary = _find_speedtest()
            if not self._binary:
                self._check_binary()
                return

        self._log.clear()
        self._progress.setVisible(True)
        self._run_btn.setText("■ Stop")
        self._run_btn.setStyleSheet(
            "QPushButton { background-color: #7c2d2d; color: #ef4444; padding: 8px 20px;"
            "border: 1px solid #944; border-radius: 6px; font-weight: bold; font-size: 13px; }"
            "QPushButton:hover { background-color: #944; }"
        )
        self._share_btn.setEnabled(False)

        # Reset results
        self._ping_value.setText("— ms")
        self._ping_value.setStyleSheet("color: #4ade80;")
        self._jitter_value.setText("— ms")
        self._jitter_value.setStyleSheet("color: #fbbf24;")
        self._dl_value.setText("—")
        self._dl_value.setStyleSheet("color: #3b82f6;")
        self._ul_value.setText("—")
        self._ul_value.setStyleSheet("color: #a78bfa;")
        self._server_label.setText("")
        self._isp_label.setText("")

        self._worker.start_test(self._binary)

    def _on_progress(self, line: str):
        cursor = self._log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = cursor.charFormat()

        # Color-code progress lines
        if "error" in line.lower() or "fail" in line.lower():
            fmt.setForeground(QColor("#ef4444"))
        elif "download" in line.lower() or "upload" in line.lower():
            fmt.setForeground(QColor("#60a5fa"))
        elif "ping" in line.lower() or "latency" in line.lower():
            fmt.setForeground(QColor("#4ade80"))
        elif "mbit" in line.lower() or "mbps" in line.lower():
            fmt.setForeground(QColor("#fbbf24"))
        else:
            fmt.setForeground(QColor("#808080"))

        cursor.insertText(line + "\n", fmt)
        self._log.setTextCursor(cursor)
        self._log.ensureCursorVisible()

    def _on_complete(self, result: dict):
        self._progress.setVisible(False)
        self._share_btn.setEnabled(True)

        # Parse latency
        ping_data = result.get("ping", {})
        ping_ms = ping_data.get("latency", 0)
        jitter_ms = ping_data.get("jitter", 0)

        # Parse bandwidth (bytes per second → Mbps)
        dl_data = result.get("download", {})
        ul_data = result.get("upload", {})
        dl_bps = dl_data.get("bandwidth", 0)
        ul_bps = ul_data.get("bandwidth", 0)
        dl_mbps = (dl_bps * 8) / 1_000_000 if dl_bps > 0 else 0
        ul_mbps = (ul_bps * 8) / 1_000_000 if ul_bps > 0 else 0

        # Server info
        server = result.get("server", {})
        server_name = server.get("name", "Unknown")
        server_location = server.get("location", "")
        server_country = server.get("country", "")

        # ISP
        isp = result.get("isp", "Unknown ISP")
        iface = result.get("interface", {})
        external_ip = iface.get("externalIp", "")

        # Result URL
        self._result_url = result.get("result", {}).get("url", "")

        # Update values
        self._ping_value.setText(f"{ping_ms:.0f}")
        if ping_ms < 20:
            self._ping_value.setStyleSheet("color: #4ade80;")
        elif ping_ms < 50:
            self._ping_value.setStyleSheet("color: #fbbf24;")
        else:
            self._ping_value.setStyleSheet("color: #ef4444;")

        self._jitter_value.setText(f"{jitter_ms:.1f}")
        if jitter_ms < 5:
            self._jitter_value.setStyleSheet("color: #4ade80;")
        elif jitter_ms < 15:
            self._jitter_value.setStyleSheet("color: #fbbf24;")
        else:
            self._jitter_value.setStyleSheet("color: #ef4444;")

        self._dl_value.setText(f"{dl_mbps:.1f}")
        if dl_mbps >= 100:
            self._dl_value.setStyleSheet("color: #4ade80;")
        elif dl_mbps >= 25:
            self._dl_value.setStyleSheet("color: #fbbf24;")
        else:
            self._dl_value.setStyleSheet("color: #ef4444;")

        self._ul_value.setText(f"{ul_mbps:.1f}")
        if ul_mbps >= 50:
            self._ul_value.setStyleSheet("color: #4ade80;")
        elif ul_mbps >= 10:
            self._ul_value.setStyleSheet("color: #fbbf24;")
        else:
            self._ul_value.setStyleSheet("color: #ef4444;")

        # Server/ISP info
        loc_info = f"{server_name} · {server_location}, {server_country}"
        self._server_label.setText(loc_info)

        isp_info = f"ISP: {isp}"
        if external_ip:
            isp_info += f"  |  IP: {external_ip}"
        self._isp_label.setText(isp_info)

    def _on_error(self, msg: str):
        self._progress.setVisible(False)
        cursor = self._log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = cursor.charFormat()
        fmt.setForeground(QColor("#ef4444"))
        cursor.insertText(msg + "\n", fmt)
        self._log.setTextCursor(cursor)

    def _on_worker_finished(self):
        self._run_btn.setText("🚀 Run Speedtest")
        self._run_btn.setStyleSheet(
            "QPushButton { background-color: #1a3d5c; color: #60a5fa; padding: 8px 20px;"
            "border: 1px solid #2d5a7f; border-radius: 6px; font-weight: bold; font-size: 13px; }"
            "QPushButton:hover { background-color: #2d5a7f; }"
        )
        self._progress.setVisible(False)

    def _share_result(self):
        """Open speedtest.net result page in browser."""
        url = getattr(self, "_result_url", "")
        if url:
            QDesktopServices.openUrl(QUrl(url))
        else:
            QMessageBox.information(self, "Share", "No result URL available.")

    def closeEvent(self, event):
        if self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(3000)
        event.accept()
