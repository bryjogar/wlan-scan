"""Wi-Fi scanner for macOS via the built-in `airport` CLI.

macOS has no netsh/wlanapi; the standard passive scan tool is the private
`airport` binary:

    /System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport -s

Output (tab-separated columns after a header line):

    SSID BSSID             RSSI CHANNEL HT CC SECURITY (auth/unicast/group)
    MyNet aa:bb:cc:dd:ee:ff -45  44,+1  Y  US WPA2(PSK/AES/AES)

Returns the same ScanResult model as the Windows scanners for drop-in
replacement. Parsing is defensive: any malformed line is skipped.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import datetime
from typing import Optional

from .models import BSSEntry, Network, ScanResult
from .oui_lookup import lookup_vendor

_AIRPORT = ("/System/Library/PrivateFrameworks/Apple80211.framework/"
            "Versions/Current/Resources/airport")

# SSID can contain spaces — split from the right after the SSID.
_LINE_RE = re.compile(
    r"^(?P<ssid>.+?)\s+"
    r"(?P<bssid>[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:"
    r"[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2})\s+"
    r"(?P<rssi>-?\d+)\s+"
    r"(?P<channel>\d+)(?P<width>,(\+|-)?\d+)?\s+"
    r"(?P<ht>Y|N)\s+"
    r"(?P<cc>[A-Z]{2})\s+"
    r"(?P<security>.*)$"
)


def _find_airport() -> Optional[str]:
    """Locate the airport binary; check both the private framework and PATH."""
    if shutil.which("airport"):
        return shutil.which("airport")
    if os.path.exists(_AIRPORT):
        return _AIRPORT
    return None


def _run_airport() -> str:
    exe = _find_airport()
    if not exe:
        raise RuntimeError(
            "airport CLI not found — this build must run on macOS."
        )
    try:
        result = subprocess.run(
            [exe, "-s"],
            capture_output=True, text=True, timeout=20,
            encoding="utf-8", errors="replace",
        )
    except Exception as e:
        raise RuntimeError(f"airport -s failed: {e}")
    if result.returncode != 0:
        raise RuntimeError(f"airport -s exited {result.returncode}: {result.stderr}")
    return result.stdout


def _channel_width(width: Optional[str]) -> int:
    """',40' → 40, ',+1'/'-1' → 40 (bonded), None → 20.

    airport reports either an explicit MHz (40/80/160) or a secondary-channel
    offset (+1/-1) which means a bonded 40 MHz channel.
    """
    if not width:
        return 20
    try:
        w = int(width.lstrip(",+-"))
        if w <= 1:
            return 40  # secondary-channel offset → 40 MHz bonded
        return w if w >= 40 else 20
    except ValueError:
        return 20


def _band(channel: int, width: int) -> str:
    if channel <= 14:
        return "2.4 GHz"
    if channel <= 35:
        return "6 GHz"  # 6 GHz uses 1-233; 5 GHz starts at 36
    if channel <= 177:
        return "5 GHz"
    return "6 GHz"


def _security(field: str) -> tuple[str, str]:
    """airport security field → (security, auth_algo)."""
    field = (field or "").strip()
    if not field or field == "NONE":
        return "Open", "Open"
    if field.upper().startswith("WPA3"):
        return "WPA3", field
    if field.upper().startswith("WPA2"):
        return "WPA2", field
    if field.upper().startswith("WPA"):
        return "WPA", field
    if field.upper().startswith("WEP"):
        return "WEP", field
    return "Unknown", field


def scan(interface: str = "") -> ScanResult:
    """Scan for Wi-Fi networks on macOS. Returns ScanResult."""
    output = _run_airport()
    return parse_airport_output(output, interface=interface)


def parse_airport_output(output: str, interface: str = "") -> ScanResult:
    result = ScanResult(timestamp=datetime.now(), interface_name=interface)
    seen_ssids: dict[str, Network] = {}

    for line in output.splitlines():
        if not line.strip():
            continue
        if line.lstrip().startswith("SSID"):
            continue  # header row
        m = _LINE_RE.match(line.strip())
        if not m:
            continue

        bssid = m.group("bssid").upper()
        ssid = m.group("ssid").strip()
        rssi = int(m.group("rssi"))
        channel = int(m.group("channel"))
        width = _channel_width(m.group("width"))
        security, auth = _security(m.group("security"))

        entry = BSSEntry(
            bssid=bssid,
            ssid=ssid,
            rssi=rssi,
            channel=channel,
            channel_width=width,
            band=_band(channel, width),
            security=security,
            auth_algo=auth,
            vendor=lookup_vendor(bssid),
            timestamp=result.timestamp,
        )

        net = seen_ssids.get(ssid)
        if net is None:
            net = Network(ssid=ssid)
            seen_ssids[ssid] = net
            result.networks.append(net)
        net.bss_list.append(entry)
        net.refresh_summary()

    return result


if __name__ == "__main__":
    r = scan()
    print(f"{len(r.networks)} networks, {sum(len(n.bss_list) for n in r.networks)} BSSIDs")
