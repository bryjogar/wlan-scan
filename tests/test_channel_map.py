"""Channel map places blocks on the operating centre."""

from conftest import need_qt

need_qt()

from wlan_scan.gui.channel_map import block_center_mhz  # noqa: E402
from wlan_scan.models import BSSEntry  # noqa: E402


def test_block_centre_uses_center_channel():
    b = BSSEntry(bssid="x", ssid="s", rssi=-50, channel=36, channel_width=80,
                 center_channel=42, band="5 GHz", center_frequency=5180)
    assert block_center_mhz(b, "5 GHz") == 5210


def test_block_centre_falls_back_to_primary():
    b = BSSEntry(bssid="x", ssid="s", rssi=-50, channel=6, band="2.4 GHz")
    assert block_center_mhz(b, "2.4 GHz") == 2437
