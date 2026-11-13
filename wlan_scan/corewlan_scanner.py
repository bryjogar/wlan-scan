"""macOS Wi-Fi scanner via CoreWLAN (pyobjc).

Per network: SSID, BSSID, RSSI, noise, channel number / width / band,
security (probed with ``supportsSecurity:``), beacon interval, country code.

Not available from public macOS APIs (Windows gets these from raw IEs,
which CoreWLAN does not expose): station count, channel utilization, max
rate, per-network PHY. Those stay blank.

Location Services: since macOS 10.15 (enforced harder in 14+), an app
without location permission gets networks with nil SSID *and* nil BSSID.
v1 skipped those silently, so the list was just empty. ``scan()`` now
raises ``LocationPermissionRequired`` with instructions instead.

pyobjc is macOS-only: CoreWLAN is imported inside ``scan()``, and the
mapping helpers are duck-typed so tests can feed them fake CWNetworks.
"""

from __future__ import annotations

import sys
from datetime import datetime
from typing import Callable, Optional

from .channels import BAND_24, BAND_5, BAND_6, channel_to_freq, standard_center_channel
from .models import BSSEntry, ScanResult, group_networks
from .vendors import lookup_vendor

LOCATION_HELP = (
    "macOS is hiding Wi-Fi network names and BSSIDs because WLAN Scan has no "
    "Location Services permission. Open System Settings → Privacy & Security → "
    "Location Services and switch on WLAN Scan (when running from source, the "
    "terminal or Python app you launched it from), then scan again. The app "
    "bundle asks for this via NSLocationUsageDescription in its Info.plist "
    "(set in wlan-scan-mac.spec)."
)


class LocationPermissionRequired(RuntimeError):
    """CoreWLAN returned networks with SSID and BSSID redacted."""

    def __init__(self, count: int):
        super().__init__(LOCATION_HELP)
        self.count = count


# CWChannelWidth (CoreWLAN/CoreWLANTypes.h): 0 is Unknown.
_CH_WIDTH = {
    1: 20,    # kCWChannelWidth20MHz
    2: 40,    # kCWChannelWidth40MHz
    3: 80,    # kCWChannelWidth80MHz
    4: 160,   # kCWChannelWidth160MHz
}

# CWChannelBand
_CH_BAND = {1: BAND_24, 2: BAND_5, 3: BAND_6}

# CWSecurity (CoreWLANTypes.h)
CW_SECURITY = {
    "none": 0, "wep": 1, "wpa_personal": 2, "wpa_personal_mixed": 3,
    "wpa2_personal": 4, "personal": 5, "dynamic_wep": 6, "wpa_enterprise": 7,
    "wpa_enterprise_mixed": 8, "wpa2_enterprise": 9, "enterprise": 10,
    "wpa3_personal": 11, "wpa3_enterprise": 12, "wpa3_transition": 13,
    "owe": 14, "owe_transition": 15,
}

# Probe order: strongest / most specific first → (label, auth)
_SECURITY_PROBES = [
    ("wpa3_enterprise", "WPA3-Enterprise", "802.1X"),
    ("wpa3_transition", "WPA2/WPA3", "PSK / SAE"),
    ("wpa3_personal", "WPA3", "SAE"),
    ("owe", "OWE (Enhanced Open)", "OWE"),
    ("owe_transition", "Open (OWE transition)", "Open"),
    ("wpa2_enterprise", "WPA2-Enterprise", "802.1X"),
    ("wpa_enterprise_mixed", "WPA/WPA2-Enterprise", "802.1X"),
    ("wpa_enterprise", "WPA-Enterprise", "802.1X"),
    ("wpa2_personal", "WPA2", "PSK"),
    ("wpa_personal_mixed", "WPA/WPA2", "PSK"),
    ("wpa_personal", "WPA", "PSK"),
    ("dynamic_wep", "WEP", "802.1X"),
    ("wep", "WEP", "WEP"),
]


def security_from_network(net) -> tuple[str, str]:
    """(security, auth) by probing ``supportsSecurity:`` (the public API)."""
    probe = getattr(net, "supportsSecurity_", None)
    if probe is None:
        return "Unknown", ""
    for key, label, auth in _SECURITY_PROBES:
        try:
            if probe(CW_SECURITY[key]):
                return label, auth
        except Exception:
            continue
    try:
        if probe(CW_SECURITY["none"]):
            return "Open", "Open"
    except Exception:
        pass
    return "Unknown", ""


def _band_from_channel(channel: int) -> str:
    """Fallback when CWChannel has no band: 6 GHz numbers overlap 2.4/5."""
    if channel <= 14:
        return BAND_24
    return BAND_5


def _wifi_generation(width: int, band: str) -> str:
    """Coarse generation from band + width (CoreWLAN gives no PHY per network).

    6 GHz is Wi-Fi 6E or later; 160 MHz on 5 GHz is most likely Wi-Fi 6;
    anything else is reported as the minimum that fits.
    """
    if band == BAND_6:
        return "Wi-Fi 6E"
    if band == BAND_5:
        if width >= 160:
            return "Wi-Fi 6"
        if width >= 80:
            return "Wi-Fi 5"
        return "Wi-Fi 4"
    return "Wi-Fi 4"


def _call(obj, name, default=None):
    fn = getattr(obj, name, None)
    if fn is None:
        return default
    try:
        v = fn()
    except Exception:
        return default
    return default if v is None else v


def map_network(net, timestamp: datetime) -> Optional[BSSEntry]:
    """CWNetwork (or a duck-typed fake) → BSSEntry; None if BSSID is redacted."""
    bssid = str(_call(net, "bssid", "") or "").upper()
    if not bssid:
        return None
    ssid = str(_call(net, "ssid", "") or "")
    channel = _call(net, "wlanChannel")
    ch_num = int(_call(channel, "channelNumber", 0)) if channel is not None else 0
    width = _CH_WIDTH.get(int(_call(channel, "channelWidth", 0)) if channel is not None else 0, 20)
    band = _CH_BAND.get(int(_call(channel, "channelBand", 0)) if channel is not None else 0) \
        or _band_from_channel(ch_num)
    security, auth = security_from_network(net)
    entry = BSSEntry(
        bssid=bssid,
        ssid=ssid,
        rssi=int(_call(net, "rssiValue", -100)),
        channel=ch_num,
        channel_width=width,
        center_channel=standard_center_channel(ch_num, width, band),
        band=band,
        center_frequency=channel_to_freq(ch_num, band),
        security=security,
        auth_algo=auth,
        vendor=lookup_vendor(bssid),
        wifi_generation=_wifi_generation(width, band),
        beacon_period=int(_call(net, "beaconInterval", 0)),
        timestamp=timestamp,
    )
    if _call(net, "ibss", False):
        entry.phy_type = "IBSS"
    return entry


def map_networks(networks, timestamp: datetime,
                 debug: Optional[Callable[[str], None]] = None) -> list[BSSEntry]:
    """Map a scan's CWNetworks. Raises LocationPermissionRequired when every
    network came back with its BSSID redacted."""
    entries, redacted = [], 0
    for net in networks:
        try:
            e = map_network(net, timestamp)
        except Exception as ex:
            if debug:
                debug(f"CoreWLAN entry skipped: {ex}")
            continue
        if e is None:
            redacted += 1
        else:
            entries.append(e)
    if redacted and not entries:
        raise LocationPermissionRequired(redacted)
    if redacted and debug:
        debug(f"CoreWLAN: {redacted} network(s) without BSSID skipped")
    return entries


def corewlan_available() -> bool:
    """True when pyobjc CoreWLAN can be imported (macOS only)."""
    if sys.platform != "darwin":
        return False
    try:
        import CoreWLAN  # noqa: F401
        return True
    except Exception:
        return False


def scan(interface: str = "", debug: Optional[Callable[[str], None]] = None) -> ScanResult:
    """Scan via CoreWLAN. Raises RuntimeError when unavailable/failing."""
    import CoreWLAN

    client = CoreWLAN.CWWiFiClient.sharedWiFiClient()
    iface = None
    if interface:
        iface = client.interfaceWithName_(interface)
    if iface is None:
        iface = client.interface()  # default interface
    if iface is None:
        raise RuntimeError("No Wi-Fi interface available (Wi-Fi may be off).")

    # Returns (NSSet<CWNetwork>, NSError). v1 treated the set as a dict.
    found, error = iface.scanForNetworksWithName_includeHidden_error_(None, True, None)
    if error:
        raise RuntimeError(f"CoreWLAN scan failed: {error}")

    ts = datetime.now()
    entries = map_networks(list(found or []), ts, debug)
    return ScanResult(timestamp=ts, networks=group_networks(entries),
                      interface_name=str(iface.interfaceName() or interface))


def connected_bssid() -> Optional[str]:
    try:
        import CoreWLAN

        iface = CoreWLAN.CWWiFiClient.sharedWiFiClient().interface()
        b = iface.bssid() if iface is not None else None
        return str(b).upper() if b else None
    except Exception:
        return None


if __name__ == "__main__":
    r = scan()
    print(f"{len(r.networks)} networks, {sum(len(n.bss_list) for n in r.networks)} BSSIDs")
    for n in r.networks:
        b = n.bss_list[0]
        print(f"{b.ssid:28s} {b.bssid} {b.rssi:4d} {b.channel:3d} "
              f"{b.channel_width:3d}MHz {b.band:8s} {b.security:6s} "
              f"beacon={b.beacon_period}")
