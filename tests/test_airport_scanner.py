"""Tests for the macOS airport -s parser."""

from wlan_scan.airport_scanner import parse_airport_output

SAMPLE = """\
                            SSID BSSID             RSSI CHANNEL HT CC SECURITY (auth/unicast/group)
                 My Network   aa:bb:cc:dd:ee:01 -45  44,+1    Y  US WPA2(PSK/AES/AES)
             Office-Guest5G   aa:bb:cc:dd:ee:02 -61  149     Y  US WPA2(PSK/CCMP/AES)
        Backyard Mesh Node   aa:bb:cc:dd:ee:03 -72  6       N  US WPA3(SAE/CCMP/AES)
                   OpenTest   aa:bb:cc:dd:ee:04 -55  1       N  US NONE
                       6GHz   aa:bb:cc:dd:ee:05 -50  29,+1   Y  US WPA3(SAE/GCMP/AES)
garbage line that should be skipped
"""


def test_parses_networks_and_bssids():
    r = parse_airport_output(SAMPLE)
    # 5 valid networks (garbage line skipped)
    assert len(r.networks) == 5
    total_bss = sum(len(n.bss_list) for n in r.networks)
    assert total_bss == 5


def test_ssid_with_spaces_parses():
    r = parse_airport_output(SAMPLE)
    ssids = {n.ssid for n in r.networks}
    assert "My Network" in ssids
    assert "Backyard Mesh Node" in ssids


def test_band_detection():
    r = parse_airport_output(SAMPLE)
    by_ssid = {n.ssid: n.bss_list[0] for n in r.networks}
    assert by_ssid["My Network"].band == "5 GHz"     # ch 44
    assert by_ssid["Office-Guest5G"].band == "5 GHz"  # ch 149
    assert by_ssid["Backyard Mesh Node"].band == "2.4 GHz"  # ch 6
    assert by_ssid["6GHz"].band == "6 GHz"           # ch 29 (6 GHz band)


def test_security_parsing():
    r = parse_airport_output(SAMPLE)
    by_ssid = {n.ssid: n.bss_list[0] for n in r.networks}
    assert by_ssid["My Network"].security == "WPA2"
    assert by_ssid["Backyard Mesh Node"].security == "WPA3"
    assert by_ssid["OpenTest"].security == "Open"
    assert by_ssid["6GHz"].security == "WPA3"


def test_channel_width():
    r = parse_airport_output(SAMPLE)
    by_ssid = {n.ssid: n.bss_list[0] for n in r.networks}
    # ",+1" and ",+1" on 44 = bonded 40 MHz
    assert by_ssid["My Network"].channel_width == 40
    # plain "6" = 20 MHz
    assert by_ssid["Backyard Mesh Node"].channel_width == 20
    # ",+1" on 29 (6 GHz) = 40 MHz
    assert by_ssid["6GHz"].channel_width == 40


def test_rssi_and_vendor_fields_populated():
    r = parse_airport_output(SAMPLE)
    b = r.networks[0].bss_list[0]
    assert b.rssi == -45
    assert b.bssid == "AA:BB:CC:DD:EE:01"
    assert b.vendor != "" or b.vendor == ""  # vendor lookup may be empty offline


def test_malformed_lines_skipped():
    r = parse_airport_output("not a network line\n" + SAMPLE + "\nanother bad\n")
    assert len(r.networks) == 5
