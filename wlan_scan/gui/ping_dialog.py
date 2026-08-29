"""Pop-out ping test dialog with continuous ping and live statistics.

Runs `ping -t` on Windows in a background thread, parses output,
and shows real-time min/avg/max latency plus packet loss.
"""

import re
import subprocess
import sys
import threading

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QFont, QColor, QTextCursor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QPlainTextEdit, QFrame, QMessageBox,
)

from .styles import DARK_THEME


# Regex for Windows ping response lines
_PING_REPLY_RE = re.compile(
    r"(?:Reply from|Antwort von|Réponse de|Respuesta desde)\s+"
    r"([\d.]+|[\w:]+)"
    r".*?(?:time|Zeit|temps|tiempo)[=<]\s*<?(\d+)\s*ms",
    re.IGNORECASE,
)
_PING_TIMEOUT_RE = re.compile(
    r"(?:Request timed out|Zeitüberschreitung|Délai d'attente|Tiempo de espera)",
    re.IGNORECASE,
)
_PING_UNREACHABLE_RE = re.compile(
    r"(?:Destination host unreachable|Destination net unreachable|"
    r"Zielhost nicht erreichbar|"
    r"Hôte de destination inaccessible|"
    r"Host de destino inaccesible)",
    re.IGNORECASE,
)
_PING_STATS_LINE_RE = re.compile(
    r"(?:Packets|Pakete|Paquets|Paquetes).*?"
    r"Sent\s*=\s*(\d+).*?Received\s*=\s*(\d+).*?Lost\s*=\s*(\d+)\s*\((\d+)%",
    re.IGNORECASE,
)
_PING_MIN_RE = re.compile(
    r"(?:Minimum|Minimum|Minimum|Mínimo)\s*=\s*(\d+)\s*ms",
    re.IGNORECASE,
)
_PING_MAX_RE = re.compile(
    r"(?:Maximum|Maximum|Maximum|Máximo)\s*=\s*(\d+)\s*ms",
    re.IGNORECASE,
)
_PING_AVG_RE = re.compile(
    r"(?:Average|Mittelwert|Moyenne|Media)\s*=\s*(\d+)\s*ms",
    re.IGNORECASE,
)


class PingWorker(QThread):
    """Background thread that runs ping and emits output lines."""

    output_line = Signal(str, str)  # (text, kind) — kind: "reply", "timeout", "unreachable", "info", "error"
    stats_updated = Signal(int, int, int, int, float, float, float)  # sent, recv, lost, loss_pct, min, avg, max

    def __init__(self, parent=None):
        super().__init__(parent)
        self._target: str = ""
        self._process: subprocess.Popen | None = None
        self._running: bool = False

        # Accumulated stats
        self._sent: int = 0
        self._received: int = 0
        self._lost: int = 0
        self._times: list[float] = []

    def start_ping(self, target: str):
        self._target = target
        self._running = True
        self._sent = 0
        self._received = 0
        self._lost = 0
        self._times = []
        self.start()

    def stop_ping(self):
        self._running = False
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()

    def run(self):
        try:
            # -t = continuous ping on Windows
            # -n with a large count also works cross-platform
            if sys.platform == "win32":
                cmd = ["ping", "-t", self._target]
            else:
                cmd = ["ping", self._target]  # Linux/macOS: continuous by default

            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )

            for line in self._process.stdout:
                if not self._running:
                    break
                line = line.rstrip("\n").rstrip("\r")
                if not line:
                    continue

                kind = "info"
                m = _PING_REPLY_RE.search(line)
                if m:
                    kind = "reply"
                    self._sent += 1
                    self._received += 1
                    ms = float(m.group(2))
                    self._times.append(ms)
                    # Keep last 1000 times to avoid unbounded memory
                    if len(self._times) > 1000:
                        self._times = self._times[-1000:]
                elif _PING_TIMEOUT_RE.search(line):
                    kind = "timeout"
                    self._sent += 1
                    self._lost += 1
                elif _PING_UNREACHABLE_RE.search(line):
                    kind = "unreachable"
                    self._sent += 1
                    self._lost += 1
                elif _PING_STATS_LINE_RE.search(line):
                    kind = "stats"
                    sm = _PING_STATS_LINE_RE.search(line)
                    if sm:
                        sent = int(sm.group(1))
                        recv = int(sm.group(2))
                        lost = int(sm.group(3))
                        loss_pct = int(sm.group(4))
                        # Continue reading next lines for min/max/avg
                        continue
                elif _PING_MIN_RE.search(line) or _PING_MAX_RE.search(line) or _PING_AVG_RE.search(line):
                    # Skip individual stat lines — we compute our own
                    kind = "info"

                self.output_line.emit(line, kind)

                # Emit stats after each reply/timeout
                if kind in ("reply", "timeout", "unreachable"):
                    self._emit_stats()

            # Process finished
            if self._running and self._process:
                self._process.stdout.close()
                self._process.wait()

            # Final stats
            self._emit_stats()

        except FileNotFoundError:
            self.output_line.emit("⚠ ping command not found", "error")
        except Exception as e:
            self.output_line.emit(f"⚠ Error: {e}", "error")

        self._process = None

    def _emit_stats(self):
        if self._sent == 0:
            return
        lost_count = self._sent - self._received
        loss_pct = round((lost_count / self._sent) * 100, 1)
        if self._times:
            min_t = min(self._times)
            max_t = max(self._times)
            avg_t = sum(self._times) / len(self._times)
        else:
            min_t = max_t = avg_t = 0.0
        self.stats_updated.emit(self._sent, self._received, lost_count, int(loss_pct), min_t, avg_t, max_t)


class PingDialog(QDialog):
    """Pop-out ping test window with live output and statistics."""

    def __init__(self, target: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ping Test")
        self.setMinimumSize(620, 420)
        self.resize(680, 500)
        self.setStyleSheet(DARK_THEME)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        self._worker = PingWorker(self)
        self._worker.output_line.connect(self._on_output)
        self._worker.stats_updated.connect(self._on_stats)
        self._worker.finished.connect(self._on_worker_finished)

        self._setup_ui(target)

    def _setup_ui(self, target: str):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        # ── Top bar: target + buttons ──
        top = QHBoxLayout()

        top.addWidget(QLabel("Target:"))
        self._target_input = QLineEdit()
        self._target_input.setPlaceholderText("IP address or hostname (e.g. 192.168.1.1)")
        self._target_input.setText(target)
        self._target_input.returnPressed.connect(self._toggle_ping)
        self._target_input.setStyleSheet("""
            QLineEdit {
                background-color: #1a1d2e; border: 1px solid #353848;
                border-radius: 4px; padding: 5px 8px; color: #e0e0e0;
                font-family: 'Consolas', monospace; font-size: 13px;
            }
            QLineEdit:focus { border-color: #3b82f6; }
        """)
        top.addWidget(self._target_input, stretch=1)

        self._start_btn = QPushButton("▶ Start")
        self._start_btn.clicked.connect(self._toggle_ping)
        self._start_btn.setStyleSheet(
            "QPushButton { background-color: #1a4d2e; color: #4ade80; padding: 5px 14px;"
            "border: 1px solid #2d6b3f; border-radius: 4px; font-weight: bold; }"
            "QPushButton:hover { background-color: #2d6b3f; }"
        )
        top.addWidget(self._start_btn)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.clicked.connect(self._clear_output)
        self._clear_btn.setStyleSheet(
            "QPushButton { background-color: #252836; color: #c0c0c0; padding: 5px 12px;"
            "border: 1px solid #353848; border-radius: 4px; }"
            "QPushButton:hover { background-color: #353848; }"
        )
        top.addWidget(self._clear_btn)

        layout.addLayout(top)

        # ── Stats bar ──
        self._stats_label = QLabel("Ready — enter a target and click Start")
        self._stats_label.setStyleSheet(
            "color: #808080; font-family: 'Consolas', monospace; font-size: 12px;"
            "padding: 4px 8px; background-color: #13151f; border: 1px solid #252836; border-radius: 4px;"
        )
        layout.addWidget(self._stats_label)

        # ── Output area ──
        self._output = QPlainTextEdit()
        self._output.setReadOnly(True)
        self._output.setFont(QFont("Consolas", 10))
        self._output.setStyleSheet("""
            QPlainTextEdit {
                background-color: #0a0c14; color: #c0c0c0;
                border: 1px solid #252836; border-radius: 4px;
                padding: 6px;
            }
        """)
        layout.addWidget(self._output, stretch=1)

        # ── Bottom actions ──
        bottom = QHBoxLayout()

        self._auto_scroll_cb_label = QLabel("Auto-scroll")
        self._auto_scroll_cb_label.setStyleSheet("color: #808080; font-size: 11px;")
        bottom.addWidget(self._auto_scroll_cb_label)

        self._auto_scroll = QPushButton("✓")
        self._auto_scroll.setCheckable(True)
        self._auto_scroll.setChecked(True)
        self._auto_scroll.setFixedSize(24, 24)
        self._auto_scroll.setStyleSheet(
            "QPushButton { background-color: #252836; color: #4ade80; border: 1px solid #353848; "
            "border-radius: 3px; font-size: 11px; }"
            "QPushButton:checked { background-color: #1a4d2e; }"
            "QPushButton:hover { border-color: #3b82f6; }"
        )
        bottom.addWidget(self._auto_scroll)

        bottom.addStretch()

        self._line_count_label = QLabel("")
        self._line_count_label.setStyleSheet("color: #555; font-size: 11px;")
        bottom.addWidget(self._line_count_label)

        layout.addLayout(bottom)

    def _toggle_ping(self):
        if self._worker.isRunning():
            self._worker.stop_ping()
            self._start_btn.setText("▶ Start")
            self._start_btn.setStyleSheet(
                "QPushButton { background-color: #1a4d2e; color: #4ade80; padding: 5px 14px;"
                "border: 1px solid #2d6b3f; border-radius: 4px; font-weight: bold; }"
                "QPushButton:hover { background-color: #2d6b3f; }"
            )
        else:
            target = self._target_input.text().strip()
            if not target:
                QMessageBox.warning(self, "Ping", "Please enter a target IP or hostname.")
                return
            self._output.clear()
            self._line_count = 0
            self._line_count_label.setText("")
            self._output.appendPlainText(f"Pinging {target} ...\n")
            self._worker.start_ping(target)
            self._start_btn.setText("■ Stop")
            self._start_btn.setStyleSheet(
                "QPushButton { background-color: #7c2d2d; color: #ef4444; padding: 5px 14px;"
                "border: 1px solid #944; border-radius: 4px; font-weight: bold; }"
                "QPushButton:hover { background-color: #944; }"
            )

    def _clear_output(self):
        if self._worker.isRunning():
            self._worker.stop_ping()
            self._start_btn.setText("▶ Start")
            self._start_btn.setStyleSheet(
                "QPushButton { background-color: #1a4d2e; color: #4ade80; padding: 5px 14px;"
                "border: 1px solid #2d6b3f; border-radius: 4px; font-weight: bold; }"
                "QPushButton:hover { background-color: #2d6b3f; }"
            )
        self._output.clear()
        self._line_count = 0
        self._line_count_label.setText("")
        self._stats_label.setText("Ready — enter a target and click Start")
        self._stats_label.setStyleSheet(
            "color: #808080; font-family: 'Consolas', monospace; font-size: 12px;"
            "padding: 4px 8px; background-color: #13151f; border: 1px solid #252836; border-radius: 4px;"
        )

    def _on_output(self, line: str, kind: str):
        cursor = self._output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        if kind == "reply":
            fmt = cursor.charFormat()
            fmt.setForeground(QColor("#4ade80"))
            cursor.insertText(line + "\n", fmt)
        elif kind == "timeout":
            fmt = cursor.charFormat()
            fmt.setForeground(QColor("#fbbf24"))
            cursor.insertText(line + "\n", fmt)
        elif kind == "unreachable":
            fmt = cursor.charFormat()
            fmt.setForeground(QColor("#ef4444"))
            cursor.insertText(line + "\n", fmt)
        elif kind == "error":
            fmt = cursor.charFormat()
            fmt.setForeground(QColor("#ef4444"))
            fmt.setFontWeight(700)
            cursor.insertText(line + "\n", fmt)
        else:
            fmt = cursor.charFormat()
            fmt.setForeground(QColor("#808080"))
            cursor.insertText(line + "\n", fmt)

        if self._auto_scroll.isChecked():
            self._output.setTextCursor(cursor)
            self._output.ensureCursorVisible()

        self._line_count = getattr(self, "_line_count", 0) + 1
        self._line_count_label.setText(f"{self._line_count} lines")

        # Trim buffer to prevent memory issues during very long runs
        if self._line_count > 5000:
            self._trim_buffer()

    def _on_stats(self, sent: int, recv: int, lost: int, loss_pct: int,
                   min_t: float, avg_t: float, max_t: float):
        """Update the stats bar."""
        if sent == 0:
            return

        ms = lambda v: f"{v:.1f} ms" if v > 0 else "—"
        stats_text = (
            f"Sent: {sent}  |  Recv: {recv}  |  Lost: {lost} ({loss_pct}%)  "
            f"|  Min: {ms(min_t)}  |  Avg: {ms(avg_t)}  |  Max: {ms(max_t)}"
        )

        # Color-code by loss
        if loss_pct >= 10:
            color = "#ef4444"
        elif loss_pct > 0:
            color = "#fbbf24"
        elif avg_t > 100:
            color = "#fbbf24"
        else:
            color = "#4ade80"

        self._stats_label.setText(stats_text)
        self._stats_label.setStyleSheet(
            f"color: {color}; font-family: 'Consolas', monospace; font-size: 12px;"
            "padding: 4px 8px; background-color: #13151f; border: 1px solid #252836; border-radius: 4px;"
        )

    def _on_worker_finished(self):
        self._start_btn.setText("▶ Start")
        self._start_btn.setStyleSheet(
            "QPushButton { background-color: #1a4d2e; color: #4ade80; padding: 5px 14px;"
            "border: 1px solid #2d6b3f; border-radius: 4px; font-weight: bold; }"
            "QPushButton:hover { background-color: #2d6b3f; }"
        )

    def _trim_buffer(self):
        """Remove oldest lines to keep memory usage bounded."""
        doc = self._output.document()
        # Remove first ~2000 lines
        block = doc.findBlockByLineNumber(2000)
        if block.isValid():
            cursor = QTextCursor(block)
            cursor.movePosition(QTextCursor.MoveOperation.Start, QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
            self._line_count = doc.blockCount()

    def closeEvent(self, event):
        if self._worker.isRunning():
            self._worker.stop_ping()
        event.accept()

    def set_target(self, target: str):
        """Set the target from outside (e.g. from main window selection)."""
        self._target_input.setText(target)
