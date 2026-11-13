"""F1: .wlansurvey round trip, schema check, autosave sidecar."""

import json
import os
import time
import zipfile

import pytest

from conftest import FIXTURES
from wlan_scan.recording import ReplayBackend
from wlan_scan.survey import project as P


def _scan():
    rb = ReplayBackend(FIXTURES / "synthetic_office.jsonl")
    rb.open()
    r = rb.scan()
    r.connected_bssid = "A0:36:BC:10:00:01"
    return r


def _project():
    pr = P.SurveyProject(name="HQ", site="Main St")
    f1 = pr.add_floor("Ground", b"\x89PNG fake", "png", (800, 600))
    f2 = pr.add_floor("First", b"\xff\xd8 fake jpg", "jpg", (1000, 700))
    f1.scale = P.Scale((0, 0), (300, 400), 50.0, "ft")
    f1.boundary = [(0, 0), (800, 0), (800, 600)]
    f1.ap_markers.append(P.APMarker("ap-1", "AP Lobby", ["A0:36:BC:10:00:01"], 10, 20, True))
    p = pr.add_point(f1.id, 100.5, 200.25)
    for _ in range(3):
        pr.add_sample(p.id, _scan())
    p.ping = {"target": "192.168.1.1", "avg_ms": 3.2, "loss_pct": 0, "sent": 5}
    w = pr.add_point(f2.id, 5, 5, mode="walk", segment="seg-1")
    pr.add_sample(w.id, _scan())
    return pr


def test_round_trip(tmp_path):
    pr = _project()
    path = tmp_path / "hq.wlansurvey"
    P.save(pr, path)
    back = P.load(path)
    assert back.name == "HQ" and back.site == "Main St"
    assert [f.name for f in back.floors] == ["Ground", "First"]
    g = back.floors[0]
    assert g.image_bytes == b"\x89PNG fake" and g.image_size == (800, 600)
    assert g.scale.px_per_unit == pytest.approx(10.0)
    assert g.boundary == [(0, 0), (800, 0), (800, 600)]
    assert g.ap_markers[0].manual and g.ap_markers[0].bssids == ["A0:36:BC:10:00:01"]
    assert back.floors[1].image_file.endswith(".jpg")
    assert len(back.points) == 2 and len(back.samples) == 4
    pt = back.points[0]
    assert (pt.x, pt.y) == (100.5, 200.25) and pt.ping["avg_ms"] == 3.2
    s = back.samples_for(pt.id)[0]
    assert s.connected == "A0:36:BC:10:00:01"
    assert len(s.readings) == 8
    r = next(r for r in s.readings if r.bssid == "A0:36:BC:10:00:02")
    assert (r.channel, r.width, r.band) == (36, 80, "5 GHz")
    assert back.points[1].mode == "walk" and back.points[1].segment == "seg-1"
    assert back.settings["scans_per_point"] == 3


def test_zip_layout(tmp_path):
    path = tmp_path / "hq.wlansurvey"
    P.save(_project(), path)
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        doc = json.loads(z.read("project.json"))
    assert {"project.json", "samples.jsonl"} <= names
    assert any(n.startswith("plans/") for n in names)
    assert doc["schema"] == P.SCHEMA_VERSION


def test_newer_schema_refused(tmp_path):
    pr = _project()
    data = P.to_bytes(pr)
    path = tmp_path / "future.wlansurvey"
    with zipfile.ZipFile(path, "w") as z:
        doc = pr.to_json()
        doc["schema"] = P.SCHEMA_VERSION + 1
        z.writestr("project.json", json.dumps(doc))
    with pytest.raises(P.UnsupportedSchema):
        P.load(path)
    assert P.from_bytes(data).name == "HQ"


def test_unknown_settings_and_missing_keys_tolerated(tmp_path):
    path = tmp_path / "old.wlansurvey"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("project.json", json.dumps({"schema": 1, "name": "x",
                                               "settings": {"future": 1}}))
    pr = P.load(path)
    assert pr.settings["scans_per_point"] == 3 and pr.settings["future"] == 1
    assert pr.samples == []


def test_remove_and_retake():
    pr = _project()
    pt = pr.points[0]
    pr.clear_samples(pt.id)
    assert pr.point(pt.id) is not None and pr.samples_for(pt.id) == []
    assert pr.point(pt.id).ping is None
    pr.remove_point(pt.id)
    assert pr.point(pt.id) is None
    pr.remove_floor(pr.floors[1].id)
    assert pr.points == [] and pr.samples == [] and len(pr.floors) == 1


def test_atomic_save_leaves_no_temp(tmp_path):
    path = tmp_path / "a.wlansurvey"
    P.save(_project(), path)
    assert [p.name for p in tmp_path.iterdir()] == ["a.wlansurvey"]


def test_autosave_sidecar_and_recovery(tmp_path):
    path = tmp_path / "hq.wlansurvey"
    pr = _project()
    P.save(pr, path)
    assert P.recovery_candidate(path) is None
    time.sleep(0.01)
    side = P.autosave(pr, path)
    assert side.name == "hq.wlansurvey.autosave"
    os.utime(side, (time.time() + 5, time.time() + 5))
    assert P.recovery_candidate(path) == side
    assert P.load(side).name == "HQ"
    P.discard_autosave(path)
    assert P.recovery_candidate(path) is None


def test_untitled_autosave_goes_to_cache(tmp_path, monkeypatch):
    from wlan_scan import vendors
    monkeypatch.setattr(vendors, "cache_dir", lambda: tmp_path / "cache")
    side = P.autosave(_project(), None)
    assert side.parent == tmp_path / "cache"
    assert P.recovery_candidate(None) == side
