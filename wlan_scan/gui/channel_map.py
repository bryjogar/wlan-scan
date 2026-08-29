"""Visual channel map widget — shows Wi-Fi networks positioned on their channels.

Draws a horizontal spectrum map with channels on the x-axis and
signal strength on the y-axis. Networks appear as colored rectangles
whose width represents channel width and height represents signal strength.

SSID labels are drawn inside rects when tall enough (strong signals) or
with collision-avoided placement below for weaker signals.
"""

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QLinearGradient, QPainterPath,
)
from PySide6.QtWidgets import QWidget, QToolTip

from ..models import Network, BSSEntry
from .styles import BAND_COLORS, signal_color, signal_label

# Channel map configuration
CHANNEL_CONFIGS = {
    "2.4 GHz": {
        "channels": list(range(1, 12)),  # 1-11 (US)
        "freq_start": 2401,
        "freq_end": 2499,
        "label": "2.4 GHz",
        "width_mhz": 22,  # per channel
    },
    "5 GHz": {
        "channels": [
            36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112,
            116, 120, 124, 128, 132, 136, 140, 144, 149, 153, 157, 161, 165,
        ],
        "freq_start": 5160,
        "freq_end": 5840,
        "label": "5 GHz",
        "width_mhz": 20,
    },
    "6 GHz": {
        "channels": list(range(1, 234, 4)),  # simplified
        "freq_start": 5935,
        "freq_end": 7115,
        "label": "6 GHz",
        "width_mhz": 20,
    },
}

# Minimum rect height needed to draw label inside instead of below
LABEL_INSIDE_MIN_HEIGHT = 20
BAR_MIN_HEIGHT = 8
BAR_ALPHA = 180  # semi-transparent bars so overlaps are visible


class ChannelMapWidget(QWidget):
    """Custom-painted widget showing a visual channel map."""

    PADDING_LEFT = 60
    PADDING_RIGHT = 20
    PADDING_TOP = 40
    PADDING_BOTTOM = 50
    CHANNEL_LABEL_TOP = 22  # px above PADDING_TOP + h for channel number area
    NETWORK_MIN_HEIGHT = 14
    NETWORK_MAX_HEIGHT = 40
    NETWORK_RADIUS = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        self.setMouseTracking(True)
        self._networks: list[Network] = []
        self._band: str = "2.4 GHz"
        self._hovered_bss: BSSEntry | None = None
        self._bss_rects: list[tuple[QRectF, BSSEntry]] = []
        self._hidden_ssids: set[str] = set()

    def set_data(self, networks: list[Network], band: str = "2.4 GHz"):
        """Update the channel map with new scan data."""
        self._networks = [
            n for n in networks
            if any(b.band == band for b in n.bss_list)
        ]
        self._band = band
        self.update()

    def toggle_ssid(self, ssid: str, visible: bool):
        """Show or hide a specific SSID on the channel map."""
        if visible:
            self._hidden_ssids.discard(ssid)
        else:
            self._hidden_ssids.add(ssid)
        self.update()

    def get_ssids(self) -> list[str]:
        """Return unique SSIDs visible on the current band."""
        seen: set[str] = set()
        for net in self._networks:
            if net.ssid and net.ssid not in seen:
                seen.add(net.ssid)
        return sorted(seen)

    def paintEvent(self, event):
        if not self._networks:
            return

        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            config = CHANNEL_CONFIGS.get(self._band, CHANNEL_CONFIGS["2.4 GHz"])
            channels = config["channels"]
            freq_start = config["freq_start"]
            freq_end = config["freq_end"]

            w = self.width() - self.PADDING_LEFT - self.PADDING_RIGHT
            h = self.height() - self.PADDING_TOP - self.PADDING_BOTTOM
            freq_range = freq_end - freq_start

            self._bss_rects = []

            # ── Background grid ──
            painter.setPen(QPen(QColor("#1e2130"), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)

            # Horizontal grid lines
            for rssi in range(-30, -100, -10):
                y = self.PADDING_TOP + int(((-30 - rssi) / 70.0) * h)
                painter.drawLine(self.PADDING_LEFT, y, self.PADDING_LEFT + w, y)

            # X-axis line (separates graph from channel labels)
            axis_y = self.PADDING_TOP + h
            painter.setPen(QPen(QColor("#808090"), 1))
            painter.drawLine(self.PADDING_LEFT, axis_y, self.PADDING_LEFT + w, axis_y)

            band_color = BAND_COLORS.get(self._band, "#3b82f6")

            # Band label
            painter.setPen(QPen(QColor(band_color)))
            painter.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
            painter.drawText(
                QRectF(0, 5, self.width(), 30),
                Qt.AlignmentFlag.AlignCenter,
                f"{self._band} Band",
            )

            # RSSI axis labels
            painter.setPen(QPen(QColor("#707080")))
            painter.setFont(QFont("Segoe UI", 9))
            for rssi in range(-30, -100, -10):
                y = self.PADDING_TOP + int(((-30 - rssi) / 70.0) * h)
                painter.drawText(
                    QRectF(5, y - 10, self.PADDING_LEFT - 10, 20),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    f"{rssi}",
                )
            painter.drawText(
                QRectF(5, self.PADDING_TOP - 10, self.PADDING_LEFT - 10, 20),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                "dBm",
            )

            # ── Collect & sort BSS entries ──
            all_bss: list[tuple[BSSEntry, str]] = []  # (bss, ssid)
            for net in self._networks:
                if net.ssid in self._hidden_ssids:
                    continue
                for bss in net.bss_list:
                    if bss.band == self._band:
                        all_bss.append((bss, net.ssid))

            all_bss.sort(key=lambda item: (item[0].channel_width or 20, item[0].rssi), reverse=True)

            # ── First pass: compute rect positions ──
            drawn_positions: list[tuple[float, float, float, float]] = []  # (cx, y, half_w, bh)

            rect_data: list[dict] = []  # hold rect info for drawing + label phase

            for bss, ssid in all_bss:
                center_mhz = bss.center_frequency if bss.center_frequency > 0 else _channel_to_freq(bss.channel, self._band)
                center_mhz = max(freq_start, min(freq_end, center_mhz))

                x_center = self.PADDING_LEFT + ((center_mhz - freq_start) / freq_range) * w

                ch_width_mhz = bss.channel_width or 20
                rect_width = max(8, (ch_width_mhz / freq_range) * w)
                half_w = rect_width / 2
                x_center = max(self.PADDING_LEFT + half_w, min(self.PADDING_LEFT + w - half_w, x_center))
                x = x_center - half_w

                rssi_ratio = ((-30 - max(-90, bss.rssi)) / 70.0)
                rssi_ratio = max(0.0, min(1.0, rssi_ratio))

                # Y position: top of bar sits at the signal level on the chart
                y_top = self.PADDING_TOP + int(rssi_ratio * h)
                # Bar fills downward from signal level to bottom of chart
                full_height = (self.PADDING_TOP + h) - y_top
                rect_height = max(BAR_MIN_HEIGHT, full_height)
                y = y_top

                # Stagger overlapping rects vertically (small nudge since bars are already filled)
                for ox, oy, ohw, obh in drawn_positions:
                    if abs(x_center - ox) < ohw + half_w + 2:
                        y += 6
                        y = min(y, self.PADDING_TOP + h - rect_height)
                        break

                drawn_positions.append((x_center, y, half_w, rect_height))

                color = QColor(signal_color(bss.rssi))
                rect = QRectF(x, y, rect_width, rect_height)

                rect_data.append({
                    "rect": rect,
                    "bss": bss,
                    "ssid": ssid,
                    "color": color,
                    "ch_width_mhz": ch_width_mhz,
                    "rect_height": rect_height,
                    "rect_width": rect_width,
                    "center_mhz": center_mhz,
                })

            # ── Track label positions for collision avoidance ──
            placed_labels: list[QRectF] = []

            def _label_collides(candidate: QRectF) -> bool:
                for existing in placed_labels:
                    if candidate.intersects(existing):
                        return True
                return False

            # ── Second pass: draw rects and labels ──
            label_font = QFont("Segoe UI", 8, QFont.Weight.Bold)
            label_font_small = QFont("Segoe UI", 7, QFont.Weight.Bold)

            for rd in rect_data:
                bss = rd["bss"]
                ssid = rd["ssid"]
                color = rd["color"]
                ch_width_mhz = rd["ch_width_mhz"]
                rect = rd["rect"]
                rect_height = rd["rect_height"]
                rect_width = rd["rect_width"]
                x = rect.x()
                y = rect.y()
                center_mhz = rd["center_mhz"]

                if bss == self._hovered_bss:
                    color = color.lighter(140)

                # ── Draw the rect(s) ──
                if ch_width_mhz > 20:
                    sub_width_mhz = 20
                    num_subs = ch_width_mhz // sub_width_mhz
                    sub_w = rect_width / num_subs
                    primary_freq = _channel_to_freq(bss.channel, self._band)
                    for sub_i in range(num_subs):
                        sub_rect = QRectF(x + sub_i * sub_w, y, sub_w, rect_height)
                        sub_start_freq = center_mhz - ch_width_mhz / 2 + sub_i * sub_width_mhz
                        sub_end_freq = sub_start_freq + sub_width_mhz
                        is_primary = sub_start_freq <= primary_freq < sub_end_freq

                        sub_grad = QLinearGradient(sub_rect.x(), sub_rect.y(), sub_rect.x(), sub_rect.y() + rect_height)
                        if is_primary:
                            primary_bright = QColor(color.lighter(130))
                            primary_bright.setAlpha(BAR_ALPHA)
                            primary_dim = QColor(color)
                            primary_dim.setAlpha(BAR_ALPHA)
                            sub_grad.setColorAt(0.0, primary_bright)
                            sub_grad.setColorAt(1.0, primary_dim)
                            sub_border = color.darker(120)
                        else:
                            ext_color = QColor(color)
                            h_val, s, l_val, a = ext_color.getHsl()
                            ext_color.setHsl(h_val, int(s * 0.35), l_val + 20, a)
                            ext_bright = QColor(ext_color.lighter(110))
                            ext_bright.setAlpha(BAR_ALPHA)
                            ext_dim = QColor(ext_color)
                            ext_dim.setAlpha(BAR_ALPHA)
                            sub_grad.setColorAt(0.0, ext_bright)
                            sub_grad.setColorAt(1.0, ext_dim)
                            sub_border = ext_color.darker(120)

                        painter.fillRect(sub_rect, QBrush(sub_grad))
                        painter.setPen(QPen(sub_border, 1))
                        painter.drawRect(sub_rect)
                else:
                    # Single 20 MHz channel — semi-transparent fill from top to bottom
                    bar_color = QColor(color)
                    bar_color.setAlpha(BAR_ALPHA)

                    gradient = QLinearGradient(x, y, x, y + rect_height)
                    bright = QColor(color)
                    bright.setAlpha(BAR_ALPHA)
                    dim = QColor(color.darker(150))
                    dim.setAlpha(BAR_ALPHA)
                    gradient.setColorAt(0.0, bright)
                    gradient.setColorAt(1.0, dim)

                    path = QPainterPath()
                    path.addRoundedRect(rect, self.NETWORK_RADIUS, self.NETWORK_RADIUS)
                    painter.fillPath(path, QBrush(gradient))
                    painter.setPen(QPen(color.darker(120), 1))
                    painter.drawPath(path)

                # ── Draw label ──
                label = ssid or "<Hidden>"
                if len(label) > 12:
                    label = label[:11] + "…"

                font = label_font if len(label) <= 8 else label_font_small

                if rect_height >= LABEL_INSIDE_MIN_HEIGHT:
                    # Draw label INSIDE the rect — white text with dark outline
                    painter.setFont(font)
                    text_color = QColor("#ffffff")
                    outline_color = QColor("#1a1d2e")

                    # Draw outlined text: render offset in 4 directions as outline
                    inner_label_rect = QRectF(x, y, rect_width, rect_height)
                    painter.setPen(QPen(outline_color))
                    for dx, dy in [(-1, -1), (-1, 1), (1, -1), (1, 1)]:
                        outline_rect = inner_label_rect.translated(dx, dy)
                        painter.drawText(outline_rect, Qt.AlignmentFlag.AlignCenter, label)
                    painter.setPen(QPen(text_color))
                    painter.drawText(inner_label_rect, Qt.AlignmentFlag.AlignCenter, label)

                    placed_labels.append(rect)

                else:
                    # Draw label BELOW rect with collision avoidance
                    painter.setFont(font)
                    max_label_y = self.PADDING_TOP + h + 2

                    # Try preferred position directly below rect center
                    label_w = max(60, rect_width + 20)
                    label_h = 13
                    best_y = min(y + rect_height + 3, max_label_y - label_h)
                    label_rect = QRectF(
                        x + (rect_width - label_w) / 2, best_y,
                        label_w, label_h,
                    )

                    # If collides, try shifting up / wider
                    if _label_collides(label_rect):
                        # Try drawing above the rect
                        alt_y = max(self.PADDING_TOP - label_h, y - label_h - 2)
                        label_rect = QRectF(
                            x + (rect_width - label_w) / 2, alt_y,
                            label_w, label_h,
                        )

                    # If still collides, try right-offset
                    if _label_collides(label_rect):
                        label_rect = QRectF(
                            x + rect_width + 2, y + (rect_height - label_h) / 2,
                            label_w, label_h,
                        )

                    # If still collides, skip the label entirely (too crowded)
                    if _label_collides(label_rect):
                        # Draw a tiny dot or nothing — tooltip on hover still works
                        pass
                    else:
                        # Draw a thin dark backdrop behind label for readability
                        bg_rect = label_rect.adjusted(-1, -1, 1, 1)
                        painter.fillRect(bg_rect, QColor(0, 0, 0, 140))
                        painter.setPen(QPen(QColor("#e0e0e0")))
                        painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, label)
                        placed_labels.append(label_rect)

                self._bss_rects.append((rect, bss))

            # ── Channel grid lines — drawn ON TOP of network rects ──
            if self._band == "5 GHz":
                label_channels = {36, 40, 44, 48, 52, 56, 60, 64, 100, 116, 132, 149, 157, 165}
            else:
                label_channels = set(channels)

            for ch in channels:
                freq = _channel_to_freq(ch, self._band)
                if freq < freq_start or freq > freq_end:
                    continue
                x_ch = self.PADDING_LEFT + int(((freq - freq_start) / freq_range) * w)
                painter.setPen(QPen(QColor("#555570"), 1, Qt.PenStyle.DashLine))
                painter.drawLine(x_ch, self.PADDING_TOP, x_ch, self.PADDING_TOP + h)
                if ch in label_channels:
                    painter.setPen(QPen(QColor("#808090")))
                    painter.setFont(QFont("Segoe UI", 9))
                    painter.drawText(
                        QRectF(x_ch - 15, self.PADDING_TOP + h + self.CHANNEL_LABEL_TOP, 30, 20),
                        Qt.AlignmentFlag.AlignCenter,
                        str(ch),
                    )

            # Axis label
            painter.setPen(QPen(QColor("#808090")))
            painter.setFont(QFont("Segoe UI", 9))
            painter.drawText(
                QRectF(self.PADDING_LEFT + w - 40, self.PADDING_TOP + h + self.CHANNEL_LABEL_TOP, 40, 20),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                "Channel",
            )


    def mouseMoveEvent(self, event):
        pos = event.position()
        old = self._hovered_bss
        self._hovered_bss = None

        for rect, bss in self._bss_rects:
            if rect.contains(pos.x(), pos.y()):
                self._hovered_bss = bss
                break

        if self._hovered_bss != old:
            self.update()

        if self._hovered_bss:
            b = self._hovered_bss
            tooltip = (
                f"<b>{b.ssid or 'Hidden'}</b><br>"
                f"BSSID: {b.bssid}<br>"
                f"Channel: {b.channel} ({b.channel_width} MHz)<br>"
                f"RSSI: {b.rssi} dBm · {signal_label(b.rssi)}<br>"
                f"Security: {b.security}<br>"
                f"PHY: {b.phy_type}<br>"
                f"Vendor: {b.vendor}<br>"
            )
            if b.channel_utilization > 0:
                tooltip += f"Utilization: {b.channel_utilization}%<br>"
            if b.station_count > 0:
                tooltip += f"Clients: {b.station_count}<br>"
            QToolTip.showText(event.globalPos(), tooltip, self)
        else:
            QToolTip.hideText()

        super().mouseMoveEvent(event)


def _channel_to_freq(channel: int, band: str) -> int:
    """Convert channel number to center frequency in MHz."""
    if band == "2.4 GHz":
        return 2407 + channel * 5
    elif band == "5 GHz":
        return 5000 + channel * 5
    elif band == "6 GHz":
        return 5950 + channel * 5
    return 2407
