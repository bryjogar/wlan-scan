"""R13: IE blobs written out as literal hex (independent of ie_builder).

No over-the-air captures exist on this machine; these are typed in from the
802.11-2020 / 802.11ax / 802.11be field layouts so a bug shared by the
builder and the parser can't hide. Real captures can be added later with
`--record` on hardware (see docs/V2-STATUS.md).
"""

import random

import pytest

import ie_builder as B
from wlan_scan.ie_parser import iter_elements, parse_information_elements as parse

# 5 GHz Wi-Fi 6 AP, primary 52, 80 MHz (centre 58), WPA2/WPA3 transition, PMF capable.
AX5 = bytes.fromhex("".join([
    "0006" "4f6666696365",                   # SSID "Office"
    "0108" "8c129824b048606c",               # rates 6..54 (basic 6/12/24)
    "0b05" "0400663412",                     # BSS load: 4 STAs, util 0x66 (40.0 %)
    "2d1a" "ef0917" "ffff0000" + "00" * 12 + "0000" "00000000" "00",  # HT caps, 2SS
    "3d16" "34" "05" "0000" "0000" + "00" * 16,                        # HT op: pri 52, SCA, 40
    "bf0c" "b2798033" "faff0000faff0000",    # VHT caps, 2SS MCS0-9
    "c005" "01" "3a" "00" "fcff",            # VHT op: 80 MHz, CCFS0 = 58
    "ff18" "23" "000000000000" "0644000000000000000000" "faff" "faff" "0000",  # HE caps
    "ff07" "24" "040000" "05" "fcff",        # HE op (no optional fields)
    "3018" "0100" "000fac04" "0100" "000fac04" "0200" "000fac02" "000fac08" "8000",  # RSN
]))

# 6 GHz Wi-Fi 7 AP: HE op 6 GHz info pri 37 160 MHz; EHT op 320 MHz (CCFS1 31).
BE6 = bytes.fromhex("".join([
    "0003" "626536",
    "ff0c" "24" "000002" "05" "fcff" "25" "03" "27" "2f" "06",   # HE op + 6 GHz info
    "ff09" "6a" "01" "00000000" "04" "2f" "1f",                  # EHT op: 320, 47, 31
    "3018" "0100" "000fac04" "0100" "000fac04" "0200" "000fac08" "000fac18" "c000",
]))


def test_literal_wifi6_5ghz():
    r = parse(AX5, "5 GHz")
    assert (r["channel_width"], r["center_channel"], r["primary_channel"]) == (80, 58, 52)
    assert r["security"] == "WPA2/WPA3" and r["pmf"] == "Capable"
    assert r["station_count"] == 4 and r["channel_utilization"] == 40.0
    assert r["he_supported"] and r["vht_supported"] and not r["eht_supported"]
    assert r["wifi_generation"] == "Wi-Fi 6"
    assert r["max_rate_mbps"] == pytest.approx(1201.0, abs=0.2)


def test_literal_wifi7_6ghz():
    r = parse(BE6)
    assert r["band"] == "6 GHz"
    assert (r["channel_width"], r["center_channel"], r["primary_channel"]) == (320, 31, 37)
    assert r["security"] == "WPA3" and r["pmf"] == "Required"
    assert r["wifi_generation"] == "Wi-Fi 7"


def test_iter_elements_stops_at_truncation():
    assert list(iter_elements(b"\x00\x02ab\x03\x05\x01")) == [(0, b"ab")]


PROFILES = [B.profile_11n_24(), B.profile_11n_24(6, True), B.profile_11ac_5(),
            B.profile_11ax_24(), B.profile_11ax_5_160(), B.profile_11ax_6(),
            B.profile_11be_6_320(), AX5, BE6]


@pytest.mark.parametrize("blob", PROFILES)
def test_every_truncation_parses(blob):
    for cut in range(len(blob) + 1):
        for band in (None, "2.4 GHz", "5 GHz", "6 GHz"):
            parse(blob[:cut], band)


def test_random_garbage_never_raises():
    rnd = random.Random(1)
    for _ in range(2000):
        blob = bytes(rnd.randrange(256) for _ in range(rnd.randrange(80)))
        parse(blob, rnd.choice([None, "2.4 GHz", "5 GHz", "6 GHz"]), rnd.randrange(65536))


def test_corrupted_lengths_never_raise():
    rnd = random.Random(2)
    for blob in PROFILES:
        for _ in range(200):
            b = bytearray(blob)
            i = rnd.randrange(len(b))
            b[i] = rnd.randrange(256)
            parse(bytes(b), "5 GHz")
