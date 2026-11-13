"""Detect the BSSID this machine is currently connected to (per platform).

Returned as uppercase "XX:XX:XX:XX:XX:XX", or None when not connected
(Wi-Fi off, no association, or platform unsupported).
"""

from __future__ import annotations

import re
import subprocess
import sys
from typing import Optional


def connected_bssid() -> Optional[str]:
    """Current connected BSSID, or None."""
    if sys.platform == "win32":
        return _windows_connected_bssid()
    if sys.platform == "darwin":
        return _macos_connected_bssid()
    return None


def _windows_connected_bssid() -> Optional[str]:
    """Parse `netsh wlan show interfaces` for the connected BSSID."""
    try:
        result = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW
            if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
        )
    except Exception:
        return None
    for line in result.stdout.splitlines():
        m = re.search(r"BSSID\s*:\s*([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})", line)
        if m:
            return m.group(1).upper()
    return None


def _macos_connected_bssid() -> Optional[str]:
    """CoreWLAN interface bssid; falls back to `airport -I`."""
    try:
        import CoreWLAN
        client = CoreWLAN.CWWiFiClient.sharedWiFiClient()
        iface = client.interface()
        if iface is not None:
            bssid = iface.bssid()
            if bssid:
                return str(bssid).upper()
    except Exception:
        pass

    # Fallback: airport -I prints "BSSID: xx:xx:xx:xx:xx:xx"
    airport = ("/System/Library/PrivateFrameworks/Apple80211.framework/"
               "Versions/Current/Resources/airport")
    try:
        result = subprocess.run(
            [airport, "-I"], capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW
            if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
        )
    except Exception:
        return None
    for line in result.stdout.splitlines():
        m = re.search(r"BSSID:\s*([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})", line)
        if m:
            return m.group(1).upper()
    return None


# ---------------------------------------------------------------------------
# Default gateway
# ---------------------------------------------------------------------------

_IPV4 = r"(\d{1,3}(?:\.\d{1,3}){3})"


def parse_windows_route_print(text: str) -> Optional[str]:
    """`route print 0.0.0.0`: 'Network Dest  Netmask  Gateway  Interface  Metric'."""
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "0.0.0.0" and parts[1] == "0.0.0.0":
            if re.fullmatch(_IPV4, parts[2]) and parts[2] != "0.0.0.0":
                return parts[2]
    return None


def parse_macos_route_get(text: str) -> Optional[str]:
    """`route -n get default`: '    gateway: 192.168.1.1'."""
    m = re.search(r"^\s*gateway:\s*" + _IPV4 + r"\s*$", text, re.MULTILINE)
    return m.group(1) if m else None


def parse_linux_ip_route(text: str) -> Optional[str]:
    """`ip route show default`: 'default via 192.168.1.1 dev wlan0 ...'."""
    m = re.search(r"^default via " + _IPV4, text, re.MULTILINE)
    return m.group(1) if m else None


def default_gateway() -> Optional[str]:
    """IPv4 default gateway, or None. Runs a subprocess: call off the GUI thread."""
    if sys.platform == "win32":
        cmd, parse = ["route", "print", "0.0.0.0"], parse_windows_route_print
    elif sys.platform == "darwin":
        cmd, parse = ["route", "-n", "get", "default"], parse_macos_route_get
    else:
        cmd, parse = ["ip", "route", "show", "default"], parse_linux_ip_route
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=5,
            encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW
            if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
        )
    except Exception:
        return None
    return parse(result.stdout)
