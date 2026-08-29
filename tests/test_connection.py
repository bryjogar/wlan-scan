"""Tests for the connected-BSSID detection (platform-independent parsing)."""

from wlan_scan.connection import _windows_connected_bssid, _macos_connected_bssid

NETSH_SAMPLE = """\
There is 1 interface on the system:

    Name                   : Wi-Fi
    Description            : Intel(R) Wi-Fi 6E AX211 160MHz
    GUID                   : 12345678-1234-1234-1234-123456789abc
    Physical address       : aa:bb:cc:dd:ee:ff
    State                  : connected
    SSID                   : Office
    BSSID                  : 11:22:33:44:55:66
    Network type           : Infrastructure
    Radio type             : 802.11ax
    Authentication         : WPA3-Personal
"""

NETSH_DISCONNECTED = """\
There is 1 interface on the system:

    Name                   : Wi-Fi
    Description            : Intel(R) Wi-Fi 6E AX211 160MHz
    State                  : disconnected
"""

AIRPORT_SAMPLE = """\
     agrCtlRSSI: -45
     agrExtRSSI: 0
    agrCtlNoise: -95
     lastTxRate: 866
        maxRate: 1300
    lastAssocStatus: 0
    802.11 auth: open
      link auth: wpa2-psk
          BSSID: 22:33:44:55:66:77
           SSID: Office
            MCS: 8
"""


def test_windows_parses_connected_bssid(monkeypatch):
    import subprocess
    class FakeResult:
        returncode = 0
        stdout = NETSH_SAMPLE
        stderr = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeResult())
    assert _windows_connected_bssid() == "11:22:33:44:55:66"


def test_windows_disconnected_returns_none(monkeypatch):
    import subprocess
    class FakeResult:
        returncode = 0
        stdout = NETSH_DISCONNECTED
        stderr = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeResult())
    assert _windows_connected_bssid() is None


def test_windows_netsh_error_returns_none(monkeypatch):
    import subprocess
    def boom(*a, **k):
        raise FileNotFoundError
    monkeypatch.setattr(subprocess, "run", boom)
    assert _windows_connected_bssid() is None


def test_airport_parses_bssid(monkeypatch):
    import subprocess
    class FakeResult:
        returncode = 0
        stdout = AIRPORT_SAMPLE
        stderr = ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeResult())
    assert _macos_connected_bssid() == "22:33:44:55:66:77"
