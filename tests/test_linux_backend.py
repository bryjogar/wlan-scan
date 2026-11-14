"""L1: Linux backends (iw, nmcli) against hand-written sample output.

tests/fixtures/linux/*.txt are SYNTHETIC: written in the formats iw 6.x
and nmcli print (labels checked against iw 6.17's strings), not captures.
"""

import subprocess

import pytest

from conftest import FIXTURES
from wlan_scan import backends as BK
from wlan_scan import linux_scanner as L

IW = (FIXTURES / "linux" / "iw_scan.txt").read_text()
NM = (FIXTURES / "linux" / "nmcli.txt").read_text()


@pytest.fixture(scope="module")
def iw():
    entries, connected = L.parse_iw_scan(IW)
    return {e.bssid: e for e in entries}, connected


def test_iw_basic_fields(iw):
    by, connected = iw
    assert len(by) == 7 and connected == "3C:84:6A:10:20:08"
    e = by["3C:84:6A:10:20:08"]
    assert (e.ssid, e.rssi, e.channel, e.band, e.center_frequency) == ("Corp", -52, 36, "5 GHz", 5180)
    assert (e.channel_width, e.center_channel) == (80, 42)
    assert (e.station_count, e.channel_utilization) == (7, 20.0)
    assert e.wifi_generation == "Wi-Fi 6" and e.phy_type == "802.11ax (HE)"
    assert e.estimated == ""


def test_iw_security_matches_ie_parser_labels(iw):
    by, _ = iw
    corp = by["3C:84:6A:10:20:08"]
    assert corp.security == "WPA2/WPA3" and corp.pmf == "Capable"
    assert corp.auth_algo == "PSK / SAE / FT-SAE" and corp.cipher == "CCMP-128 (AES)"
    cafe = by["F0:9F:C2:77:00:10"]
    assert cafe.security == "WPA/WPA2" and cafe.pmf == "Disabled"
    assert "TKIP" in cafe.cipher
    six = by["3C:84:6A:10:40:10"]
    assert six.security == "WPA3" and six.pmf == "Required"
    assert "SAE-EXT-KEY" in six.auth_algo and "GCMP-256" in six.cipher
    ent = by["B6:FB:E4:01:02:03"]
    assert ent.security == "WPA3-Enterprise"            # 802.1X-SHA256 + MFP required
    assert ent.auth_algo == "802.1X-SHA256"
    assert by["00:11:22:33:44:55"].security == "Open (OWE transition)"
    assert by["00:0C:41:AA:BB:CC"].security == "WEP"


def test_iw_width_ht40_on_24_and_6ghz(iw):
    by, _ = iw
    cafe = by["F0:9F:C2:77:00:10"]
    assert (cafe.channel, cafe.channel_width, cafe.center_channel) == (1, 40, 3)
    six = by["3C:84:6A:10:40:10"]
    assert (six.band, six.channel) == ("6 GHz", 33)
    assert six.channel_width == 160 and six.center_channel == 47
    assert six.wifi_generation == "Wi-Fi 7"
    unknown = by["3C:84:6A:10:40:20"]                    # 6 GHz, no operation info
    assert (unknown.channel, unknown.channel_width) == (1, 20)
    assert unknown.is_estimated("channel_width") and not unknown.is_estimated("rssi")
    assert unknown.wifi_generation == "Wi-Fi 6E"


def test_iw_ssid_escapes(iw):
    by, _ = iw
    assert by["B6:FB:E4:01:02:03"].ssid == "\x00\x00\x00\x00"
    assert by["00:11:22:33:44:55"].ssid == "Café Guest"


def test_iw_suite_matching_is_longest_first():
    assert L._suites("IEEE 802.1X/SHA-256 PSK", L._IW_AKM) == [5, 2]
    assert L._suites("IEEE 802.1X FT/IEEE 802.1X", L._IW_AKM) == [1, 3]
    assert L._suites("FT/PSK 00-0f-ac:24 Bogus SAE", L._IW_AKM) == [4, 24, 8]
    unknown = L._sec_dict({"Authentication suites": "PSK 00-0f-ac:99"})
    assert unknown["akms"] == ["PSK", "AKM 99"] and unknown["akm_types"] == {2, 99}


def test_iw_quality_signal_is_estimate():
    text = "BSS 00:11:22:33:44:55(on wlan0)\n\tfreq: 2412\n\tsignal: 70/100\n\tSSID: x\n"
    (e,), _ = L.parse_iw_scan(text)
    assert e.rssi == L.percent_to_dbm(70) and e.is_estimated("rssi")


def test_nmcli_parse(monkeypatch):
    entries, connected = L.parse_nmcli(NM)
    by = {e.bssid: e for e in entries}
    assert len(entries) == 7 and connected == "3C:84:6A:10:20:08"   # duplicate row dropped
    c = by["3C:84:6A:10:20:08"]
    assert (c.ssid, c.channel, c.band, c.rssi) == ("Corp", 36, "5 GHz", -52)
    assert c.is_estimated("rssi") and c.is_estimated("channel_width")
    assert by["00:11:22:33:44:55"].ssid == "Caf:e\\ Guest"
    assert by["3C:84:6A:10:40:10"].band == "6 GHz"
    assert [by[b].security for b in ("3C:84:6A:10:20:08", "F0:9F:C2:77:00:10",
                                     "3C:84:6A:10:40:10", "B6:FB:E4:01:02:03",
                                     "00:11:22:33:44:55", "00:0C:41:AA:BB:CC",
                                     "A0:36:BC:10:00:01")] == [
        "WPA2/WPA3", "WPA/WPA2", "WPA3", "WPA2-Enterprise", "OWE (Enhanced Open)", "WEP", "Open"]


def test_percent_mapping_and_nm_security():
    assert [L.percent_to_dbm(p) for p in (100, 80, 50, 0, 150, -5)] == [-40, -52, -70, -100, -40, -100]
    assert L.nm_security("WPA3 802.1X") == "WPA3-Enterprise"
    assert L.nm_security("WPA1 WPA2 802.1X") == "WPA/WPA2-Enterprise"
    assert L.nm_security("WPA1") == "WPA"
    assert L.nm_security("") == "Open"
    assert L.split_terse(r"a\:b:c\\d:") == ["a:b", "c\\d", ""]


class FakeRun:
    """Records commands; answers from a table of (prefix tuple → CompletedProcess)."""

    def __init__(self, table):
        self.table, self.calls = table, []

    def __call__(self, cmd, **kw):
        self.calls.append(tuple(cmd))
        for prefix, cp in self.table:
            if tuple(cmd[:len(prefix)]) == prefix:
                return cp(cmd) if callable(cp) else cp
        return subprocess.CompletedProcess(cmd, 1, "", "unknown command")


def ok(out=""):
    return lambda cmd: subprocess.CompletedProcess(cmd, 0, out, "")


IW_DEV = "phy#0\n\tInterface wlan0\n\t\tifindex 3\n\t\ttype managed\n"


def test_iw_backend_with_root_triggers_scans():
    run = FakeRun([(("iw", "dev", "wlan0", "scan", "dump"), ok("")),
                   (("iw", "dev", "wlan0", "scan"), ok(IW)), (("iw", "dev"), ok(IW_DEV))])
    b = BK.IwBackend(run=run)
    b.open()
    assert b.ifname == "wlan0"
    r = b.scan()
    assert sum(len(n.bss_list) for n in r.networks) == 7
    assert r.connected_bssid == "3C:84:6A:10:20:08" and b.can_trigger
    assert ("iw", "dev", "wlan0", "scan") in run.calls
    assert not any(c[0] == "nmcli" for c in run.calls)


def test_iw_backend_without_root_uses_nmcli_rescan_and_dump():
    denied = lambda cmd: subprocess.CompletedProcess(          # noqa: E731
        cmd, 255, "", "command failed: Operation not permitted (-1)")
    run = FakeRun([(("iw", "dev", "wlan0", "scan", "dump"), ok(IW)),
                   (("iw", "dev", "wlan0", "scan"), denied), (("iw", "dev"), ok(IW_DEV)),
                   (("nmcli",), ok(""))])
    msgs = []
    b = BK.IwBackend(debug=msgs.append, run=run)
    b.open()
    r1 = b.scan()
    assert not b.can_trigger and any("CAP_NET_ADMIN" in m for m in msgs)
    assert sum(len(n.bss_list) for n in r1.networks) == 7
    run.calls.clear()
    b.scan()                                              # no second trigger attempt
    assert ("iw", "dev", "wlan0", "scan") not in run.calls
    assert run.calls[0][:1] == ("nmcli",) and "--rescan" in run.calls[0]


def test_iw_backend_unavailable_without_interface(monkeypatch):
    monkeypatch.setattr(L, "wireless_interfaces", lambda run=None: [])
    run = FakeRun([(("iw", "dev"), ok("phy#0\n"))])
    with pytest.raises(BK.BackendUnavailable):
        BK.IwBackend(run=run).open()


def test_nmcli_backend_and_rescan_fallback():
    calls = []

    def lst(cmd):
        calls.append(cmd)
        if cmd[cmd.index("--rescan") + 1] == "yes":
            return subprocess.CompletedProcess(cmd, 1, "", "Error: Scanning not allowed")
        return subprocess.CompletedProcess(cmd, 0, NM, "")
    run = FakeRun([(("nmcli", "-t", "-f", "DEVICE,TYPE"),
                    ok("lo:loopback\nwlp2s0:wifi\neth0:ethernet\n")),
                   (("nmcli", "-t", "-e", "yes"), lst)])
    b = BK.NmcliBackend(run=run)
    b.open()
    assert b.ifname == "wlp2s0" and b.estimated
    r = b.scan()
    assert len(calls) == 2 and "no" in calls[1] and calls[1][-2:] == ["ifname", "wlp2s0"]
    assert r.connected_bssid == "3C:84:6A:10:20:08"


def test_nmcli_backend_needs_wifi_device():
    run = FakeRun([(("nmcli",), ok("eth0:ethernet\n"))])
    with pytest.raises(BK.BackendUnavailable):
        BK.NmcliBackend(run=run).open()


def test_linux_candidates_and_connected_bssid():
    assert BK.candidate_backends(platform="linux") == [BK.IwBackend, BK.NmcliBackend]
    run = FakeRun([(("iw", "dev", "wlan0", "link"),
                    ok("Connected to 3c:84:6a:10:20:08 (on wlan0)\n\tSSID: Corp\n")),
                   (("iw", "dev"), ok(IW_DEV))])
    assert L.linux_connected_bssid(run) == "3C:84:6A:10:20:08"
    nm = FakeRun([(("iw", "dev", "wlan0", "link"), ok("Not connected.\n")),
                  (("iw", "dev"), ok(IW_DEV)),
                  (("nmcli",), ok("no:AA\\:BB\\:CC\\:DD\\:EE\\:FF\nyes:11\\:22\\:33\\:44\\:55\\:66\n"))])
    assert L.linux_connected_bssid(nm) == "11:22:33:44:55:66"


def test_wireless_interfaces_sysfs_fallback(tmp_path):
    (tmp_path / "wlp1s0" / "wireless").mkdir(parents=True)
    (tmp_path / "eth0").mkdir()
    run = FakeRun([(("iw", "dev"), ok(""))])
    assert L.wireless_interfaces(run, tmp_path) == ["wlp1s0"]
