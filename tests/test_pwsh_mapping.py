"""R3: the PowerShell bridge and the ctypes path give identical results."""

import json

import ie_builder as B
from wlan_scan.bss import build_bss_entry
from wlan_scan.pwsh_scanner import entry_from_json, result_from_json

IES = B.profile_11ax_5_160(36)

BRIDGE_JSON = json.dumps({
    "interface": "Qualcomm FastConnect 7800",
    "state": 1,
    "count": 2,
    "entries": [
        {"ssidHex": b"Office".hex(), "bssid": "A0:36:BC:10:00:02", "rssi": -48,
         "channelFrequency": 5180000, "phyType": 10, "beaconPeriod": 100,
         "capabilities": 0x1111, "maxRate": 54.0, "ieHex": IES.hex().upper()},
        {"ssidHex": "", "bssid": "B6:FB:E4:01:02:03", "rssi": -80,
         "channelFrequency": 2437000, "phyType": 7, "beaconPeriod": 100,
         "capabilities": 0x0001, "maxRate": 0, "ieHex": ""},
    ],
})


def test_bridge_matches_ctypes_builder():
    data = json.loads(BRIDGE_JSON)
    via_pwsh = entry_from_json(data["entries"][0])
    via_ctypes = build_bss_entry(
        ssid=b"Office", bssid=bytes.fromhex("a036bc100002"), rssi=-48,
        freq_khz=5180000, phy_id=10, beacon_period=100, capability=0x1111,
        ies=IES, legacy_max_rate=54.0)
    for field in ("ssid", "bssid", "rssi", "channel", "band", "channel_width",
                  "center_channel", "center_frequency", "security", "auth_algo",
                  "cipher", "pmf", "wifi_generation", "supported_modes", "phy_type"):
        assert getattr(via_pwsh, field) == getattr(via_ctypes, field), field


def test_bridge_mapping_values():
    r = result_from_json(json.loads(BRIDGE_JSON))
    assert r.interface_name == "Qualcomm FastConnect 7800"
    office = next(n for n in r.networks if n.ssid == "Office").bss_list[0]
    assert (office.channel, office.band, office.channel_width) == (36, "5 GHz", 160)
    assert office.security == "WPA3" and office.pmf == "Required"
    assert office.phy_type == "802.11ax (HE)"
    assert office.wifi_generation == "Wi-Fi 6"
    hidden = next(n for n in r.networks if n.ssid == "").bss_list[0]
    assert hidden.band == "2.4 GHz" and hidden.channel == 6
    assert hidden.security == "Open"  # no IEs, Privacy bit clear
    assert hidden.phy_type == "802.11n (HT)"  # DOT11_PHY_TYPE 7 = HT


def test_single_entry_unrolled_by_convertto_json():
    data = json.loads(BRIDGE_JSON)
    data["entries"] = data["entries"][0]
    assert len(result_from_json(data).networks) == 1


def test_old_bridge_output_with_ssid_string():
    e = entry_from_json({"ssid": "HIDDEN", "bssid": "aa:bb:cc:dd:ee:ff", "rssi": -70,
                         "channelFrequency": 5955000, "phyType": 10, "ieHex": ""})
    assert e.ssid == "" and e.band == "6 GHz" and e.channel == 1
    assert e.bssid == "AA:BB:CC:DD:EE:FF"


def test_privacy_bit_without_ies_is_wep():
    e = build_bss_entry(ssid=b"old", bssid="00:11:22:33:44:55", rssi=-60,
                        freq_khz=2412000, phy_id=6, capability=0x0011)
    assert e.security == "WEP"


def test_non_utf8_ssid_does_not_crash():
    e = build_bss_entry(ssid=b"\xff\xfeCaf\xe9", bssid="00:11:22:33:44:55", rssi=-60,
                        freq_khz=2412000, phy_id=7)
    assert "Caf" in e.ssid
