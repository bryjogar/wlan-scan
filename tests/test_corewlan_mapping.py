"""Tests for the CoreWLAN scanner's mapping logic (pure functions).

The pyobjc CoreWLAN module only exists on macOS, so these tests cover the
pure mapping helpers: security enum → (security, auth), channel → band,
width → generation, and the rate estimate.
"""

from wlan_scan.corewlan_scanner import (
    _security_from_cw,
    _band_from_channel,
    _wifi_generation,
    _max_rate_hint,
)


def test_security_mapping():
    assert _security_from_cw(0) == ("Open", "Open")
    assert _security_from_cw(1) == ("WEP", "WEP")
    assert _security_from_cw(2) == ("WPA", "WPA")
    assert _security_from_cw(4) == ("WPA2", "WPA2")
    assert _security_from_cw(12) == ("WPA3", "WPA3")
    assert _security_from_cw(16) == ("Enterprise", "802.1X")
    # unknown values degrade to a labeled Unknown
    sec, auth = _security_from_cw(999)
    assert sec == "Unknown"
    assert "999" in auth


def test_band_from_channel():
    assert _band_from_channel(1) == "2.4 GHz"
    assert _band_from_channel(6) == "2.4 GHz"
    assert _band_from_channel(14) == "2.4 GHz"
    assert _band_from_channel(29) == "6 GHz"
    assert _band_from_channel(36) == "5 GHz"
    assert _band_from_channel(149) == "5 GHz"
    assert _band_from_channel(200) == "6 GHz"


def test_generation_from_width_band():
    assert _wifi_generation(20, "2.4 GHz") == "Wi-Fi 4"
    assert _wifi_generation(40, "5 GHz") == "Wi-Fi 5"
    assert _wifi_generation(80, "5 GHz") == "Wi-Fi 5"
    assert _wifi_generation(160, "5 GHz") == "Wi-Fi 6"
    assert _wifi_generation(20, "6 GHz") == "Wi-Fi 6E"


def test_max_rate_hint():
    assert _max_rate_hint(20, "2.4 GHz") == 150.0
    assert _max_rate_hint(40, "2.4 GHz") == 300.0
    assert _max_rate_hint(80, "5 GHz") == 1200.0
    assert _max_rate_hint(160, "5 GHz") == 2400.0
