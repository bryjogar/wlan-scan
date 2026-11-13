"""Build a BSSEntry from raw wlanapi fields.

The ctypes scanner (x64) and the PowerShell bridge (ARM64) both read
``WLAN_BSS_ENTRY`` records; both hand the raw fields to
``build_bss_entry`` so they produce identical results from identical data.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from .channels import freq_to_channel
from .ie_parser import parse_information_elements
from .models import BSSEntry

# DOT11_PHY_TYPE (windot11.h)
PHY_TYPE_NAMES = {
    0: "Unknown",
    1: "FHSS",
    2: "DSSS",
    3: "IR Baseband",
    4: "802.11a (OFDM)",
    5: "802.11b (HR-DSSS)",
    6: "802.11g (ERP)",
    7: "802.11n (HT)",
    8: "802.11ac (VHT)",
    9: "802.11ad (DMG)",
    10: "802.11ax (HE)",
    11: "802.11be (EHT)",
}


def decode_ssid(raw: bytes) -> str:
    """SSID bytes → str. Hidden networks (empty or all-NUL) → ""."""
    raw = bytes(raw)
    if not raw or not raw.strip(b"\x00"):
        return ""
    return raw.decode("utf-8", errors="replace")


def format_bssid(raw) -> str:
    if isinstance(raw, str):
        return raw.upper()
    return ":".join(f"{b & 0xFF:02X}" for b in raw)


def build_bss_entry(
    *,
    ssid: bytes,
    bssid,
    rssi: int,
    freq_khz: int,
    phy_id: int,
    beacon_period: int = 0,
    capability: int = 0,
    ies: bytes = b"",
    legacy_max_rate: float = 0.0,  # from WLAN_RATE_SET; used only without IEs
    vendor_lookup=None,
    timestamp: Optional[datetime] = None,
) -> BSSEntry:
    freq_mhz = int(round(freq_khz / 1000))
    channel, band = freq_to_channel(freq_mhz)
    info = parse_information_elements(ies, band if channel else None, capability) if ies else None

    phy = PHY_TYPE_NAMES.get(phy_id, f"Unknown ({phy_id})")
    bssid_s = format_bssid(bssid)
    entry = BSSEntry(
        bssid=bssid_s,
        ssid=decode_ssid(ssid),
        rssi=int(rssi),
        channel=channel,
        band=band,
        center_frequency=freq_mhz,
        phy_type=phy,
        beacon_period=int(beacon_period),
        capability_info=int(capability),
        max_rate=float(legacy_max_rate or 0.0),
        ies_raw=bytes(ies),
        timestamp=timestamp or datetime.now(),
    )
    if info is not None:
        entry.security = info["security"]
        entry.auth_algo = info["auth_algo"]
        entry.cipher = info["cipher"]
        entry.pmf = info["pmf"]
        entry.channel_width = info["channel_width"]
        entry.center_channel = info["center_channel"]
        entry.station_count = info["station_count"]
        entry.channel_utilization = info["channel_utilization"]
        entry.wifi_generation = info["wifi_generation"]
        entry.supported_modes = info["supported_modes"]
        entry.max_rate = info["max_rate_mbps"] or float(legacy_max_rate or 0.0)
        entry.spatial_streams = info["spatial_streams"]
        if phy_id in (0, 1, 2, 3) or phy.startswith("Unknown"):
            entry.phy_type = info["phy_type"]
        if not channel and info["primary_channel"]:
            entry.channel = info["primary_channel"]
            entry.band = info["band"] or band
    else:
        entry.security = "WEP" if capability & 0x0010 else "Open"
    if vendor_lookup is not None:
        entry.vendor = vendor_lookup(bssid_s)
    return entry
