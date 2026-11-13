"""Record / replay round trip and the synthetic fixtures."""

import json

import pytest

from conftest import FIXTURES
from wlan_scan.models import BSSEntry, Network, ScanResult
from wlan_scan.recording import (ReplayBackend, ScanRecorder, load_recording,
                                 scan_to_dict, scan_from_dict)


def _result():
    b = BSSEntry(bssid="aa:bb:cc:dd:ee:ff", ssid="X", rssi=-55, channel=36,
                 band="5 GHz", ies_raw=b"\x00\x01X")
    n = Network(ssid="X", bss_list=[b])
    n.refresh_summary()
    return ScanResult(networks=[n], interface_name="wlan0")


def test_dict_round_trip():
    r = _result()
    back = scan_from_dict(json.loads(json.dumps(scan_to_dict(r))))
    b = back.networks[0].bss_list[0]
    assert b.bssid == "aa:bb:cc:dd:ee:ff"
    assert b.ies_raw == b"\x00\x01X"
    assert b.rssi == -55
    assert back.interface_name == "wlan0"


def test_unknown_and_missing_keys_tolerated():
    d = {"t": "2026-11-13T10:00:00", "bss": [{"bssid": "a", "ssid": "s", "rssi": -1,
                                              "channel": 1, "future_field": 3}]}
    b = scan_from_dict(d).networks[0].bss_list[0]
    assert b.band == "2.4 GHz"  # default


def test_recorder_writes_meta_then_scans(tmp_path):
    p = tmp_path / "rec.jsonl"
    rec = ScanRecorder(p)
    rec.write(_result())
    rec.write(_result())
    rec.close()
    meta, scans = load_recording(p)
    assert meta["format"] == 1 and meta["synthetic"] is False
    assert len(scans) == 2


def test_replay_loops(tmp_path):
    p = tmp_path / "rec.jsonl"
    rec = ScanRecorder(p)
    rec.write(_result())
    rec.close()
    rb = ReplayBackend(p, reparse=False)
    rb.open()
    assert rb.scan().networks[0].ssid == "X"
    assert rb.scan().networks[0].ssid == "X"  # looped


def test_replay_no_loop_stops(tmp_path):
    p = tmp_path / "rec.jsonl"
    rec = ScanRecorder(p)
    rec.write(_result())
    rec.close()
    rb = ReplayBackend(p, loop=False, reparse=False)
    rb.open()
    rb.scan()
    with pytest.raises(StopIteration):
        rb.scan()


def test_empty_recording_refuses(tmp_path):
    p = tmp_path / "empty.jsonl"
    p.write_text('{"_meta": {"format": 1}}\n')
    with pytest.raises(RuntimeError):
        ReplayBackend(p).open()


@pytest.mark.parametrize("name", ["synthetic_office.jsonl", "synthetic_walk.jsonl",
                                  "synthetic_no_ies.jsonl"])
def test_fixtures_load_and_are_marked_synthetic(name):
    meta, scans = load_recording(FIXTURES / name)
    assert meta["synthetic"] is True
    assert scans and all(s.networks for s in scans)


def test_replay_fixture_reparses_ies():
    rb = ReplayBackend(FIXTURES / "synthetic_office.jsonl")
    rb.open()
    r = rb.scan()
    bss = [b for n in r.networks for b in n.bss_list]
    assert len(bss) == 8
    assert all(b.security for b in bss)
