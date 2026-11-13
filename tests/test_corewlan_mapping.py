"""R9: CoreWLAN mapping with fake CWNetwork objects (pyobjc is macOS-only)."""

from datetime import datetime

import pytest

from wlan_scan import corewlan_scanner as cw
from wlan_scan.airport_scanner import airport_removed

TS = datetime(2026, 11, 13, 10, 0)


class FakeChannel:
    def __init__(self, number, width_enum, band_enum):
        self._n, self._w, self._b = number, width_enum, band_enum

    def channelNumber(self):
        return self._n

    def channelWidth(self):
        return self._w

    def channelBand(self):
        return self._b


class FakeNetwork:
    def __init__(self, ssid, bssid, rssi=-50, channel=None, supports=(), beacon=100):
        self._ssid, self._bssid, self._rssi = ssid, bssid, rssi
        self._ch = channel or FakeChannel(36, 3, 2)
        self._supports = {cw.CW_SECURITY[k] for k in supports}
        self._beacon = beacon

    def ssid(self):
        return self._ssid

    def bssid(self):
        return self._bssid

    def rssiValue(self):
        return self._rssi

    def wlanChannel(self):
        return self._ch

    def supportsSecurity_(self, s):
        return s in self._supports

    def beaconInterval(self):
        return self._beacon

    def ibss(self):
        return False


def test_maps_fields_and_enums():
    net = FakeNetwork("Office", "a0:36:bc:10:00:02", -48, FakeChannel(36, 3, 2),
                      supports=("wpa2_personal", "wpa3_personal", "wpa3_transition"))
    e = cw.map_network(net, TS)
    assert e.bssid == "A0:36:BC:10:00:02"
    assert (e.channel, e.channel_width, e.band, e.center_channel) == (36, 80, "5 GHz", 42)
    assert e.center_frequency == 5180
    assert e.security == "WPA2/WPA3"
    assert e.rssi == -48 and e.beacon_period == 100
    assert e.vendor == "ASUSTek COMPUTER"
    assert e.max_rate == 0  # unknown on macOS, left blank


@pytest.mark.parametrize("width_enum,mhz", [(0, 20), (1, 20), (2, 40), (3, 80), (4, 160)])
def test_channel_width_enum(width_enum, mhz):
    # v1 mapped 0→20, 1→40 ... (off by one: 0 is kCWChannelWidthUnknown)
    e = cw.map_network(FakeNetwork("x", "00:11:22:33:44:55",
                                   channel=FakeChannel(36, width_enum, 2)), TS)
    assert e.channel_width == mhz


def test_6ghz_from_channel_band_not_number():
    e = cw.map_network(FakeNetwork("x", "00:11:22:33:44:55",
                                   channel=FakeChannel(37, 4, 3)), TS)
    assert e.band == "6 GHz" and e.center_channel == 47
    assert e.wifi_generation == "Wi-Fi 6E"


@pytest.mark.parametrize("supports,label", [
    ((), "Unknown"),
    (("none",), "Open"),
    (("wep",), "WEP"),
    (("wpa_personal",), "WPA"),
    (("wpa_personal_mixed", "wpa2_personal"), "WPA2"),
    (("wpa_personal_mixed",), "WPA/WPA2"),
    (("wpa3_personal",), "WPA3"),
    (("wpa2_enterprise",), "WPA2-Enterprise"),
    (("wpa3_enterprise", "wpa2_enterprise"), "WPA3-Enterprise"),
    (("owe",), "OWE (Enhanced Open)"),
])
def test_security_probe(supports, label):
    sec, _ = cw.security_from_network(FakeNetwork("x", "00:11:22:33:44:55", supports=supports))
    assert sec == label


def test_location_permission_detected():
    nets = [FakeNetwork(None, None), FakeNetwork(None, None)]
    with pytest.raises(cw.LocationPermissionRequired) as ex:
        cw.map_networks(nets, TS)
    assert "Location Services" in str(ex.value) and ex.value.count == 2


def test_partial_redaction_keeps_visible_networks():
    msgs = []
    nets = [FakeNetwork(None, None), FakeNetwork("ok", "00:11:22:33:44:55")]
    out = cw.map_networks(nets, TS, debug=msgs.append)
    assert [e.ssid for e in out] == ["ok"]
    assert any("without BSSID" in m for m in msgs)


def test_empty_scan_is_not_a_permission_error():
    assert cw.map_networks([], TS) == []


def test_band_fallback_without_channel_band():
    assert cw._band_from_channel(6) == "2.4 GHz"
    assert cw._band_from_channel(149) == "5 GHz"


@pytest.mark.parametrize("ver,removed", [
    ("13.6.1", False), ("14.3", False), ("14.4", True), ("14.4.1", True), ("15.1", True),
    ("26.0", True), ("", False),
])
def test_airport_removed(ver, removed):
    assert airport_removed(ver) is removed


def test_permission_shown_once(qtbot, monkeypatch):
    from conftest import FIXTURES, need_qt
    need_qt()
    from wlan_scan.gui import main_window

    shown = []
    monkeypatch.setattr(main_window.QMessageBox, "warning",
                        lambda *a, **k: shown.append(a[2]))
    win = main_window.MainWindow(replay=str(FIXTURES / "synthetic_office.jsonl"),
                                 check_updates=False)
    qtbot.addWidget(win)
    win._on_permission_needed(cw.LOCATION_HELP)
    win._on_permission_needed(cw.LOCATION_HELP)
    assert len(shown) == 1
    assert "Location Services" in win._statusbar.currentMessage()
    win.close()


def test_no_platform_warning_when_backend_available(qtbot):
    from conftest import FIXTURES, need_qt
    need_qt()
    from wlan_scan.gui import main_window

    win = main_window.MainWindow(replay=str(FIXTURES / "synthetic_office.jsonl"),
                                 check_updates=False)
    qtbot.addWidget(win)
    qtbot.waitUntil(lambda: win._scan_count >= 1, timeout=3000)
    assert "scanner" not in win._statusbar.currentMessage().lower()
    win.close()


def test_macos_log_dir(monkeypatch, tmp_path):
    from wlan_scan import logging_setup

    monkeypatch.setattr(logging_setup.sys, "platform", "darwin")
    monkeypatch.setattr(logging_setup.Path, "home", lambda: tmp_path)
    assert logging_setup.log_dir() == tmp_path / "Library" / "Logs" / "WLANScan"
