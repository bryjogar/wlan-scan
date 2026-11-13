"""CSV export shared by the GUI and the CLI (one writer, one column set)."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import IO, Union

from .models import ScanResult

COLUMNS = [
    "Timestamp", "SSID", "BSSID", "RSSI (dBm)", "Channel", "Center Channel", "Band",
    "Channel Width (MHz)", "Security", "Auth", "Cipher", "PMF", "PHY", "Wi-Fi",
    "Vendor", "Max Rate (Mbps)", "Spatial Streams", "Channel Utilization %",
    "Clients", "Center Freq (MHz)", "Connected",
]


def _blank0(v, fmt="{}"):
    return fmt.format(v) if v else ""


def scan_rows(result: ScanResult):
    ts = result.timestamp.isoformat(timespec="seconds")
    conn = (result.connected_bssid or "").upper()
    for net in result.networks:
        for b in net.bss_list:
            yield [
                ts, net.ssid, b.bssid, b.rssi, b.channel,
                b.center_channel if b.center_channel and b.channel_width > 20 else "",
                b.band, b.channel_width, b.security, b.auth_algo, b.cipher, b.pmf,
                b.phy_type, b.wifi_generation, b.vendor,
                _blank0(b.max_rate, "{:.1f}"), _blank0(b.spatial_streams),
                _blank0(b.channel_utilization, "{:.1f}"), _blank0(b.station_count),
                _blank0(b.center_frequency), "yes" if b.bssid == conn and conn else "",
            ]


def write_scan_csv(result: ScanResult, dest: Union[str, Path, IO[str]]) -> int:
    """Write one scan as CSV. Returns the number of BSS rows written."""
    if isinstance(dest, (str, Path)):
        with open(dest, "w", newline="", encoding="utf-8") as f:
            return write_scan_csv(result, f)
    w = csv.writer(dest)
    w.writerow(COLUMNS)
    n = 0
    for row in scan_rows(result):
        w.writerow(row)
        n += 1
    return n
