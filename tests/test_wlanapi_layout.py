"""R3: ctypes structs match wlanapi.h (sizes/offsets from the Windows SDK)."""

import ctypes

from wlan_scan.scanner import (DOT11_SSID, GUID, WLAN_AVAILABLE_NETWORK, WLAN_BSS_ENTRY,
                               WLAN_INTERFACE_INFO, WLAN_RATE_SET)


def off(struct, field):
    return getattr(struct, field).offset


def test_bss_entry_layout():
    assert ctypes.sizeof(DOT11_SSID) == 36
    assert ctypes.sizeof(WLAN_RATE_SET) == 256
    assert ctypes.sizeof(WLAN_BSS_ENTRY) == 360
    expected = {
        "uPhyId": 36, "dot11Bssid": 40, "dot11BssType": 48, "dot11BssPhyType": 52,
        "lRssi": 56, "uLinkQuality": 60, "bInRegDomain": 64, "usBeaconPeriod": 66,
        "ullTimestamp": 72, "ullHostTimestamp": 80, "usCapabilityInformation": 88,
        "ulChCenterFrequency": 92, "wlanRateSet": 96, "ulIeOffset": 352, "ulIeSize": 356,
    }
    for field, o in expected.items():
        assert off(WLAN_BSS_ENTRY, field) == o, field


def test_available_network_and_interface_info():
    assert ctypes.sizeof(WLAN_AVAILABLE_NETWORK) == 628
    assert ctypes.sizeof(GUID) == 16
    assert ctypes.sizeof(WLAN_INTERFACE_INFO) == 532


def test_rate_set_max_masks_basic_bit():
    rs = WLAN_RATE_SET()
    rs.uRateSetLength = 6  # bytes → 3 rates
    rs.usRateSet[0] = 0x8000 | 12   # 6 Mbps basic
    rs.usRateSet[1] = 108           # 54 Mbps
    rs.usRateSet[2] = 0x8000 | 48   # 24 Mbps basic
    rs.usRateSet[3] = 999           # beyond length: ignored
    assert rs.max_mbps() == 54.0


def test_guid_str_unsigned():
    g = GUID()
    g.Data1, g.Data2, g.Data3 = 0x12345678, 0xABCD, 0xEF01
    for i in range(8):
        g.Data4[i] = 0xF0 + i
    assert str(g) == "12345678-ABCD-EF01-F0F1-F2F3F4F5F6F7"
