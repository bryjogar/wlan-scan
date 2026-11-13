"""Wi-Fi scanner using `netsh wlan show networks mode=bssid` (last-resort fallback).

Used when neither wlanapi (ctypes) nor the PowerShell bridge works. netsh
gives no raw IEs, so several values are ESTIMATES and flagged as such in
``BSSEntry.estimated``:

* RSSI: netsh prints signal *quality* in percent. Windows defines quality
  linearly from -100 dBm (0 %) to -50 dBm (100 %), so dBm = pct / 2 - 100.
* Channel width: guessed from radio type and band.

Parsing expects English output (labels are localised on other Windows
languages; unknown lines are ignored).

v1 crashed on the first BSSID (``_channel_to_freq`` was undefined) and
emitted each BSS at its Channel line, before that BSS's rates, BSS-load
and (on Windows 11) Band lines.
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime
from typing import Optional

from .channels import BAND_24, BAND_5, BAND_6, band_from_channel, channel_to_freq, \
    standard_center_channel
from .models import BSSEntry, ScanResult, group_networks
from .vendors import lookup_vendor

ESTIMATED_FIELDS = "rssi,channel_width"


def _run_netsh() -> str:
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


_RE_KV = re.compile(r"^\s*([^:]+?)\s*:\s*(.*?)\s*$")
_RE_SSID = re.compile(r"^SSID\s+\d+$", re.IGNORECASE)
_RE_BSSID = re.compile(r"^BSSID\s+\d+$", re.IGNORECASE)
_RE_MAC = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$", re.IGNORECASE)


def signal_to_rssi(percent: int) -> int:
    """Windows signal quality (0-100 %) → dBm: linear -100 dBm … -50 dBm."""
    percent = max(0, min(100, percent))
    return int(round(percent / 2 - 100))


def _radio_to_phy(radio: str) -> str:
    r = radio.strip().lower()
    for key, phy in (("802.11be", "802.11be (EHT)"), ("802.11ax", "802.11ax (HE)"),
                     ("802.11ac", "802.11ac (VHT)"), ("802.11n", "802.11n (HT)"),
                     ("802.11g", "802.11g (ERP)"), ("802.11b", "802.11b (HR-DSSS)"),
                     ("802.11a", "802.11a (OFDM)")):
        if key in r:
            return phy
    return radio.strip() or "Unknown"


def _generation(phy: str, band: str) -> str:
    if "be" in phy:
        return "Wi-Fi 7"
    if "ax" in phy:
        return "Wi-Fi 6E" if band == BAND_6 else "Wi-Fi 6"
    if "ac" in phy:
        return "Wi-Fi 5"
    if "11n" in phy:
        return "Wi-Fi 4"
    return "Legacy"


def estimate_channel_width(phy: str, band: str) -> int:
    """Typical deployment width for a radio type on a band (estimate)."""
    p = phy.lower()
    if "be" in p:
        return 320 if band == BAND_6 else (160 if band == BAND_5 else 20)
    if "ax" in p:
        return {BAND_6: 160, BAND_5: 80}.get(band, 20)
    if "ac" in p:
        return 80
    if "11n" in p:
        return 40 if band == BAND_5 else 20
    return 20


def _band_label(text: str, channel: int) -> str:
    t = text.replace(" ", "").lower()
    if t.startswith("2.4"):
        return BAND_24
    if t.startswith("5"):
        return BAND_5
    if t.startswith("6"):
        return BAND_6
    return band_from_channel(channel)


def normalize_security(auth: str, encryption: str) -> tuple[str, str, str]:
    """netsh Authentication/Encryption → (security, auth_algo, cipher)."""
    a = auth.strip().lower()
    e = encryption.strip()
    cipher = "" if e.lower() == "none" else e.upper()
    if "owe" in a:
        return "OWE (Enhanced Open)", "OWE", cipher
    if "wpa3" in a and "enterprise" in a:
        return "WPA3-Enterprise", auth.strip(), cipher
    if "wpa3" in a or "sae" in a:
        return "WPA3", "SAE", cipher
    if "wpa2" in a and "enterprise" in a:
        return "WPA2-Enterprise", "802.1X", cipher
    if "wpa2" in a:
        return "WPA2", "PSK", cipher
    if "wpa" in a and "enterprise" in a:
        return "WPA-Enterprise", "802.1X", cipher
    if "wpa" in a:
        return "WPA", "PSK", cipher
    if "wep" in a or e.lower() == "wep":
        return "WEP", "WEP", "WEP"
    if "open" in a:
        return ("WEP", "Open", "WEP") if e.lower() == "wep" else ("Open", "Open", "")
    return auth.strip() or "Unknown", auth.strip(), cipher


def _finish(bss: dict, net: dict, ts: datetime) -> Optional[BSSEntry]:
    if not bss.get("bssid"):
        return None
    channel = int(bss.get("channel") or 0)
    band = _band_label(bss.get("band", ""), channel)
    phy = _radio_to_phy(bss.get("radio", ""))
    width = estimate_channel_width(phy, band)
    security, auth, cipher = normalize_security(net.get("auth", ""), net.get("enc", ""))
    util = bss.get("util")
    return BSSEntry(
        bssid=bss["bssid"],
        ssid=net.get("ssid", ""),
        rssi=signal_to_rssi(int(bss.get("signal") or 0)),
        channel=channel,
        channel_width=width,
        center_channel=standard_center_channel(channel, width, band),
        band=band,
        center_frequency=channel_to_freq(channel, band),
        phy_type=phy,
        security=security,
        auth_algo=auth,
        cipher=cipher,
        vendor=lookup_vendor(bss["bssid"]),
        wifi_generation=_generation(phy, band),
        station_count=int(bss.get("stations") or 0),
        channel_utilization=float(util) if util is not None else 0.0,
        max_rate=0.0,  # netsh lists only legacy rates (<= 54 Mbps): not a PHY max
        estimated=ESTIMATED_FIELDS,
        timestamp=ts,
    )


def parse_netsh_output(output: str, ts: Optional[datetime] = None) -> ScanResult:
    """Parse `netsh wlan show networks mode=bssid` (English) into a ScanResult."""
    ts = ts or datetime.now()
    result = ScanResult(timestamp=ts, interface_guid="netsh")
    entries: list[BSSEntry] = []
    net: dict = {}
    bss: dict = {}

    def flush_bss():
        nonlocal bss
        e = _finish(bss, net, ts)
        if e is not None:
            entries.append(e)
        bss = {}

    for raw in output.splitlines():
        m = _RE_KV.match(raw)
        if not m:
            continue
        key, value = m.group(1).strip(), m.group(2).strip()
        k = key.lower()
        if k == "interface name":
            result.interface_name = value
        elif _RE_SSID.match(key):
            flush_bss()
            net = {"ssid": value}
        elif _RE_BSSID.match(key) and _RE_MAC.match(value):
            flush_bss()
            bss = {"bssid": value}
        elif k == "authentication":
            net["auth"] = value
        elif k == "encryption":
            net["enc"] = value
        elif not bss:
            continue
        elif k == "signal":
            bss["signal"] = re.sub(r"\D", "", value) or "0"
        elif k == "radio type":
            bss["radio"] = value
        elif k == "band":
            bss["band"] = value
        elif k == "channel":
            bss["channel"] = re.sub(r"\D", "", value) or "0"
        elif k == "connected stations":
            bss["stations"] = re.sub(r"\D", "", value) or "0"
        elif k == "channel utilization":
            pm = re.search(r"\((\d+)\s*%\)", value)
            if pm:
                bss["util"] = pm.group(1)
            elif value.isdigit():  # raw 0-255 only
                bss["util"] = round(int(value) / 255 * 100, 1)
    flush_bss()
    result.networks = group_networks(entries)
    return result


def scan() -> ScanResult:
    """Run netsh and parse its output."""
    if sys.platform != "win32":
        raise OSError("netsh wlan is only available on Windows.")
    return parse_netsh_output(_run_netsh())
