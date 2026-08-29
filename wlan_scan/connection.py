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
