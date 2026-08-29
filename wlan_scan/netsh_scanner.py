"""Wi-Fi scanner using `netsh wlan show networks mode=bssid`.

Reliable fallback when wlanapi ctypes fails (ARM64, Qualcomm drivers, etc.).
Parses the text output from netsh into the same BSSEntry/Network/ScanResult
types used by the ctypes scanner.
"""

import re
import subprocess
import sys
from datetime import datetime
from typing import Optional

from .models import BSSEntry, Network, ScanResult
from .vendor_lookup import lookup_vendor


def _run_netsh() -> str:
    """Run netsh and return stdout as string."""
    try:
        result = subprocess.run(
            ["netsh", "wlan", "show", "networks", "mode=bssid"],
            capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW
            if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
        )
        return result.stdout
    except Exception as e:
        raise RuntimeError(f"netsh wlan failed: {e}")


# Matches the interface name line
_RE_INTERFACE = re.compile(r"^\s*Interface name\s*:\s*(.+)$", re.IGNORECASE)

# Matches SSID line (start of a network block)
# Uses .* not .+ to match empty/hidden SSIDs
_RE_SSID = re.compile(r"^\s*SSID\s+\d+\s*:\s*(.*)$", re.IGNORECASE)

# Matches BSSID line
_RE_BSSID = re.compile(r"^\s*BSSID\s+\d+\s*:\s*([0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2}:[0-9a-f]{2})$", re.IGNORECASE)

# Field matchers (name: value pairs)
_RE_NETWORK_TYPE = re.compile(r"^\s*Network type\s*:\s*(.+)$", re.IGNORECASE)
_RE_AUTH = re.compile(r"^\s*Authentication\s*:\s*(.+)$", re.IGNORECASE)
_RE_ENCRYPTION = re.compile(r"^\s*Encryption\s*:\s*(.+)$", re.IGNORECASE)
_RE_SIGNAL = re.compile(r"^\s*Signal\s*:\s*(\d+)%?$", re.IGNORECASE)
_RE_RADIO = re.compile(r"^\s*Radio type\s*:\s*(.+)$", re.IGNORECASE)
_RE_CHANNEL = re.compile(r"^\s*Channel\s*:\s*(\d+)$", re.IGNORECASE)
_RE_BASIC_RATES = re.compile(r"^\s*Basic rates\s*\(Mbps\)\s*:\s*(.+)$", re.IGNORECASE)
_RE_OTHER_RATES = re.compile(r"^\s*Other rates\s*\(Mbps\)\s*:\s*(.+)$", re.IGNORECASE)
_RE_BAND = re.compile(r"^\s*Band\s*:\s*(.+)$", re.IGNORECASE)
_RE_BEACON = re.compile(r"^\s*Beacon interval\s*:\s*(\d+)$", re.IGNORECASE)
_RE_PHY = re.compile(r"^\s*Phy type\s*:\s*(.+)$", re.IGNORECASE)
_RE_VENDOR = re.compile(r"^\s*Vendor\s*:\s*(.+)$", re.IGNORECASE)


def _signal_to_rssi(percent: int) -> int:
    """Convert signal percentage (0-100) to approximate RSSI in dBm."""
    # Rough mapping: 100% ≈ -30 dBm, 0% ≈ -90 dBm
    if percent <= 0:
        return -90
    if percent >= 100:
        return -30
    return -90 + int(percent * 0.6)


def _radio_to_phy(radio: str) -> str:
    """Map netsh radio type to PHY type name."""
    radio = radio.strip().lower()
    if "802.11be" in radio or "wi-fi 7" in radio:
        return "802.11be (EHT)"
    if "802.11ax" in radio or "wi-fi 6" in radio:
        return "802.11ax (HE)"
    if "802.11ac" in radio:
        return "802.11ac (VHT)"
    if "802.11n" in radio:
        return "802.11n (HT)"
    if "802.11g" in radio:
        return "802.11g"
    if "802.11b" in radio:
        return "HRDSSS (802.11b)"
    if "802.11a" in radio:
        return "802.11a"
    return radio


def _channel_to_band(channel: int) -> str:
    """Map channel number to band name."""
    if 1 <= channel <= 14:
        return "2.4 GHz"
    if 36 <= channel <= 177:
        return "5 GHz"
    if channel >= 1:  # 6 GHz channels are typically 1-233 in the new scheme
        # More specific check
        if 36 <= channel <= 177:
            return "5 GHz"
        if channel >= 183:
            return "6 GHz"
    return "Unknown"


def _estimate_channel_width(phy_type: str, band: str) -> int:
    """Estimate channel width from PHY type and band.

    Netsh doesn't report channel width; real widths come from 802.11
    HT/VHT/HE/EHT Operation IEs only accessible via wlanapi ctypes.
    These are reasonable defaults for the most common deployments.

    Returns estimated width in MHz, or 0 if unknown.
    """
    p = phy_type.lower()

    # WiFi 7 (802.11be / EHT)
    if "be" in p or "eht" in p:
        if "6 ghz" in band or "6GHz" in band:
            return 320
        return 160

    # WiFi 6/6E (802.11ax / HE)
    if "ax" in p or "he" in p:
        if "6 ghz" in band or "6GHz" in band:
            return 160
        if "5 ghz" in band or "5GHz" in band:
            return 80
        return 20  # 2.4 GHz ax is usually 20 MHz

    # WiFi 5 (802.11ac / VHT)
    if "ac" in p or "vht" in p:
        return 80

    # WiFi 4 (802.11n / HT) — 40 MHz on 5 GHz, 20 MHz on 2.4
    if "n" in p or "ht" in p:
        if "5 ghz" in band or "5GHz" in band:
            return 40
        return 20

    # Legacy (802.11a/b/g) — always 20 MHz
    return 20
    """Approximate center frequency in kHz from channel + band."""
    if band == "2.4 GHz":
        return int((2407 + channel * 5) * 1000)
    elif band == "5 GHz":
        return int((5000 + channel * 5) * 1000)
    elif band == "6 GHz":
        return int((5950 + (channel - 1) * 5) * 1000)
    return 0


def _parse_max_rate(rates_str: str) -> float:
    """Parse a space-separated rate list and return max in Mbps."""
    try:
        parts = rates_str.strip().split()
        nums = [float(p.replace(",", ".")) for p in parts if p]
        return max(nums) if nums else 0.0
    except (ValueError, IndexError):
        return 0.0


def _normalize_security(auth: str, encryption: str) -> tuple[str, str, str]:
    """Normalize auth/encryption strings into security, auth_algo, cipher."""
    a = auth.strip().lower()
    e = encryption.strip().lower()

    if "wpa3" in a or "sae" in a:
        return "WPA3-SAE", "WPA3-SAE", e.upper()
    if "wpa2" in a and "enterprise" in a:
        return "WPA2-Enterprise", "WPA2-Enterprise", e.upper()
    if "wpa2" in a:
        return "WPA2 PSK (WPA2)", "WPA2-PSK", e.upper()
    if "wpa" in a and "enterprise" in a:
        return "WPA-Enterprise", "WPA-Enterprise", e.upper()
    if "wpa" in a:
        return "WPA PSK (WPA)", "WPA-PSK", e.upper()
    if "wep" in a:
        return "WEP", "WEP", e.upper()
    if "open" in a or "none" in a:
        return "Open", "Open", "None"

    return a, a, e


def scan() -> ScanResult:
    """Run netsh wlan show networks mode=bssid and parse output."""
    if sys.platform != "win32":
        raise OSError("netsh wlan is only available on Windows.")

    output = _run_netsh()

    scan = ScanResult(
        timestamp=datetime.now(),
        interface_name="",  # populated from output
        interface_guid="netsh",  # netsh doesn't expose GUID
    )

    lines = output.splitlines()

    # Track state
    current_ssid: Optional[str] = None
    current_auth = ""
    current_encryption = ""
    current_network_type = ""
    current_band = ""
    current_phy = ""
    current_beacon = 0
    current_basic_rates = ""
    current_other_rates = ""
    current_vendor = ""

    networks_by_ssid: dict[str, Network] = {}
    bss_count = 0

    for raw_line in lines:
        line = raw_line.rstrip()
        if not line.strip():
            continue

        # Interface name
        m = _RE_INTERFACE.match(line)
        if m:
            scan.interface_name = m.group(1).strip()
            continue

        # New SSID block
        m = _RE_SSID.match(line)
        if m:
            current_ssid = m.group(1).strip()
            # Reset per-SSID state
            current_auth = ""
            current_encryption = ""
            current_network_type = ""
            current_band = ""
            current_phy = ""
            current_beacon = 0
            current_basic_rates = ""
            current_other_rates = ""
            current_vendor = ""
            continue

        # BSSID block within current SSID
        m = _RE_BSSID.match(line)
        if m:
            bssid_raw = m.group(1)
            bssid = bssid_raw.lower()
            # The next several lines will be this BSSID's attributes
            # We'll collect them and emit a BSSEntry when we hit
            # the next BSSID or SSID line
            continue

        # Collect attributes (apply to current SSID or current BSSID)
        m = _RE_NETWORK_TYPE.match(line)
        if m:
            current_network_type = m.group(1).strip()
            continue

        m = _RE_AUTH.match(line)
        if m:
            current_auth = m.group(1).strip()
            continue

        m = _RE_ENCRYPTION.match(line)
        if m:
            current_encryption = m.group(1).strip()
            continue

        m = _RE_SIGNAL.match(line)
        if m:
            signal_pct = int(m.group(1))
            rssi = _signal_to_rssi(signal_pct)
            continue

        m = _RE_RADIO.match(line)
        if m:
            current_phy = _radio_to_phy(m.group(1).strip())
            continue

        m = _RE_CHANNEL.match(line)
        if m:
            channel = int(m.group(1))
            band = _channel_to_band(channel)
            current_band = band
            freq = _channel_to_freq(channel, band)

            # We have enough to create a BSSEntry at this point
            # (channel comes last in a BSSID block)
            security, auth_algo, cipher = _normalize_security(
                current_auth, current_encryption)

            max_rate = _parse_max_rate(
                f"{current_basic_rates} {current_other_rates}")

            vendor = lookup_vendor(bssid)

            entry = BSSEntry(
                bssid=bssid,
                ssid=current_ssid or "",
                rssi=rssi,
                channel=channel,
                channel_width=_estimate_channel_width(
                    current_phy, band),  # estimated
                band=band,
                center_frequency=freq,
                phy_type=current_phy or "Unknown",
                security=security,
                auth_algo=auth_algo,
                cipher=cipher,
                vendor=vendor,
                station_count=0,
                channel_utilization=0.0,
                beacon_period=current_beacon,
                max_rate=max_rate,
                ies_raw=b"",
                timestamp=datetime.now(),
            )

            ssid_key = current_ssid or "<Hidden>"
            if ssid_key not in networks_by_ssid:
                networks_by_ssid[ssid_key] = Network(ssid=current_ssid)
            networks_by_ssid[ssid_key].bss_list.append(entry)
            bss_count += 1
            continue

        m = _RE_BASIC_RATES.match(line)
        if m:
            current_basic_rates = m.group(1).strip()
            continue

        m = _RE_OTHER_RATES.match(line)
        if m:
            current_other_rates = m.group(1).strip()
            continue

        m = _RE_BAND.match(line)
        if m:
            current_band = m.group(1).strip()
            continue

        m = _RE_BEACON.match(line)
        if m:
            try:
                current_beacon = int(m.group(1))
            except ValueError:
                pass
            continue

    # Refresh summaries
    for net in networks_by_ssid.values():
        net.refresh_summary()

    def sort_key(n: Network) -> tuple:
        return (n.ssid == "<Hidden>", -n.best_rssi)

    scan.networks = sorted(networks_by_ssid.values(), key=sort_key)
    return scan


def scan_networks() -> list[Network]:
    """Return just the network list (convenience)."""
    return scan().networks
