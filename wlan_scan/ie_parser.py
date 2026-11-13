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

# RSN AKM suite types (OUI 00-0F-AC), IEEE 802.11-2020 Table 9-151
AKM_NAMES = {
    1: "802.1X",
    2: "PSK",
    3: "FT-802.1X",
    4: "FT-PSK",
    5: "802.1X-SHA256",
    6: "PSK-SHA256",
    7: "TDLS",
    8: "SAE",
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
    22: "FT-802.1X-SHA384 (EXT)",
    23: "802.1X-SHA384",
    24: "SAE-EXT-KEY",
    25: "FT-SAE-EXT-KEY",
}

AKM_PSK = {2, 4, 6, 19, 20}
AKM_SAE = {8, 9, 24, 25}
AKM_8021X = {1, 3, 5, 14, 15, 16, 17}
AKM_SUITE_B = {11, 12, 13, 22, 23}
AKM_OWE = {18}

CIPHER_NAMES_IE = {
    0: "Use Group",
    1: "WEP-40",
    2: "TKIP",
    4: "CCMP-128 (AES)",
    5: "WEP-104",
    6: "BIP-CMAC-128",
    7: "Group addressed traffic not allowed",
    8: "GCMP-128",
    9: "GCMP-256",
    10: "CCMP-256",
    11: "BIP-GMAC-128",
    12: "BIP-GMAC-256",
    13: "BIP-CMAC-256",
}

# WPA1 (Microsoft vendor IE 00:50:F2 type 1) suite types
_WPA1_CIPHERS = {0: "Use Group", 1: "WEP-40", 2: "TKIP", 4: "CCMP-128 (AES)", 5: "WEP-104"}
_WPA1_AKMS = {1: "802.1X", 2: "PSK"}

OUI_IEEE = b"\x00\x0f\xac"
OUI_MS = b"\x00\x50\xf2"
OUI_WFA = b"\x50\x6f\x9a"
WFA_OWE_TRANSITION = 0x1C

#: Capability Information "Privacy" bit (WEP or any RSN/WPA)
CAP_PRIVACY = 0x0010

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


def parse_information_elements(data: bytes, band: Optional[str] = None,
                               capability: Optional[int] = None) -> dict[str, Any]:
    """Parse raw 802.11 IEs into a dict.

    ``band`` ("2.4 GHz" / "5 GHz" / "6 GHz") comes from the frequency the
    frame was received on; when omitted it is inferred from the IEs.
    ``capability`` is the beacon's Capability Information field; its
    Privacy bit identifies WEP when there is no RSN/WPA element.

    Keys: security, auth_algo, cipher, pmf ("Required"/"Capable"/"Disabled"/""), channel_width, center_channel,
    primary_channel, band, station_count, channel_utilization,
    max_rate_mbps (highest PHY rate the AP advertises; 0 = unknown),
    spatial_streams, ht_supported, vht_supported, he_supported, eht_supported,
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
        "spatial_streams": 0,
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
    rsn = None
    wpa1 = None
    owe_transition = False
    legacy_max = 0.0
    ht_rate = vht_rate = he_rate = eht_rate = None

    for eid, body in iter_elements(data):
        if eid == IE_DS_PARAMETER and len(body) >= 1:
            ds_channel = body[0]

        elif eid == IE_RSN:
            rsn = _parse_rsn(body)

        elif eid == IE_VENDOR and len(body) >= 4:
            if body[:3] == OUI_MS and body[3] == 0x01:
                wpa1 = _parse_wpa1(body)
            elif body[:3] == OUI_WFA and body[3] == WFA_OWE_TRANSITION:
                owe_transition = True

        elif eid == IE_HT_CAPABILITIES and len(body) >= 2:
            result["ht_supported"] = True
            if len(body) >= 7:
                info = body[0] | (body[1] << 8)
                nss = sum(1 for i in range(4) if body[3 + i])
                ht_rate = (nss, bool(info & 0x0020), bool(info & 0x0040))

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
            if len(body) >= 6:
                info = body[0] | (body[1] << 8) | (body[2] << 16) | (body[3] << 24)
                nss, code = _mcs_map_nss(body[4] | (body[5] << 8))
                vht_rate = (nss, {0: 7, 1: 8, 2: 9}.get(code, 0),
                            bool(info & (1 << 5)), bool(info & (1 << 6)))

        elif eid == IE_VHT_OPERATION and len(body) >= 3:
            result["vht_supported"] = True
            w, c = _vht_style_width(body[0], body[1], body[2])
            if w:
                vht_op = (w, c)

        elif eid == IE_EXTENSION and len(body) >= 1:
            ext_id, ebody = body[0], body[1:]
            if ext_id == EXT_HE_CAPABILITIES:
                result["he_supported"] = True
                if len(ebody) >= 19:  # MAC(6) + PHY(11) + Rx/Tx HE-MCS <=80 (2+2)
                    nss, code = _mcs_map_nss(ebody[17] | (ebody[18] << 8))
                    he_rate = (nss, {0: 7, 1: 9, 2: 11}.get(code, 0))
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
                if len(ebody) >= 14:  # MAC(2) + PHY(9) + <=80 MHz set (3)
                    # one byte per MCS range (0-9, 10-11, 12-13): Rx max NSS in low nibble
                    sets = ebody[11:14]
                    for mcs, byte in ((13, sets[2]), (11, sets[1]), (9, sets[0])):
                        if byte & 0x0F:
                            eht_rate = (byte & 0x0F, mcs)
                            break
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
                if rate_byte in (0xFF, 0xFE):  # BSS membership selectors (HT/VHT/SAE-H2E)
                    continue
                legacy_max = max(legacy_max, (rate_byte & 0x7F) * 0.5)

    # ----- security -----
    privacy = None if capability is None else bool(capability & CAP_PRIVACY)
    _apply_security(result, rsn, wpa1, privacy, owe_transition)

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

    # ----- max PHY rate -----
    rate, nss = max_phy_rate(width, b, ht_rate, vht_rate, he_rate, eht_rate)
    result["max_rate_mbps"] = rate or legacy_max
    result["spatial_streams"] = nss

    # ----- generation / modes / PHY -----
    gen, modes, phy = _generation(result, b)
    result["wifi_generation"] = gen
    result["supported_modes"] = modes
    result["phy_type"] = phy

    return result


def _mcs_map_nss(mcs_map: int) -> tuple[int, int]:
    """VHT/HE MCS map (2 bits per stream, 3 = unsupported) → (NSS, code of SS1)."""
    nss = 0
    for ss in range(8):
        if (mcs_map >> (2 * ss)) & 3 != 3:
            nss = ss + 1
    return nss, (mcs_map & 3) if nss else 3


# Bits per subcarrier x coding rate, by MCS index (802.11n/ac/ax/be share it)
_MCS_BITS = [0.5, 1, 1.5, 2, 3, 4, 4.5, 5, 6, 20 / 3, 7.5, 25 / 3, 9, 10]
# Data subcarriers by width: HT/VHT and HE/EHT
_NSD_VHT = {20: 52, 40: 108, 80: 234, 160: 468}
_NSD_HE = {20: 234, 40: 468, 80: 980, 160: 1960, 320: 3920}


def _rate(nsd: int, mcs: int, nss: int, symbol_us: float) -> float:
    return nsd * _MCS_BITS[mcs] * nss / symbol_us


def max_phy_rate(width: int, band: Optional[str], ht=None, vht=None, he=None,
                 eht=None) -> tuple[float, int]:
    """Highest PHY rate (Mbps) the AP advertises at its operating width.

    ht = (nss, sgi20, sgi40); vht = (nss, max_mcs, sgi80, sgi160);
    he = (nss, max_mcs); eht = (nss, max_mcs). Returns (Mbps, NSS); (0, 0)
    when no HT+ capabilities were seen. HE/EHT use 0.8 us GI (13.6 us symbol).
    """
    if eht and eht[0] and width in _NSD_HE:
        nss, mcs = eht
        return round(_rate(_NSD_HE[width], mcs, nss, 13.6), 1), nss
    if he and he[0] and width in _NSD_HE and width <= 160:
        nss, mcs = he
        return round(_rate(_NSD_HE[width], mcs, nss, 13.6), 1), nss
    if vht and vht[0] and band != BAND_24 and width in _NSD_VHT:
        nss, mcs, sgi80, sgi160 = vht
        if width == 20 and mcs == 9 and nss not in (3, 6):
            mcs = 8  # VHT MCS 9 is invalid at 20 MHz except for 3/6 streams
        sgi = sgi160 if width == 160 else (sgi80 if width == 80 else True)
        return round(_rate(_NSD_VHT[width], mcs, nss, 3.6 if sgi else 4.0), 1), nss
    if ht and ht[0]:
        nss, sgi20, sgi40 = ht
        w = 40 if width >= 40 else 20
        sgi = sgi40 if w == 40 else sgi20
        return round(_rate(_NSD_VHT[w], 7, min(nss, 4), 3.6 if sgi else 4.0), 1), nss
    return 0.0, 0


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


def _read_suites(body: bytes, pos: int) -> tuple[list[bytes], int, bool]:
    """Read a 2-byte count + that many 4-byte suites. Returns (suites, pos, ok)."""
    if pos + 2 > len(body):
        return [], pos, False
    count = body[pos] | (body[pos + 1] << 8)
    pos += 2
    suites = []
    for _ in range(count):
        if pos + 4 > len(body):
            return suites, pos, False
        suites.append(body[pos:pos + 4])
        pos += 4
    return suites, pos, True


def _suite_name(suite: bytes, oui: bytes, table: dict) -> str:
    if suite[:3] != oui:
        return f"Vendor-{suite.hex()}"
    return table.get(suite[3], f"{suite[3]}")


def _parse_rsn(body: bytes) -> dict:
    """RSN element body → {'akms', 'akm_types', 'ciphers', 'group', 'pmf', 'ok'}.

    Every field after the version is optional (802.11-2020 9.4.2.24), so a
    short element is read as far as it goes and never raises.
    """
    out = {"akms": [], "akm_types": set(), "ciphers": [], "group": "",
           "pmf": "", "ok": False}
    if len(body) < 2:
        return out
    out["ok"] = True
    pos = 2  # version
    if pos + 4 <= len(body):
        out["group"] = _suite_name(body[pos:pos + 4], OUI_IEEE, CIPHER_NAMES_IE)
        pos += 4
    else:
        return out
    pairwise, pos, ok = _read_suites(body, pos)
    out["ciphers"] = [_suite_name(c, OUI_IEEE, CIPHER_NAMES_IE) for c in pairwise]
    if not ok:
        return out
    akms, pos, ok = _read_suites(body, pos)
    for a in akms:
        out["akms"].append(_suite_name(a, OUI_IEEE, AKM_NAMES))
        if a[:3] == OUI_IEEE:
            out["akm_types"].add(a[3])
    if not ok:
        return out
    if pos + 2 <= len(body):
        caps = body[pos] | (body[pos + 1] << 8)
        if caps & 0x0040:      # MFPR
            out["pmf"] = "Required"
        elif caps & 0x0080:    # MFPC
            out["pmf"] = "Capable"
        else:
            out["pmf"] = "Disabled"
    return out


def _parse_wpa1(body: bytes) -> Optional[dict]:
    """Vendor element body; returns WPA1 info when it is 00:50:F2 type 1."""
    if len(body) < 4 or body[:3] != OUI_MS or body[3] != 0x01:
        return None
    b = body[4:]
    out = {"akms": [], "akm_types": set(), "ciphers": [], "group": ""}
    pos = 2  # version
    if pos + 4 > len(b):
        return out
    out["group"] = _suite_name(b[pos:pos + 4], OUI_MS, _WPA1_CIPHERS)
    pos += 4
    pairwise, pos, ok = _read_suites(b, pos)
    out["ciphers"] = [_suite_name(c, OUI_MS, _WPA1_CIPHERS) for c in pairwise]
    if ok:
        akms, pos, _ = _read_suites(b, pos)
        for a in akms:
            out["akms"].append(_suite_name(a, OUI_MS, _WPA1_AKMS))
            if a[:3] == OUI_MS:
                out["akm_types"].add(a[3])
    return out


def classify_security(rsn: Optional[dict], wpa1: Optional[dict],
                      privacy: Optional[bool], owe_transition: bool = False) -> str:
    """Human label from parsed RSN / WPA1 / Privacy bit."""
    if rsn and rsn.get("ok"):
        t = rsn["akm_types"]
        if t & AKM_SUITE_B:
            return "WPA3-Enterprise"
        if t & AKM_OWE:
            return "OWE (Enhanced Open)"
        sae, psk, dot1x = bool(t & AKM_SAE), bool(t & AKM_PSK), bool(t & AKM_8021X)
        if dot1x and not (sae or psk):
            # 802.1X-SHA256 only with PMF required is WPA3-Enterprise (non-192)
            if t <= {5} and rsn.get("pmf") == "Required":
                return "WPA3-Enterprise"
            return "WPA/WPA2-Enterprise" if wpa1 else "WPA2-Enterprise"
        if sae and psk:
            return "WPA2/WPA3"
        if sae:
            return "WPA3"
        if psk:
            return "WPA/WPA2" if wpa1 else "WPA2"
        if dot1x:
            return "WPA2-Enterprise"
        return "WPA2" if not rsn["akms"] else f"RSN ({', '.join(rsn['akms'])})"
    if wpa1 is not None:
        return "WPA-Enterprise" if 1 in wpa1["akm_types"] else "WPA"
    if privacy:
        return "WEP"
    if owe_transition:
        return "Open (OWE transition)"
    return "Open"


def _apply_security(result: dict, rsn, wpa1, privacy, owe_transition) -> None:
    result["security"] = classify_security(rsn, wpa1, privacy, owe_transition)
    akms: list[str] = []
    ciphers: list[str] = []
    for src in (wpa1, rsn):
        if not src:
            continue
        for a in src["akms"]:
            if a not in akms:
                akms.append(a)
        for c in (src["ciphers"] or ([src["group"]] if src["group"] else [])):
            if c not in ciphers:
                ciphers.append(c)
    if result["security"] == "WEP":
        ciphers = ["WEP"]
    result["auth_algo"] = " / ".join(akms)
    result["cipher"] = " / ".join(ciphers)
    result["pmf"] = rsn["pmf"] if rsn and rsn.get("ok") else ""

def apply_ies(bss) -> None:
    """Fill a BSSEntry's IE-derived fields from ``bss.ies_raw`` (in place)."""
    if not bss.ies_raw:
        return
    info = parse_information_elements(bss.ies_raw, bss.band or None,
                                      bss.capability_info or None)
    bss.security = info["security"]
    bss.pmf = info["pmf"]
    bss.auth_algo = info["auth_algo"]
    bss.cipher = info["cipher"]
    bss.channel_width = info["channel_width"]
    if info["center_channel"]:
        bss.center_channel = info["center_channel"]
    bss.station_count = info["station_count"]
    bss.channel_utilization = info["channel_utilization"]
    bss.wifi_generation = info["wifi_generation"]
    bss.supported_modes = info["supported_modes"]
    bss.max_rate = info["max_rate_mbps"]
    bss.spatial_streams = info["spatial_streams"]
