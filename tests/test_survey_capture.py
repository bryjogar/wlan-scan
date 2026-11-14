"""F4: point capture, walk-mode spreading, per-point metric, short ping."""

import subprocess

import pytest

from conftest import FIXTURES
from wlan_scan.recording import load_recording
from wlan_scan.survey import capture as C
from wlan_scan.survey import metrics as M
from wlan_scan.survey import ping as PG
from wlan_scan.survey.project import SurveyProject


@pytest.fixture(scope="module")
def scans():
    _meta, results = load_recording(FIXTURES / "synthetic_office.jsonl")
    return results


def test_point_capture_takes_n_scans_and_skips_in_flight(scans):
    proj = SurveyProject()
    f = proj.add_floor("F1")
    p = proj.add_point(f.id, 10, 20)
    cap = C.PointCapture(p.id, needed=2, skip=1)
    assert not cap.feed(proj, scans[0])          # the scan already running at the click
    assert cap.feed(proj, scans[1]) and not cap.done
    assert cap.feed(proj, scans[2]) and cap.done
    assert not cap.feed(proj, scans[3])           # extra scans ignored
    assert len(proj.samples_for(p.id)) == 2
    assert proj.samples_for(p.id)[0].readings[0].bssid == scans[1].networks[0].bss_list[0].bssid


def test_spread_along_segment():
    pos = C.spread_along_segment((0, 0), (100, 0), 10.0, 20.0, [10, 12.5, 15, 20, 25, 5])
    assert [x for x, _ in pos] == pytest.approx([0, 25, 50, 100, 100, 0])
    assert C.spread_along_segment((0, 0), (10, 10), 5, 5, [5]) == [(5, 5)]


def test_walk_session_spreads_scans_by_time(scans):
    proj = SurveyProject()
    f = proj.add_floor("F1")
    w = C.WalkSession(f.id)
    w.feed(scans[0], 99.0)                        # before the first click: dropped
    assert w.click(proj, 0, 0, 100.0) == []
    for i, t in enumerate((101.0, 102.0, 103.0)):
        w.feed(scans[i], t)
    created = w.click(proj, 0, 40, 104.0)
    assert [c for p in created for c in (p.x, p.y)] == pytest.approx([0, 10, 0, 20, 0, 30])
    assert {p.mode for p in created} == {"walk"}
    assert len({p.segment for p in created}) == 1
    assert all(len(proj.samples_for(p.id)) == 1 for p in created)
    # The second segment starts where the first ended.
    w.feed(scans[3], 105.0)
    (q,) = w.click(proj, 40, 40, 106.0)
    assert (q.x, q.y) == pytest.approx((20, 40))
    assert q.segment != created[0].segment
    w.stop()
    assert not w.started


def test_best_rssi_and_colour(scans):
    proj = SurveyProject()
    f = proj.add_floor("F1")
    p = proj.add_point(f.id, 0, 0)
    for r in scans[:3]:
        proj.add_sample(p.id, r)
    samples = proj.samples_for(p.id)
    want = sum(max(b.rssi for n in r.networks for b in n.bss_list) for r in scans[:3]) / 3
    assert M.best_rssi(samples) == pytest.approx(want)
    assert M.best_rssi(samples, ssids={"no-such-ssid"}) is None
    six = M.best_rssi(samples, band="6 GHz")
    assert six is not None and six <= M.best_rssi(samples)
    assert M.rssi_color(-60) == "#22c55e"
    assert M.rssi_color(-68) == "#a3e635"
    assert M.rssi_color(-75) == "#f59e0b"
    assert M.rssi_color(-90) == "#ef4444"
    assert M.rssi_color(None) == "#6b7280"


LINUX_PING = """PING 192.168.1.1 (192.168.1.1) 56(84) bytes of data.
64 bytes from 192.168.1.1: icmp_seq=1 ttl=64 time=1.20 ms
64 bytes from 192.168.1.1: icmp_seq=2 ttl=64 time=2.80 ms
64 bytes from 192.168.1.1: icmp_seq=4 ttl=64 time=2.00 ms

--- 192.168.1.1 ping statistics ---
4 packets transmitted, 3 received, 25% packet loss, time 3004ms
rtt min/avg/max/mdev = 1.200/2.000/2.800/0.653 ms
"""

WINDOWS_PING = """Pinging 192.168.1.1 with 32 bytes of data:
Reply from 192.168.1.1: bytes=32 time=3ms TTL=64
Reply from 192.168.1.1: bytes=32 time<1ms TTL=64
Request timed out.
Reply from 192.168.1.1: bytes=32 time=5ms TTL=64
Reply from 192.168.1.1: bytes=32 time=4ms TTL=64

Ping statistics for 192.168.1.1:
    Packets: Sent = 5, Received = 4, Lost = 1 (20% loss),
Approximate round trip times in milli-seconds:
    Minimum = 0ms, Maximum = 5ms, Average = 3ms
"""


def test_parse_ping_linux_and_windows():
    r = PG.parse_ping_output(LINUX_PING, 4)
    assert r == {"sent": 4, "received": 3, "loss_pct": 25.0, "avg_ms": 2.0}
    w = PG.parse_ping_output(WINDOWS_PING, 5)
    assert w["received"] == 4 and w["loss_pct"] == 20.0
    assert w["avg_ms"] == pytest.approx((3 + 0.5 + 5 + 4) / 4, abs=0.01)
    assert PG.parse_ping_output("", 5) == {"sent": 5, "received": 0, "loss_pct": 100.0,
                                           "avg_ms": None}


def test_ping_command_and_summary():
    assert PG.ping_command("gw", 5, "win32")[:3] == ["ping", "-n", "5"]
    assert PG.ping_command("gw", 5, "linux")[:3] == ["ping", "-c", "5"]
    assert PG.ping_command("gw", 5, "darwin") == ["ping", "-c", "5", "gw"]

    def fake(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout=LINUX_PING)
    s = PG.ping_summary("192.168.1.1", 4, run=fake)
    assert s["target"] == "192.168.1.1" and s["received"] == 3

    def boom(cmd, **kw):
        raise FileNotFoundError("ping")
    e = PG.ping_summary("gw", 5, run=boom)
    assert e["loss_pct"] == 100.0 and "error" in e
