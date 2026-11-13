"""Data models for WiFi network scan results."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional


@dataclass
class BSSEntry:
    """A single Basic Service Set (AP radio)."""

    bssid: str  # MAC address as XX:XX:XX:XX:XX:XX
    ssid: str
    rssi: int  # dBm
    channel: int  # primary channel
    channel_width: int = 20  # MHz (20, 40, 80, 160, 320) — operating width
    center_channel: int = 0  # centre channel of the operating block (0 = same as channel)
    band: str = "2.4 GHz"  # "2.4 GHz", "5 GHz", "6 GHz"
    center_frequency: int = 0  # MHz, centre of the primary 20 MHz channel
    phy_type: str = "Unknown"
    security: str = "Open"
    auth_algo: str = ""
    cipher: str = ""
    pmf: str = ""  # Protected Management Frames: "Required" / "Capable" / "Disabled" / ""
    capability_info: int = 0  # beacon Capability Information field (0 = unknown)
    vendor: str = ""
    wifi_generation: str = ""  # "Wi-Fi 4", "Wi-Fi 5", "Wi-Fi 6", "Wi-Fi 6E", "Wi-Fi 7"
    supported_modes: str = ""  # "a/n/ac/ax", "b/g/n/ax", etc.
    station_count: int = 0
    channel_utilization: float = 0.0  # 0-100%
    beacon_period: int = 0
    max_rate: float = 0.0  # Mbps: highest PHY rate the AP advertises (0 = unknown)
    spatial_streams: int = 0  # AP's spatial streams from HT/VHT/HE/EHT caps (0 = unknown)
    ies_raw: bytes = field(default_factory=bytes, repr=False)
    timestamp: datetime = field(default_factory=datetime.now)

    def __post_init__(self):
        # One canonical form everywhere (scanners disagreed on case, so the
        # connected-BSSID marker never matched on the ctypes path).
        if isinstance(self.bssid, str):
            self.bssid = self.bssid.upper()


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


def group_networks(entries: Iterable[BSSEntry]) -> list[Network]:
    """Group BSS entries by SSID: strongest network first, hidden last."""
    by_ssid: dict[str, Network] = {}
    for b in entries:
        net = by_ssid.get(b.ssid)
        if net is None:
            net = by_ssid[b.ssid] = Network(ssid=b.ssid)
        net.bss_list.append(b)
    for n in by_ssid.values():
        n.refresh_summary()
    return sorted(by_ssid.values(), key=lambda n: (not n.ssid, -n.best_rssi))
