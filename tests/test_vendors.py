"""R5: one vendor lookup, local only, thread-safe."""

import threading
from datetime import date, timedelta

import pytest

from wlan_scan import vendors as V


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(V, "cache_dir", lambda: tmp_path / "cache")
    V.reload()
    yield
    V.reload()


def test_bundled_snapshot_known_vendors():
    assert V.lookup_vendor("00:00:0C:12:34:56") == "Cisco Systems"
    assert V.lookup_vendor("24-a4-3c-00-00-01") == "Ubiquiti"
    assert V.lookup_vendor("dca6.3211.2233") == "Raspberry Pi Trading"
    src, n, fetched = V.snapshot_info()
    assert src == V.BUNDLED and n > 30000 and fetched is not None


def test_randomized_and_unknown():
    assert V.lookup_vendor("A2:36:BC:10:00:01") == "Randomized"   # LAA bit set
    assert V.lookup_vendor("DA:A1:19:00:00:00") == "Randomized"
    assert V.is_locally_administered("02:00:00:00:00:00")
    assert not V.is_locally_administered("00:00:0C:00:00:00")
    assert V.lookup_vendor("") == ""
    assert V.lookup_vendor("zz:zz") == ""


def test_no_network_on_lookup(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("lookup touched the network")
    monkeypatch.setattr(V.urllib.request, "urlopen", boom)
    V.lookup_vendor("00:00:0C:00:00:01")
    V.lookup_vendor("12:34:56:78:9A:BC")
    V.lookup_vendor("FC:FF:FF:00:00:00")


def test_thread_safe_first_load():
    results, errors = [], []

    def worker():
        try:
            results.append(V.lookup_vendor("00:00:0C:00:00:01"))
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and set(results) == {"Cisco Systems"}


def test_newer_cache_wins(tmp_path):
    V.write_snapshot({"00000C": "Refreshed Cisco"}, V.cached_path(),
                     fetched=date.today() + timedelta(days=1))
    V.reload()
    assert V.lookup_vendor("00:00:0C:00:00:01") == "Refreshed Cisco"


def test_older_cache_ignored():
    V.write_snapshot({"00000C": "Stale"}, V.cached_path(), fetched=date(2000, 1, 1))
    V.reload()
    assert V.lookup_vendor("00:00:0C:00:00:01") == "Cisco Systems"


def test_background_refresh(monkeypatch):
    csv_text = "Registry,Assignment,Organization Name,Organization Address\n" + "".join(
        f"MA-L,{i:06X},Vendor {i} Inc.,Somewhere\n" for i in range(12000))
    monkeypatch.setattr(V, "download_ieee", lambda timeout=60: csv_text)
    done = []
    th = V.refresh_in_background(max_age_days=-1, on_done=done.append)
    th.join(5)
    assert done == [True]
    assert V.lookup_vendor("00:00:0C:00:00:01") == "Vendor 12"


def test_refresh_not_due():
    assert V.refresh_in_background(max_age_days=10_000) is None


def test_failed_refresh_keeps_snapshot(monkeypatch):
    def fail(timeout=60):
        raise OSError("offline")
    monkeypatch.setattr(V, "download_ieee", fail)
    done = []
    V.refresh_in_background(max_age_days=-1, on_done=done.append).join(5)
    assert done == [False]
    assert V.lookup_vendor("00:00:0C:00:00:01") == "Cisco Systems"


@pytest.mark.parametrize("raw,clean", [
    ("Cisco Systems, Inc", "Cisco Systems"),
    ("TP-LINK TECHNOLOGIES CO.,LTD.", "TP-LINK"),
    ("Ubiquiti Inc", "Ubiquiti"),
    ("  Apple,   Inc. ", "Apple"),
])
def test_tidy_name(raw, clean):
    assert V.tidy_name(raw) == clean
