"""R1: Extension-element parsing and width from operation elements."""

import pytest

import ie_builder as B
from wlan_scan.channels import standard_center_channel
from wlan_scan.ie_parser import parse_information_elements as parse


@pytest.mark.parametrize("ies,band,width,centre,primary,gen", [
    (B.profile_11n_24(6, width40=False), "2.4 GHz", 20, 6, 6, "Wi-Fi 4"),
    (B.profile_11n_24(6, width40=True), "2.4 GHz", 40, 8, 6, "Wi-Fi 4"),
    (B.profile_11ac_5(36, 42), "5 GHz", 80, 42, 36, "Wi-Fi 5"),
    (B.profile_11ax_24(1), "2.4 GHz", 20, 1, 1, "Wi-Fi 6"),
    (B.profile_11ax_5_160(36), "5 GHz", 160, 50, 36, "Wi-Fi 6"),
    (B.profile_11ax_6(37, 39, 0, 2), "6 GHz", 80, 39, 37, "Wi-Fi 6E"),
    (B.profile_11ax_6(37, 39, 47, 3), "6 GHz", 160, 47, 37, "Wi-Fi 6E"),
    (B.profile_11be_6_320(37), "6 GHz", 320, 31, 37, "Wi-Fi 7"),
])
def test_width_centre_primary_generation(ies, band, width, centre, primary, gen):
    r = parse(ies, band)
    assert r["channel_width"] == width
    assert r["center_channel"] == centre
    assert r["primary_channel"] == primary
    assert r["wifi_generation"] == gen


def test_he_capabilities_do_not_widen_24ghz():
    # The old parser drew every HE network 80 MHz wide because HE *caps*
    # claim 80 MHz support. Operation elements say 20.
    r = parse(B.profile_11ax_24(), "2.4 GHz")
    assert r["he_supported"] is True
    assert r["channel_width"] == 20


def test_vht_caps_alone_do_not_set_width():
    ies = B.join(B.ht_cap(), B.ht_op(36, 0, 0), B.vht_cap(width_set=1))
    assert parse(ies, "5 GHz")["channel_width"] == 20


def test_extension_id_is_first_body_byte():
    # HE capabilities as an element-255 body: [35, ...]. The old loop read
    # body[0]/body[1] as a nested TLV.
    r = parse(B.he_cap(), "5 GHz")
    assert r["he_supported"] and not r["eht_supported"]


def test_eht_found_under_extension_not_extended_caps():
    r = parse(B.join(B.he_cap(), B.eht_cap()), "6 GHz")
    assert r["eht_supported"] is True
    # Element 127 (Extended Capabilities) is not an EHT container.
    r = parse(B.ie(127, bytes([108, 1, 0, 0, 0])), "5 GHz")
    assert r["eht_supported"] is False


def test_320_clamped_outside_6ghz():
    ies = B.join(B.ht_op(36, 1, 1), B.vht_op(1, 42, 50), B.he_cap(), B.he_op(),
                 B.eht_cap(), B.eht_op(4, 50, 50))
    assert parse(ies, "5 GHz")["channel_width"] == 160


def test_40_clamped_on_24ghz_even_if_vht_op_present():
    ies = B.join(B.ht_op(6, 1, 1), B.vht_op(1, 42))
    assert parse(ies, "2.4 GHz")["channel_width"] == 40


def test_ht_secondary_below():
    r = parse(B.ht_op(40, 3, 1), "5 GHz")
    assert (r["channel_width"], r["center_channel"]) == (40, 38)


def test_vht_deprecated_160_signalling():
    r = parse(B.join(B.ht_op(36, 1, 1), B.vht_op(2, 50)), "5 GHz")
    assert (r["channel_width"], r["center_channel"]) == (160, 50)


def test_he_operation_vht_info_used_when_no_vht_op():
    ies = B.join(B.ht_op(36, 1, 1), B.he_cap(), B.he_op(vht_info=(1, 42, 0)))
    assert parse(ies, "5 GHz")["channel_width"] == 80


def test_band_inferred_when_not_given():
    assert parse(B.profile_11ax_6())["band"] == "6 GHz"
    assert parse(B.profile_11ac_5())["band"] == "5 GHz"
    assert parse(B.profile_11n_24())["band"] == "2.4 GHz"


def test_truncated_operation_elements_do_not_raise():
    for blob in (B.ie(61, b"\x06"), B.ie(192, b"\x01"), B.ext(36, b"\x00\x00"),
                 B.ext(106, b"\x01"), b"\xff", b"\x3d\x10\x01"):
        parse(blob, "5 GHz")


@pytest.mark.parametrize("primary,width,band,centre", [
    (36, 80, "5 GHz", 42), (48, 80, "5 GHz", 42), (100, 160, "5 GHz", 114),
    (149, 80, "5 GHz", 155), (161, 160, "5 GHz", 163), (40, 40, "5 GHz", 38),
    (37, 80, "6 GHz", 39), (37, 160, "6 GHz", 47), (37, 320, "6 GHz", 31),
    (6, 40, "2.4 GHz", 0), (6, 20, "2.4 GHz", 6),
])
def test_standard_center_channel(primary, width, band, centre):
    assert standard_center_channel(primary, width, band) == centre
