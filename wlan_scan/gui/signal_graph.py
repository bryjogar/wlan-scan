"""Real-time signal strength graph using QtCharts."""

import time
from collections import defaultdict
from dataclasses import dataclass, field

from PySide6.QtCore import Qt, QPointF, QTimer
from PySide6.QtGui import QColor, QPen, QFont, QPainter
from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis, QDateTimeAxis
from PySide6.QtWidgets import QWidget, QVBoxLayout

from ..models import BSSEntry, Network
from .styles import signal_color, BAND_COLORS


@dataclass
class TrackedNetwork:
    """Tracks RSSI history for one BSSID."""
    bssid: str
    ssid: str
    band: str
    channel: int
    history: list[tuple[float, int]] = field(default_factory=list)
    color: QColor = field(default_factory=lambda: QColor("#3b82f6"))
    series: QLineSeries | None = None

    MAX_POINTS = 120  # 2 minutes at 1 sample/sec


class SignalGraphWidget(QWidget):
    """Real-time signal strength chart."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._chart = QChart()
        self._chart.setBackgroundBrush(QColor("#13151f"))
        self._chart.setTitle("Signal Strength Over Time")
        self._chart.setTitleFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self._chart.titleBrush().setColor(QColor("#c0c0c0"))
        self._chart.legend().setLabelColor(QColor("#a0a0a0"))
        self._chart.legend().setFont(QFont("Segoe UI", 9))
        self._chart.setTheme(QChart.ChartTheme.ChartThemeDark)
        self._chart.setAnimationOptions(QChart.AnimationOption.SeriesAnimations)

        # Axes
        self._axis_y = QValueAxis()
        self._axis_y.setRange(-100, -20)
        self._axis_y.setLabelFormat("%d dBm")
        self._axis_y.setTitleText("RSSI (dBm)")
        self._axis_y.setTitleFont(QFont("Segoe UI", 9))
        self._axis_y.setLabelsFont(QFont("Segoe UI", 9))
        self._axis_y.setGridLineColor(QColor("#1e2130"))
        self._axis_y.setLabelsColor(QColor("#808080"))
        self._axis_y.setShadesBrush(QColor("#181b26"))
        self._axis_y.setShadesColor(QColor("#181b26"))
        self._axis_y.setShadesPen(QPen(Qt.PenStyle.NoPen))
        self._axis_y.setShadesVisible(True)
        self._chart.addAxis(self._axis_y, Qt.AlignmentFlag.AlignLeft)

        self._axis_x = QValueAxis()
        self._axis_x.setRange(0, 120)
        self._axis_x.setLabelFormat("%d s")
        self._axis_x.setTitleText("Time (seconds ago)")
        self._axis_x.setTitleFont(QFont("Segoe UI", 9))
        self._axis_x.setLabelsFont(QFont("Segoe UI", 9))
        self._axis_x.setGridLineColor(QColor("#1e2130"))
        self._axis_x.setLabelsColor(QColor("#808080"))
        self._chart.addAxis(self._axis_x, Qt.AlignmentFlag.AlignBottom)

        # Chart view
        self._chart_view = QChartView(self._chart)
        self._chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._chart_view)

        # Tracking state
        self._tracked: dict[str, TrackedNetwork] = {}
        self._tracked_ssids: set[str] = set()  # SSIDs we're tracking
        self._auto_track_count: int = 6  # auto-track top N networks
        self._sample_count: int = 0

        # Update timer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance_x_axis)
        self._timer.start(1000)  # 1 Hz tick

    def add_sample(self, networks: list[Network]):
        """Feed new scan results into the graph."""
        now = time.time()
        self._sample_count += 1

        # Collect all BSS entries from tracked SSIDs
        active_bssids: set[str] = set()

        for net in networks:
            for bss in net.bss_list:
                active_bssids.add(bss.bssid)

                # Track if this SSID is in our watch list
                if net.ssid not in self._tracked_ssids and len(self._tracked) < self._auto_track_count:
                    if net.ssid and net.ssid != "<Hidden>":
                        self._tracked_ssids.add(net.ssid)

                if net.ssid not in self._tracked_ssids:
                    continue

                if bss.bssid not in self._tracked:
                    color = QColor(signal_color(bss.rssi))
                    tn = TrackedNetwork(
                        bssid=bss.bssid,
                        ssid=net.ssid,
                        band=bss.band,
                        channel=bss.channel,
                        color=color,
                    )
                    tn.history.append((now, bss.rssi))
                    self._tracked[bss.bssid] = tn
                else:
                    tn = self._tracked[bss.bssid]
                    tn.history.append((now, bss.rssi))

                    # Update color based on latest RSSI
                    tn.color = QColor(signal_color(bss.rssi))

                    # Trim history
                    if len(tn.history) > tn.MAX_POINTS:
                        tn.history = tn.history[-tn.MAX_POINTS:]

        # Remove tracked BSSIDs that are no longer visible
        for bssid in list(self._tracked.keys()):
            if bssid not in active_bssids:
                # Keep for a while in case it comes back
                tn = self._tracked[bssid]
                if tn.history and (now - tn.history[-1][0]) > 30:
                    del self._tracked[bssid]
                    if tn.ssid in self._tracked_ssids:
                        # Keep SSID tracked but remove dead BSSID
                        pass

        self._update_series()

    def tracked_ssids(self) -> set[str]:
        """Return the set of tracked SSIDs."""
        return self._tracked_ssids.copy()

    def track_ssid(self, ssid: str):
        """Start tracking a specific SSID."""
        self._tracked_ssids.add(ssid)

    def untrack_ssid(self, ssid: str):
        """Stop tracking an SSID."""
        self._tracked_ssids.discard(ssid)
        for bssid, tn in list(self._tracked.items()):
            if tn.ssid == ssid:
                del self._tracked[bssid]

    def clear_tracking(self):
        """Stop tracking all networks."""
        self._tracked_ssids.clear()
        self._tracked.clear()
        for series in self._chart.series():
            self._chart.removeSeries(series)

    def _update_series(self):
        """Sync chart series with tracked data."""
        now = time.time()
        current_bssids = set()

        for bssid, tn in self._tracked.items():
            if not tn.history:
                continue
            current_bssids.add(bssid)

            if tn.series is None:
                tn.series = QLineSeries()
                tn.series.setName(f"{tn.ssid} ({tn.band} ch.{tn.channel})")
                tn.series.setPen(QPen(tn.color, 2))
                self._chart.addSeries(tn.series)
                tn.series.attachAxis(self._axis_x)
                tn.series.attachAxis(self._axis_y)

            # Update series data
            points = []
            for ts, rssi in tn.history:
                x = max(0, now - ts)
                points.append(QPointF(x, rssi))
            tn.series.replace(points)

        # Remove series for dead BSSIDs
        for bssid in list(self._tracked.keys()):
            if bssid not in current_bssids:
                tn = self._tracked[bssid]
                if tn.series:
                    self._chart.removeSeries(tn.series)
                del self._tracked[bssid]

    def _advance_x_axis(self):
        """Update x-axis range to show rolling window."""
        if self._sample_count > 60:
            self._axis_x.setRange(0, 120)
        else:
            self._axis_x.setRange(0, max(30, self._sample_count + 5))

