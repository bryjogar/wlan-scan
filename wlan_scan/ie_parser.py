"""802.11 Information Element parser.

One parser for every scanner that has raw IEs (wlanapi ctypes, the
PowerShell bridge, replayed recordings). Extracts:

* operating channel width and the centre channel of the operating block,
  from the *operation* elements (HT/VHT/HE/EHT Operation), clamped by band.
  Capability elements only say what a radio *can* do and are used for PHY
  generation detection, never for width.
* security (RSN / WPA1), BSS load, and PHY generation.

Element 255 is the Extension element: the first byte of its body is the
Element ID Extension, the rest is that element's body.
"""

from __future__ import annotations

from typing import Any, Optional

from .channels import BAND_24, BAND_5, BAND_6, clamp_width

# IEEE 802.11 Element IDs
IE_SSID = 0
IE_SUPPORTED_RATES = 1
IE_DS_PARAMETER = 3
IE_COUNTRY = 7
IE_QBSS_LOAD = 11       # BSS Load
IE_HT_CAPABILITIES = 45
IE_RSN = 48
IE_EXTENDED_RATES = 50
IE_HT_OPERATION = 61
IE_EXTENDED_CAPABILITIES = 127
IE_VHT_CAPABILITIES = 191
IE_VHT_OPERATION = 192
IE_VENDOR = 221
IE_EXTENSION = 255

# Element ID Extensions (first byte of an element-255 body)
EXT_HE_CAPABILITIES = 35
EXT_HE_OPERATION = 36
EXT_EHT_OPERATION = 106
EXT_EHT_CAPABILITIES = 108

# Security: RSN AKM suites
AKM_NAMES = {
    1: "802.1X (WPA2)",
    2: "PSK (WPA2)",
    3: "FT-802.1X",
    4: "FT-PSK",
    5: "802.1X-SHA256",
    6: "PSK-SHA256",
    7: "TDLS",
    8: "SAE (WPA3)",
    9: "FT-SAE",
    10: "AP-PEER-KEY",
    11: "802.1X-Suite-B",
    12: "802.1X-Suite-B-192",
    13: "FT-802.1X-SHA384",
    14: "FILS-SHA256",
    15: "FILS-SHA384",
    16: "FT-FILS-SHA256",
    17: "FT-FILS-SHA384",
    18: "OWE",
    19: "FT-PSK-SHA384",
    20: "PSK-SHA384",
}

CIPHER_NAMES_IE = {
    0: "Use Group",
    1: "WEP-40",
    2: "TKIP",
    4: "CCMP-128 (AES)",
    5: "WEP-104",
    6: "BIP-CMAC-128",
    7: "GCMP-128",
    8: "GCMP-256",
    9: "CCMP-256",
    10: "BIP-GMAC-128",
    11: "BIP-GMAC-256",
    12: "BIP-CMAC-256",
}


def iter_elements(data: bytes):
    """Yield ``(element_id, body)``; extension elements as ``(255, ext_id, body)``.

    Stops at the first truncated element.
    """
    offset, n = 0, len(data)
    while offset + 2 <= n:
        eid, elen = data[offset], data[offset + 1]
        offset += 2
        if offset + elen > n:
            break
        body = data[offset:offset + elen]
        offset += elen
        yield eid, body


def _band_hint(band: Optional[str]) -> Optional[str]:
    if not band:
        return None
    if "2.4" in band:
        return BAND_24
    if band.startswith("5"):
        return BAND_5
    if band.startswith("6"):
        return BAND_6
    return None


def _vht_style_width(chwidth: int, ccfs0: int, ccfs1: int) -> tuple[int, int]:
    """VHT Operation Information → (width MHz, centre channel); (0, 0) = 20/40.

    Handles the current signalling (chwidth 1 + CCFS1 for 160/80+80) and the
    deprecated chwidth 2 (160) / 3 (80+80) values.
    """
    if chwidth == 0:
        return 0, 0
    if chwidth == 1:
        if ccfs1 == 0:
            return 80, ccfs0
        if abs(ccfs1 - ccfs0) == 8:
            return 160, ccfs1
        return 160, ccfs0  # 80+80: report 160, centre on the primary 80
    if chwidth == 2:
        return 160, ccfs0
    if chwidth == 3:
        return 160, ccfs0
    return 0, 0


_SIX_GHZ_WIDTH = {0: 20, 1: 40, 2: 80, 3: 160}
_EHT_WIDTH = {0: 20, 1: 40, 2: 80, 3: 160, 4: 320}


def parse_information_elements(data: bytes, band: Optional[str] = None) -> dict[str, Any]:
    """Parse raw 802.11 IEs into a dict.

    ``band`` ("2.4 GHz" / "5 GHz" / "6 GHz") comes from the frequency the
    frame was received on; when omitted it is inferred from the IEs.

    Keys: security, auth_algo, cipher, pmf, channel_width, center_channel,
    primary_channel, band, station_count, channel_utilization,
    max_rate_mbps, ht_supported, vht_supported, he_supported, eht_supported,
    wifi_generation, supported_modes, phy_type
    """
    result: dict[str, Any] = {
        "security": "Open",
        "auth_algo": "",
        "cipher": "",
        "pmf": "",
        "channel_width": 20,
        "center_channel": 0,
        "primary_channel": 0,
        "band": "",
        "station_count": 0,
        "channel_utilization": 0.0,
        "max_rate_mbps": 0.0,
        "ht_supported": False,
        "vht_supported": False,
        "he_supported": False,
        "eht_supported": False,
        "wifi_generation": "",
        "supported_modes": "",
        "phy_type": "",
    }

    ds_channel = 0
    ht_op = None      # (primary, width, centre)
    vht_op = None     # (width, centre)
    he_vht_op = None  # (width, centre) from HE Operation's VHT Operation Information
    six_ghz_op = None  # (primary, width, centre)
    eht_op = None     # (width, centre)

    for eid, body in iter_elements(data):
        if eid == IE_DS_PARAMETER and len(body) >= 1:
            ds_channel = body[0]

        elif eid == IE_RSN:
            _parse_rsn(body, result)

        elif eid == IE_HT_CAPABILITIES and len(body) >= 2:
            result["ht_supported"] = True

        elif eid == IE_HT_OPERATION and len(body) >= 2:
            result["ht_supported"] = True
            primary = body[0]
            sec_offset = body[1] & 0x03
            sta_width = (body[1] >> 2) & 0x01
            if sta_width and sec_offset in (1, 3):
                centre = primary + 2 if sec_offset == 1 else primary - 2
                ht_op = (primary, 40, centre)
            else:
                ht_op = (primary, 20, primary)

        elif eid == IE_VHT_CAPABILITIES and len(body) >= 4:
            result["vht_supported"] = True

        elif eid == IE_VHT_OPERATION and len(body) >= 3:
            result["vht_supported"] = True
            w, c = _vht_style_width(body[0], body[1], body[2])
            if w:
                vht_op = (w, c)

        elif eid == IE_EXTENSION and len(body) >= 1:
            ext_id, ebody = body[0], body[1:]
            if ext_id == EXT_HE_CAPABILITIES:
                result["he_supported"] = True
            elif ext_id == EXT_HE_OPERATION and len(ebody) >= 6:
                result["he_supported"] = True
                params = ebody[0] | (ebody[1] << 8) | (ebody[2] << 16)
                pos = 6  # params(3) + BSS colour(1) + basic HE-MCS/NSS(2)
                if params & (1 << 14):  # VHT Operation Information present
                    if len(ebody) >= pos + 3:
                        w, c = _vht_style_width(ebody[pos], ebody[pos + 1], ebody[pos + 2])
                        if w:
                            he_vht_op = (w, c)
                    pos += 3
                if params & (1 << 15):  # Co-Hosted BSS: Max Co-Hosted BSSID Indicator
                    pos += 1
                if params & (1 << 17) and len(ebody) >= pos + 4:  # 6 GHz Operation Information
                    primary, ctrl, c0, c1 = ebody[pos:pos + 4]
                    w = _SIX_GHZ_WIDTH[ctrl & 0x03]
                    centre = (c1 or c0) if w == 160 else c0
                    six_ghz_op = (primary, w, centre)
            elif ext_id == EXT_EHT_CAPABILITIES:
                result["eht_supported"] = True
            elif ext_id == EXT_EHT_OPERATION and len(ebody) >= 5:
                result["eht_supported"] = True
                if ebody[0] & 0x01 and len(ebody) >= 8:  # EHT Operation Information present
                    ctrl, c0, c1 = ebody[5], ebody[6], ebody[7]
                    w = _EHT_WIDTH.get(ctrl & 0x07)
                    if w:
                        eht_op = (w, (c1 or c0) if w >= 160 else c0)

        elif eid == IE_QBSS_LOAD and len(body) >= 3:
            result["station_count"] = body[0] | (body[1] << 8)
            result["channel_utilization"] = round((body[2] / 255) * 100, 1)

        elif eid in (IE_SUPPORTED_RATES, IE_EXTENDED_RATES):
            for rate_byte in body:
                rate = (rate_byte & 0x7F) * 0.5
                if rate > result["max_rate_mbps"]:
                    result["max_rate_mbps"] = rate

    # ----- band -----
    b = _band_hint(band)
    if b is None:
        if six_ghz_op is not None:
            b = BAND_6
        else:
            primary = ht_op[0] if ht_op else ds_channel
            if primary:
                b = BAND_24 if primary <= 14 else BAND_5
    result["band"] = b or ""

    # ----- width / centre / primary from operation elements -----
    width, centre, primary = 20, 0, 0
    if ht_op:
        primary, width, centre = ht_op
    elif ds_channel:
        primary, centre = ds_channel, ds_channel
    if b != BAND_24:
        if vht_op:
            width, centre = vht_op
        elif he_vht_op:
            width, centre = he_vht_op
    if b == BAND_6 or (b is None and six_ghz_op):
        if six_ghz_op:
            primary, width, centre = six_ghz_op
    if eht_op and eht_op[0] >= width:
        width, centre = eht_op
    if b:
        width = clamp_width(width, b)
    if width == 20 and primary:
        centre = primary
    result["channel_width"] = width
    result["center_channel"] = centre
    result["primary_channel"] = primary

    # ----- generation / modes / PHY -----
    gen, modes, phy = _generation(result, b)
    result["wifi_generation"] = gen
    result["supported_modes"] = modes
    result["phy_type"] = phy

    return result


def _generation(r: dict, band: Optional[str]) -> tuple[str, str, str]:
    if band == BAND_6:
        base = []
    elif band == BAND_24:
        base = ["b", "g"]
    else:
        base = ["a"]
    modes = list(base)
    if r["ht_supported"] and band != BAND_6:
        modes.append("n")
    if r["vht_supported"] and band == BAND_5:
        modes.append("ac")
    if r["he_supported"]:
        modes.append("ax")
    if r["eht_supported"]:
        modes.append("be")
    if r["eht_supported"]:
        gen, phy = "Wi-Fi 7", "802.11be (EHT)"
    elif r["he_supported"]:
        gen, phy = ("Wi-Fi 6E" if band == BAND_6 else "Wi-Fi 6"), "802.11ax (HE)"
    elif r["vht_supported"] and band != BAND_24:
        gen, phy = "Wi-Fi 5", "802.11ac (VHT)"
    elif r["ht_supported"]:
        gen, phy = "Wi-Fi 4", "802.11n (HT)"
    else:
        gen = "Legacy"
        phy = "802.11a" if band == BAND_5 else "802.11g"
    return gen, "/".join(modes), phy


def _parse_rsn(body: bytes, result: dict) -> None:
    """Parse RSN (WPA2/WPA3) IE body."""
    if len(body) < 2:
        return

    # Version (2 bytes) — skip
    # Group cipher suite (4 bytes)
    group_cipher = _oid_to_cipher(body[2:6])

    # Pairwise cipher count (2 bytes)
    pair_count = body[6] | (body[7] << 8)
    pairwise_ciphers = []
    pos = 8
    for _ in range(pair_count):
        if pos + 4 > len(body):
            break
        pairwise_ciphers.append(_oid_to_cipher(body[pos : pos + 4]))
        pos += 4

    # AKM count (2 bytes)
    akm_count = body[pos] | (body[pos + 1] << 8)
    pos += 2
    akms = []
    for _ in range(akm_count):
        if pos + 4 > len(body):
            break
        suite_id = body[pos : pos + 4]
        # Suite type is the 4th byte (e.g., 2=PSK, 8=SAE)
        akm_type = suite_id[3]
        akms.append(akm_type)
        pos += 4

    # Build security description
    akm_names = [AKM_NAMES.get(a, f"AKM-{a}") for a in akms]
    cipher_names = pairwise_ciphers or [group_cipher]

    if any("WPA3" in a for a in akm_names) or any("SAE" in a for a in akm_names):
        result["security"] = "WPA3"
    elif any("WPA2" in a for a in akm_names):
        result["security"] = "WPA2"
    elif akm_names:
        result["security"] = akm_names[0]

    result["auth_algo"] = " / ".join(akm_names) if akm_names else ""
    result["cipher"] = " / ".join(filter(None, cipher_names))


def _oid_to_cipher(oid: bytes) -> str:
    """Convert a 4-byte OUI-based cipher suite to a human-readable name."""
    if len(oid) < 4:
        return ""
    suite_type = oid[3]
    return CIPHER_NAMES_IE.get(suite_type, f"Cipher-{suite_type}")


def apply_ies(bss) -> None:
    """Fill a BSSEntry's IE-derived fields from ``bss.ies_raw`` (in place)."""
    if not bss.ies_raw:
        return
    info = parse_information_elements(bss.ies_raw, bss.band or None)
    bss.security = info["security"]
    bss.auth_algo = info["auth_algo"]
    bss.cipher = info["cipher"]
    bss.channel_width = info["channel_width"]
    if info["center_channel"]:
        bss.center_channel = info["center_channel"]
    bss.station_count = info["station_count"]
    bss.channel_utilization = info["channel_utilization"]
    bss.wifi_generation = info["wifi_generation"]
    bss.supported_modes = info["supported_modes"]
