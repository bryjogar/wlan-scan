"""R4: max PHY rate from MCS maps, NSS and operating width.

Expected values are the standard published rate-table maxima.
"""

import pytest

import ie_builder as B
from wlan_scan.ie_parser import max_phy_rate, parse_information_elements as parse


@pytest.mark.parametrize("ies,band,rate,nss", [
    (B.profile_11n_24(6, width40=False), "2.4 GHz", 144.4, 2),   # HT MCS15 20 MHz SGI
    (B.profile_11n_24(6, width40=True), "2.4 GHz", 300.0, 2),    # HT MCS15 40 MHz SGI
    (B.profile_11ac_5(), "5 GHz", 1300.0, 3),                    # VHT 80 3SS MCS9 SGI
    (B.profile_11ax_24(), "2.4 GHz", 286.8, 2),                  # HE 20 2SS MCS11
    (B.profile_11ax_5_160(), "5 GHz", 4803.9, 4),                # HE 160 4SS MCS11
    (B.profile_11ax_6(), "6 GHz", 1201.0, 2),                    # HE 80 2SS MCS11
    (B.profile_11be_6_320(), "6 GHz", 5764.7, 2),                # EHT 320 2SS MCS13
])
def test_profiles(ies, band, rate, nss):
    r = parse(ies, band)
    assert r["max_rate_mbps"] == pytest.approx(rate, abs=0.2)
    assert r["spatial_streams"] == nss


def test_legacy_only_uses_rates_ie():
    assert parse(B.rates(), "2.4 GHz")["max_rate_mbps"] == 54.0
    assert parse(B.rates((1, 2, 5.5, 11), basic=(1, 2)), "2.4 GHz")["max_rate_mbps"] == 11.0


def test_membership_selector_ignored():
    # 0xFF = HT PHY membership selector, not a 127.5 Mbps rate
    assert parse(B.ie(1, bytes([0x8c, 0x12, 0xff])), "5 GHz")["max_rate_mbps"] == 9.0


def test_vht_mcs9_invalid_at_20mhz_for_2ss():
    rate, _ = max_phy_rate(20, "5 GHz", vht=(2, 9, True, False))
    assert rate == pytest.approx(173.3, abs=0.1)  # MCS8 2SS SGI


def test_vht_lgi_when_no_sgi():
    rate, _ = max_phy_rate(80, "5 GHz", vht=(1, 9, False, False))
    assert rate == pytest.approx(390.0, abs=0.1)


def test_unknown_is_zero():
    assert max_phy_rate(20, "5 GHz") == (0.0, 0)
