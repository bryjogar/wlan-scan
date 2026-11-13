"""R13: frequency ↔ channel, including 6 GHz and channel 14."""

import pytest

from wlan_scan.channels import band_from_channel, channel_to_freq, clamp_width, freq_to_channel
from wlan_scan.scanner import freq_to_channel as freq_khz_to_channel


@pytest.mark.parametrize("mhz,ch,band", [
    (2412, 1, "2.4 GHz"), (2437, 6, "2.4 GHz"), (2472, 13, "2.4 GHz"),
    (2484, 14, "2.4 GHz"),                  # Japan ch 14 is not 2407 + 5*14
    (5180, 36, "5 GHz"), (5500, 100, "5 GHz"), (5720, 144, "5 GHz"),
    (5745, 149, "5 GHz"), (5825, 165, "5 GHz"), (5885, 177, "5 GHz"),
    (5935, 2, "6 GHz"),                     # 6 GHz ch 2 (the odd one)
    (5955, 1, "6 GHz"), (5975, 5, "6 GHz"), (6135, 37, "6 GHz"),
    (6415, 93, "6 GHz"), (7115, 233, "6 GHz"),
])
def test_freq_to_channel(mhz, ch, band):
    assert freq_to_channel(mhz) == (ch, band)
    assert channel_to_freq(ch, band) == mhz


@pytest.mark.parametrize("mhz", [0, 2400, 2485, 4920, 5000, 5930, 7200, 2413])
def test_unknown_frequencies(mhz):
    assert freq_to_channel(mhz) == (0, "Unknown")


def test_khz_wrapper_used_by_wlanapi():
    assert freq_khz_to_channel(5955000) == (1, "6 GHz")   # v1 said channel 2
    assert freq_khz_to_channel(2484000) == (14, "2.4 GHz")


def test_band_from_channel_and_clamp():
    assert band_from_channel(14) == "2.4 GHz"
    assert band_from_channel(36) == "5 GHz"
    assert band_from_channel(200) == "Unknown"  # 6 GHz numbers are ambiguous
    assert clamp_width(80, "2.4 GHz") == 40
    assert clamp_width(320, "5 GHz") == 160
    assert clamp_width(320, "6 GHz") == 320
