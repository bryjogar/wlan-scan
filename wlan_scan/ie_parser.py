"""802.11 Information Element parser.

Extracts security details, channel width, BSS load (channel utilization),
HT/VHT/HE capabilities, and more from raw IE data.
"""

from typing import Any

# IEEE 802.11 Element IDs
IE_SSID = 0
IE_SUPPORTED_RATES = 1
IE_DS_PARAMETER = 3
IE_COUNTRY = 7
IE_QBSS_LOAD = 11       # BSS Load / QBSS Load
IE_HT_CAPABILITIES = 45
IE_RSN = 48             # WPA2
IE_EXTENDED_RATES = 50
IE_HT_OPERATION = 61
IE_VHT_CAPABILITIES = 191
IE_VHT_OPERATION = 192
IE_EXTENDED_CAPABILITIES = 127
IE_HE_CAPABILITIES = 255  # Wi-Fi 6 (container — extension required)
IE_EHT_CAPABILITIES = 0   # Wi-Fi 7 (extension element)

# Extension element IDs
EXT_HE_CAPABILITIES = 35
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


def parse_information_elements(data: bytes) -> dict[str, Any]:
    """Parse raw 802.11 IE data into a dictionary.

    Returns keys: security, auth_algo, cipher, channel_width, station_count,
                  channel_utilization, max_rate_mbps, ht_supported, vht_supported,
                  he_supported, eht_supported, raw_ies
    """
    result: dict[str, Any] = {
        "security": "Open",
        "auth_algo": "",
        "cipher": "",
        "channel_width": 20,
        "station_count": 0,
        "channel_utilization": 0.0,
        "max_rate_mbps": 0.0,
        "ht_supported": False,
        "vht_supported": False,
        "he_supported": False,
        "eht_supported": False,
    }

    offset = 0
    length = len(data)

    while offset + 2 <= length:
        element_id = data[offset]
        element_len = data[offset + 1]
        offset += 2

        if offset + element_len > length:
            break

        body = data[offset : offset + element_len]

        # ----- RSN (WPA2/WPA3) -----
        if element_id == IE_RSN and element_len >= 2:
            _parse_rsn(body, result)

        # ----- HT Capabilities (802.11n) -----
        elif element_id == IE_HT_CAPABILITIES and element_len >= 26:
            result["ht_supported"] = True
            # Supported MCS set starts at offset 3
            # First two bytes: channel width supported
            if element_len >= 2:
                ht_info = body[0] | (body[1] << 8)
                if ht_info & 0x0002:  # Support for 40 MHz
                    result["channel_width"] = max(result["channel_width"], 40)

        # ----- HT Operation (802.11n) -----
        elif element_id == IE_HT_OPERATION and element_len >= 5:
            sta_chan_width = body[2]  # STA Channel Width
            sec_chan_offset = body[1] & 0x03
            if sta_chan_width == 1 and sec_chan_offset > 0:
                result["channel_width"] = max(result["channel_width"], 40)

        # ----- VHT Capabilities (802.11ac) -----
        elif element_id == IE_VHT_CAPABILITIES and element_len >= 4:
            result["vht_supported"] = True
            vht_info = body[0] | (body[1] << 8) | (body[2] << 16) | (body[3] << 24)
            max_mpdu = (vht_info >> 1) & 0x3
            # Channel width from VHT supported channel width set
            # Bits 2-3: 0=none, 1=160, 2=80+80, 3=reserved
            cw_set = (vht_info >> 2) & 0x3
            if cw_set == 1:
                result["channel_width"] = max(result["channel_width"], 160)
            elif cw_set in (2, 3):
                result["channel_width"] = max(result["channel_width"], 160)
            else:
                result["channel_width"] = max(result["channel_width"], 80)

        # ----- HE Capabilities (802.11ax, Wi-Fi 6) - container -----
        elif element_id == IE_HE_CAPABILITIES:
            # This is a container. The actual HE capabilities are in an extension.
            sub_offset = 0
            while sub_offset + 2 <= element_len:
                ext_id = body[sub_offset]
                ext_len = body[sub_offset + 1]
                sub_offset += 2
                if sub_offset + ext_len > element_len:
                    break
                ext_body = body[sub_offset : sub_offset + ext_len]

                if ext_id == EXT_HE_CAPABILITIES:
                    result["he_supported"] = True
                    # Parse HE MAC capabilities for channel width
                    if ext_len >= 6:
                        he_mac = ext_body
                        # Byte 0, bits 1-4: supported channel width set
                        # PHY info at offset 5+
                    if ext_len >= 11:
                        # HE PHY capabilities info
                        he_phy_info = ext_body[7:]
                        if len(he_phy_info) >= 4:
                            phy_flags = he_phy_info[0]
                            # Check 40/80/160 MHz support in 2.4 and 5 GHz
                            if phy_flags & 0x02:  # 40 MHz in 2.4 GHz
                                pass  # already handled
                            # Support for 80, 160 in subsequent bytes
                    # Be conservative - assume at least 80 for HE
                    result["channel_width"] = max(result["channel_width"], 80)

                sub_offset += ext_len

        # ----- EHT Capabilities (802.11be, Wi-Fi 7) -----
        elif element_id == IE_EXTENDED_CAPABILITIES and element_len > 0:
            # Check for EHT extension
            sub_offset = 0
            while sub_offset + 2 <= element_len:
                ext_id = body[sub_offset]
                ext_len = body[sub_offset + 1]
                sub_offset += 2
                if sub_offset + ext_len > element_len:
                    break
                if ext_id == EXT_EHT_CAPABILITIES:
                    result["eht_supported"] = True
                    result["channel_width"] = max(result["channel_width"], 160)
                sub_offset += ext_len

        # ----- BSS Load / QBSS Load (channel utilization) -----
        elif element_id == IE_QBSS_LOAD and element_len >= 3:
            station_count = (body[0] | (body[1] << 8))
            utilization_raw = body[2]
            result["station_count"] = station_count
            result["channel_utilization"] = round((utilization_raw / 255) * 100, 1)

        # ----- Supported Rates -----
        elif element_id == IE_SUPPORTED_RATES:
            for rate_byte in body:
                if rate_byte & 0x80:
                    rate = (rate_byte & 0x7F) * 0.5
                else:
                    rate = rate_byte * 0.5
                if rate > result["max_rate_mbps"]:
                    result["max_rate_mbps"] = rate

        elif element_id == IE_EXTENDED_RATES:
            for rate_byte in body:
                if rate_byte & 0x80:
                    rate = (rate_byte & 0x7F) * 0.5
                else:
                    rate = rate_byte * 0.5
                if rate > result["max_rate_mbps"]:
                    result["max_rate_mbps"] = rate

        offset += element_len

    return result


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
