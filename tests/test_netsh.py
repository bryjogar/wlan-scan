"""R12/R13: netsh text parsing (English Windows 11 sample) and estimates."""

from datetime import datetime

import pytest

from wlan_scan.netsh_scanner import (estimate_channel_width, normalize_security,
                                     parse_netsh_output, signal_to_rssi)

# Windows 11 23H2 layout (English). Values synthetic.
SAMPLE = """
Interface name : Wi-Fi
There are 3 networks currently visible.

SSID 1 : Office
    Network type            : Infrastructure
    Authentication          : WPA2-Personal
    Encryption              : CCMP
    BSSID 1                 : a0:36:bc:10:00:02
         Signal             : 92%
         Radio type         : 802.11ax
         Band               : 5 GHz
         Channel            : 36
         Bss Load:
             Connected Stations:        4
             Channel Utilization:       40 (15 %)
             Medium Available Capacity: 31250 (1000000 us/s)
         Basic rates (Mbps) : 6 12 24
         Other rates (Mbps) : 9 18 36 48 54
    BSSID 2                 : a0:36:bc:10:00:03
         Signal             : 70%
         Radio type         : 802.11ax
         Band               : 6 GHz
         Channel            : 37
         Basic rates (Mbps) : 6 12 24
         Other rates (Mbps) : 9 18 36 48 54

SSID 2 :
    Network type            : Infrastructure
    Authentication          : WPA3-Personal
    Encryption              : GCMP-256
    BSSID 1                 : b6:fb:e4:01:02:03
         Signal             : 40%
         Radio type         : 802.11ac
         Channel            : 100
         Basic rates (Mbps) : 6 12 24

SSID 3 : CoffeeShop
    Network type            : Infrastructure
    Authentication          : Open
    Encryption              : None
    BSSID 1                 : 3c:84:6a:aa:bb:cc
         Signal             : 0%
         Radio type         : 802.11n
         Channel            : 11
         Basic rates (Mbps) : 1 2 5.5 11
"""


def _by_bssid():
    r = parse_netsh_output(SAMPLE, datetime(2026, 11, 13))
    return r, {b.bssid: b for n in r.networks for b in n.bss_list}


def test_parses_all_bss_with_their_own_fields():
    r, b = _by_bssid()
    assert r.interface_name == "Wi-Fi"
    assert len(b) == 4
    five = b["A0:36:BC:10:00:02"]
    assert (five.channel, five.band, five.rssi) == (36, "5 GHz", -54)
    assert five.station_count == 4 and five.channel_utilization == 15.0
    assert five.security == "WPA2" and five.cipher == "CCMP"
    assert five.phy_type == "802.11ax (HE)" and five.wifi_generation == "Wi-Fi 6"
    # Band line decides 6 GHz (channel 37 alone would look like 5 GHz)
    six = b["A0:36:BC:10:00:03"]
    assert six.band == "6 GHz" and six.wifi_generation == "Wi-Fi 6E"
    assert six.station_count == 0  # no BSS-load leak from the previous BSSID


def test_hidden_ssid_and_old_layout_without_band_line():
    _, b = _by_bssid()
    hidden = b["B6:FB:E4:01:02:03"]
    assert hidden.ssid == "" and hidden.band == "5 GHz"
    assert hidden.security == "WPA3" and hidden.cipher == "GCMP-256"
    assert hidden.vendor == "Randomized"


def test_open_network_and_values_marked_estimated():
    _, b = _by_bssid()
    cafe = b["3C:84:6A:AA:BB:CC"]
    assert cafe.security == "Open" and cafe.cipher == ""
    assert cafe.rssi == -100
    assert cafe.is_estimated("rssi") and cafe.is_estimated("channel_width")
    assert cafe.max_rate == 0  # legacy rates are not a PHY max


@pytest.mark.parametrize("pct,dbm", [(0, -100), (50, -75), (92, -54), (100, -50), (120, -50)])
def test_signal_to_rssi(pct, dbm):
    assert signal_to_rssi(pct) == dbm


@pytest.mark.parametrize("auth,enc,label", [
    ("WPA2-Personal", "CCMP", "WPA2"),
    ("WPA3-Personal", "GCMP-256", "WPA3"),
    ("WPA2-Enterprise", "CCMP", "WPA2-Enterprise"),
    ("WPA3-Enterprise", "GCMP-256", "WPA3-Enterprise"),
    ("WPA-Personal", "TKIP", "WPA"),
    ("Open", "None", "Open"),
    ("Open", "WEP", "WEP"),
    ("Shared", "WEP", "WEP"),
    ("OWE", "CCMP", "OWE (Enhanced Open)"),
])
def test_security(auth, enc, label):
    assert normalize_security(auth, enc)[0] == label


def test_width_estimates():
    assert estimate_channel_width("802.11ax (HE)", "2.4 GHz") == 20
    assert estimate_channel_width("802.11ax (HE)", "5 GHz") == 80
    assert estimate_channel_width("802.11be (EHT)", "6 GHz") == 320
    assert estimate_channel_width("802.11n (HT)", "5 GHz") == 40


def test_empty_output():
    assert parse_netsh_output("").networks == []
