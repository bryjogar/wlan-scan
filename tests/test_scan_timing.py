"""R6: scan cycles end on the completion notification, not a fixed sleep."""

import threading
import time

from wlan_scan.scan_cycle import RateMeter, ScanCycle
from wlan_scan.scanner import (GUID, WLAN_NOTIFICATION_ACM_SCAN_COMPLETE,
                               WLAN_NOTIFICATION_ACM_SCAN_FAIL, WLAN_NOTIFICATION_DATA,
                               WLAN_NOTIFICATION_SOURCE_ACM, is_scan_done_notification)


def _cycle(notify_after=None, accept=True, fail=False, timeout=2.0):
    holder = {}

    def trigger():
        if accept and notify_after is not None:
            threading.Timer(notify_after, lambda: holder["c"].notify_complete(fail)).start()
        return accept

    c = ScanCycle(trigger, lambda: ["bss"], timeout=timeout)
    holder["c"] = c
    return c


def test_returns_when_notified():
    c = _cycle(notify_after=0.15)
    t = time.monotonic()
    assert c.run() == ["bss"]
    elapsed = time.monotonic() - t
    assert 0.1 < elapsed < 0.6      # v1 always took >= 2 s
    assert c.last.completed_by == "notification"


def test_scan_fail_notification():
    c = _cycle(notify_after=0.05, fail=True)
    c.run()
    assert c.last.completed_by == "failed"


def test_timeout_fallback():
    c = _cycle(notify_after=None, timeout=0.2)
    t = time.monotonic()
    c.run()
    assert 0.18 < time.monotonic() - t < 0.6
    assert c.last.completed_by == "timeout"


def test_rejected_scan_reads_cache_without_waiting():
    c = _cycle(accept=False, timeout=5)
    t = time.monotonic()
    c.run()
    assert time.monotonic() - t < 0.1
    assert c.last.completed_by == "cached" and not c.last.triggered


def test_request_stop_interrupts_wait():
    c = _cycle(notify_after=None, timeout=10)
    threading.Timer(0.1, c.request_stop).start()
    t = time.monotonic()
    c.run()
    assert time.monotonic() - t < 1.0
    assert c.last.completed_by == "stopped"


def test_stale_notification_does_not_end_next_cycle():
    c = _cycle(notify_after=None, timeout=0.2)
    c.notify_complete()          # arrives between cycles
    c.run()
    assert c.last.completed_by == "timeout"


def _notif(source, code, guid_bytes=b"\x01" * 16):
    d = WLAN_NOTIFICATION_DATA()
    d.NotificationSource = source
    d.NotificationCode = code
    d.InterfaceGuid = GUID.from_buffer_copy(guid_bytes)
    return d


def test_notification_filter():
    g = GUID.from_buffer_copy(b"\x01" * 16)
    other = GUID.from_buffer_copy(b"\x02" * 16)
    acm = WLAN_NOTIFICATION_SOURCE_ACM
    assert is_scan_done_notification(_notif(acm, WLAN_NOTIFICATION_ACM_SCAN_COMPLETE), g) is False
    assert is_scan_done_notification(_notif(acm, WLAN_NOTIFICATION_ACM_SCAN_FAIL), g) is True
    assert is_scan_done_notification(_notif(acm, 10), g) is None           # connection_complete
    assert is_scan_done_notification(_notif(0x10, 7), g) is None           # MSM source
    assert is_scan_done_notification(_notif(acm, 7), other) is None        # other interface


def test_rate_meter():
    t = [0.0]
    m = RateMeter(alpha=0.5, clock=lambda: t[0])
    assert m.tick() is None
    t[0] = 2.0
    assert m.tick() == 2.0
    t[0] = 6.0
    assert m.tick() == 3.0


def test_worker_interval_is_start_to_start(qtbot):
    from conftest import FIXTURES, need_qt
    need_qt()
    from wlan_scan.gui.main_window import ScannerWorker

    w = ScannerWorker(replay=str(FIXTURES / "synthetic_office.jsonl"))
    w.set_interval(0.25)
    stamps = []
    w.scan_complete.connect(lambda r: stamps.append(time.monotonic()))
    w.start()
    qtbot.waitUntil(lambda: len(stamps) >= 5, timeout=5000)
    w.stop()
    w.wait(3000)
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert all(0.2 < g < 0.45 for g in gaps), gaps
    assert all(isinstance(g, float) for g in gaps)
