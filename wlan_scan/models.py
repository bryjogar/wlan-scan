"""Data models for WiFi network scan results."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class BSSEntry:
    """A single Basic Service Set (AP radio)."""

    bssid: str  # MAC address as XX:XX:XX:XX:XX:XX
    ssid: str
    rssi: int  # dBm
    channel: int
    channel_width: int = 20  # MHz (20, 40, 80, 160)
    band: str = "2.4 GHz"  # "2.4 GHz", "5 GHz", "6 GHz"
    center_frequency: int = 0  # MHz (stored as MHz from ulChCenterFrequency / 1000)
    phy_type: str = "Unknown"
    security: str = "Open"
    auth_algo: str = ""
    cipher: str = ""
    vendor: str = ""
    wifi_generation: str = ""  # "Wi-Fi 4", "Wi-Fi 5", "Wi-Fi 6", "Wi-Fi 6E", "Wi-Fi 7"
    supported_modes: str = ""  # "a/n/ac/ax", "b/g/n/ax", etc.
    station_count: int = 0
    channel_utilization: float = 0.0  # 0-100%
    beacon_period: int = 0
    max_rate: float = 0.0  # Mbps
    ies_raw: bytes = field(default_factory=bytes, repr=False)
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class Network:
    """A Wi-Fi network (SSID), potentially with multiple BSSIDs."""

    ssid: str
    bss_list: list[BSSEntry] = field(default_factory=list)
    best_rssi: int = -100
    band_summary: str = ""
    security_summary: str = ""

    def refresh_summary(self):
        if self.bss_list:
            self.best_rssi = max(b.rssi for b in self.bss_list)
            bands = set(b.band for b in self.bss_list)
            self.band_summary = " + ".join(sorted(bands))
            secs = set(b.security for b in self.bss_list)
            self.security_summary = " / ".join(sorted(secs))


@dataclass
class ScanResult:
    """Complete scan result with all discovered networks."""

    timestamp: datetime = field(default_factory=datetime.now)
    networks: list[Network] = field(default_factory=list)
    interface_name: str = ""
    interface_guid: str = ""
