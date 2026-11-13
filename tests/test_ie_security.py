"""R2: security labels, PMF, WPA1, WEP, truncated RSN."""

import pytest

import ie_builder as B
from wlan_scan.ie_parser import parse_information_elements as parse


def sec(*parts, capability=None):
    return parse(B.join(*parts), "5 GHz", capability)


@pytest.mark.parametrize("akms,label", [
    ((2,), "WPA2"),
    ((4,), "WPA2"),            # FT-PSK
    ((6,), "WPA2"),            # PSK-SHA256
    ((2, 4), "WPA2"),
    ((8,), "WPA3"),
    ((9,), "WPA3"),            # FT-SAE
    ((24,), "WPA3"),           # SAE-EXT-KEY (Wi-Fi 7)
    ((2, 8), "WPA2/WPA3"),     # transition mode
    ((4, 9, 2, 8), "WPA2/WPA3"),
    ((1,), "WPA2-Enterprise"),
    ((3,), "WPA2-Enterprise"),  # FT-802.1X
    ((1, 5), "WPA2-Enterprise"),
    ((12,), "WPA3-Enterprise"),  # Suite-B 192
    ((11,), "WPA3-Enterprise"),
    ((18,), "OWE (Enhanced Open)"),
])
def test_rsn_labels(akms, label):
    assert sec(B.rsn(akms=akms))["security"] == label


def test_wpa3_enterprise_sha256_with_pmf_required():
    assert sec(B.rsn(akms=(5,), mfpc=True, mfpr=True))["security"] == "WPA3-Enterprise"
    assert sec(B.rsn(akms=(5,), mfpc=True))["security"] == "WPA2-Enterprise"


@pytest.mark.parametrize("mfpc,mfpr,pmf", [
    (False, False, "Disabled"), (True, False, "Capable"), (True, True, "Required"),
])
def test_pmf(mfpc, mfpr, pmf):
    assert sec(B.rsn(mfpc=mfpc, mfpr=mfpr))["pmf"] == pmf


def test_auth_and_cipher_strings():
    r = sec(B.rsn(akms=(2, 8), pairwise=(4, 8)))
    assert r["auth_algo"] == "PSK / SAE"
    assert r["cipher"] == "CCMP-128 (AES) / GCMP-128"


def test_wpa1_only():
    r = sec(B.wpa1())
    assert r["security"] == "WPA"
    assert r["cipher"] == "TKIP"
    assert r["pmf"] == ""


def test_wpa1_enterprise():
    assert sec(B.wpa1(akms=(1,)))["security"] == "WPA-Enterprise"


def test_wpa1_plus_rsn_mixed():
    r = sec(B.wpa1(), B.rsn(pairwise=(2, 4)))
    assert r["security"] == "WPA/WPA2"
    assert "TKIP" in r["cipher"] and "CCMP-128 (AES)" in r["cipher"]


def test_wep_from_privacy_bit():
    assert sec(B.ssid("old"), capability=0x0011)["security"] == "WEP"
    assert sec(B.ssid("open"), capability=0x0001)["security"] == "Open"
    assert sec(B.ssid("unknown"))["security"] == "Open"


def test_owe_transition_open_bss():
    owe_tm = B.ie(221, b"\x50\x6f\x9a\x1c" + b"\x00" * 6)
    assert sec(B.ssid("guest"), owe_tm)["security"] == "Open (OWE transition)"


@pytest.mark.parametrize("cut", list(range(0, 26)))
def test_truncated_rsn_never_raises(cut):
    r = sec(B.rsn(akms=(2, 8), mfpc=True, truncate=cut))
    assert isinstance(r["security"], str)


def test_truncated_rsn_after_version_reads_what_is_there():
    # version + group only
    r = sec(B.rsn(truncate=6))
    assert r["security"] == "WPA2"
    assert r["cipher"] == "CCMP-128 (AES)"


def test_rsn_count_larger_than_body():
    body = b"\x01\x00" + B.OUI_IEEE + b"\x04" + b"\xff\x00" + B.OUI_IEEE + b"\x04"
    r = sec(B.ie(48, body))
    assert r["cipher"] == "CCMP-128 (AES)"


def test_vendor_akm_does_not_crash():
    body = (b"\x01\x00" + B.OUI_IEEE + b"\x04" + b"\x01\x00" + B.OUI_IEEE + b"\x04"
            + b"\x01\x00" + b"\x00\x40\x96\x00")
    r = sec(B.ie(48, body))
    assert r["auth_algo"].startswith("Vendor-")
