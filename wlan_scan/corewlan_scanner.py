"""macOS Wi-Fi scanner via CoreWLAN (pyobjc).

Proper macOS API — no private-CLI text parsing. Gives per-network:
    SSID, BSSID, RSSI, channel number + width, security type (enum),
    beacon interval, IBSS flag, country code.

Not available via public macOS APIs (Windows gets these from raw IE
parsing, which CoreWLAN does not expose):
    station_count, channel_utilization, max_rate, raw IEs, per-network PHY.

pyobjc is macOS-only: this module imports lazily inside scan(), so the
package still imports on Windows/Linux.
"""

from __future__ import annotations

import sys
from datetime import datetime
from typing import Callable, Optional

from .models import BSSEntry, Network, ScanResult
from .oui_lookup import lookup_vendor

# CWChannelWidth values (CoreWLAN)
_CH_WIDTH = {
    0: 20,    # kCWChannelWidth20MHz
    1: 40,    # kCWChannelWidth40MHz
    2: 80,    # kCWChannelWidth80MHz
    3: 160,   # kCWChannelWidth160MHz
    4: 160,   # kCWChannelWidth80p80MHz
}

# CWSecurityType → (security, auth_algo) mapping (subset that matters).
_SECURITY_MAP = {
    0: ("Open", "Open"),                  # kCWSecurityNone
    1: ("WEP", "WEP"),                    # kCWSecurityWEP
    2: ("WPA", "WPA"),                    # kCWSecurityWPAPersonal
    3: ("WPA", "WPA"),                    # kCWSecurityWPAPersonalMixed
    4: ("WPA2", "WPA2"),                  # kCWSecurityWPA2Personal
    5: ("WPA2", "WPA2"),                  # kCWSecurityPersonal
    6: ("WEP", "WEP"),                    # kCWSecurityDynamicWEP
    7: ("WPA2", "WPA2"),                  # kCWSecurityWPA2PersonalMixed
    8: ("WPA", "WPA"),                    # kCWSecurityWPAGroup
    9: ("WPA2", "WPA2"),                  # kCWSecurityWPAGroupMixed
    10: ("WPA2", "WPA2"),                 # kCWSecurityWPA2Group
    11: ("WPA2", "WPA2"),                 # kCWSecurityWPA2GroupMixed
    12: ("WPA3", "WPA3"),                 # kCWSecurityWPA3Personal
    13: ("WPA3", "WPA3"),                 # kCWSecurityWPA3Enterprise
    14: ("WPA3", "WPA3"),                 # kCWSecurityWPA3Transition
    15: ("WPA3", "WPA3"),                 # kCWSecurityWPA3TransitionGroup
    16: ("Enterprise", "802.1X"),         # kCWSecurityEnterprise
    17: ("Enterprise", "802.1X"),         # kCWSecurityEnterpriseMixed
    18: ("WPA3", "WPA3"),                 # kCWSecurityWPA3EnterpriseMixed
    19: ("Enterprise", "802.1X"),         # kCWSecurityWPA2EnterpriseMixed
}


def _security_from_cw(raw: int) -> tuple[str, str]:
    """Map a CWSecurityType int to (security, auth)."""
    return _SECURITY_MAP.get(raw, ("Unknown", f"cw:{raw}"))


def _band_from_channel(channel: int) -> str:
    if channel <= 14:
        return "2.4 GHz"
    if channel <= 35:
        return "6 GHz"
    if channel <= 177:
        return "5 GHz"
    return "6 GHz"


def _wifi_generation(width: int, band: str) -> str:
    """Best-effort generation from channel width + band (no PHY on macOS).

    Coarse but useful: 6 GHz is Wi-Fi 6E territory; on 5 GHz, 80+ MHz is
    802.11ac (Wi-Fi 5), 40 MHz could be n or ac — call it Wi-Fi 5; on
    2.4 GHz, anything ≤40 MHz is 802.11n (Wi-Fi 4).
    """
    if band == "6 GHz":
        return "Wi-Fi 6E"
    if band == "5 GHz":
        if width >= 160:
            return "Wi-Fi 6"  # ax wave2; ac wave2 also does 160 — uncertain
        if width >= 40:
            return "Wi-Fi 5"
        return "Wi-Fi 4"
    # 2.4 GHz
    return "Wi-Fi 4"


def _max_rate_hint(width: int, band: str) -> float:
    """Coarse max-rate estimate from width+band (no MCS info on macOS)."""
    if band == "2.4 GHz":
        return 300.0 if width >= 40 else 150.0
    if width >= 160:
        return 2400.0
    if width >= 80:
        return 1200.0
    if width >= 40:
        return 600.0
    return 300.0


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

    # returns dict NSDictionary {CWNetwork: NSNumber rssi} (retained)
    found, error = iface.scanForNetworksWithName_includeHidden_error_(None, True, None)
    if error:
        raise RuntimeError(f"CoreWLAN scan failed: {error}")

    result = ScanResult(timestamp=datetime.now(),
                        interface_name=iface.interfaceName() or interface)
    seen: dict[str, Network] = {}

    networks = found if found else {}
    for net, rssi_num in networks.items():
        try:
            bssid = (net.bssid() or "").upper()
            ssid = net.ssid() or ""
            if not bssid:
                continue
            channel = net.channel()
            ch_num = int(channel.channelNumber()) if channel else 0
            ch_w = int(channel.channelWidth()) if channel else 0
            width = _CH_WIDTH.get(ch_w, 20)
            sec_raw = int(net.securityType()) if hasattr(net, "securityType") else 0
            security, auth = _security_from_cw(sec_raw)
            beacon = int(net.beaconInterval()) if hasattr(net, "beaconInterval") else 0
            country = net.countryCode() or "" if hasattr(net, "countryCode") else ""
            ibss = bool(net.ibss()) if hasattr(net, "ibss") else False
            rssi = int(rssi_num) if rssi_num is not None else -100

            band = _band_from_channel(ch_num)
            entry = BSSEntry(
                bssid=bssid,
                ssid=ssid,
                rssi=rssi,
                channel=ch_num,
                channel_width=width,
                band=band,
                center_frequency=0,
                security=security,
                auth_algo=auth,
                vendor=lookup_vendor(bssid),
                wifi_generation=_wifi_generation(width, band),
                max_rate=_max_rate_hint(width, band),
                beacon_period=beacon,
                timestamp=result.timestamp,
            )
            if ibss:
                entry.phy_type = "IBSS"

            net_obj = seen.get(ssid)
            if net_obj is None:
                net_obj = Network(ssid=ssid)
                seen[ssid] = net_obj
                result.networks.append(net_obj)
            net_obj.bss_list.append(entry)
            net_obj.refresh_summary()
        except Exception as e:
            if debug:
                debug(f"CoreWLAN entry skipped: {e}")
            continue

    return result


if __name__ == "__main__":
    r = scan()
    print(f"{len(r.networks)} networks, {sum(len(n.bss_list) for n in r.networks)} BSSIDs")
    for n in r.networks:
        b = n.bss_list[0]
        print(f"{b.ssid:28s} {b.bssid} {b.rssi:4d} {b.channel:3d} "
              f"{b.channel_width:3d}MHz {b.band:8s} {b.security:6s} "
              f"beacon={b.beacon_period}")
