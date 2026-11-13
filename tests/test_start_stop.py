"""R8: interruptible stop, button reflects real state, Auto-scan pauses."""

import threading
import time

import pytest

from conftest import need_qt

need_qt()

from wlan_scan.backends import BaseBackend  # noqa: E402
from wlan_scan.gui import main_window  # noqa: E402
from wlan_scan.models import ScanResult  # noqa: E402


class SlowBackend(BaseBackend):
    """Each scan takes `delay` seconds unless request_stop() is called."""
    name = "slow"
    delay = 0.4
    scans = 0

    def __init__(self, debug=None):
        super().__init__(debug)
        self._stop = threading.Event()

    def scan(self):
        SlowBackend.scans += 1
        if self._stop.wait(self.delay):
            raise RuntimeError("stopped")
        return ScanResult()

    def request_stop(self):
        self._stop.set()


@pytest.fixture
def slow(monkeypatch):
    SlowBackend.scans = 0
    SlowBackend.delay = 0.4
    monkeypatch.setattr(main_window, "candidate_backends", lambda replay=None: [SlowBackend])
    monkeypatch.setattr("wlan_scan.connection.connected_bssid", lambda: "")
    return SlowBackend


def test_stop_interrupts_long_scan(qtbot, slow):
    slow.delay = 10
    w = main_window.ScannerWorker()
    w.start()
    qtbot.waitUntil(lambda: slow.scans >= 1, timeout=2000)
    t = time.monotonic()
    w.stop()
    assert w.wait(2000)
    assert time.monotonic() - t < 1.0


def test_stop_interrupts_long_interval(qtbot, slow):
    slow.delay = 0.01
    w = main_window.ScannerWorker()
    w.set_interval(30)
    with qtbot.waitSignal(w.scan_complete, timeout=2000):
        w.start()
    t = time.monotonic()
    w.stop()
    assert w.wait(2000)
    assert time.monotonic() - t < 0.5


def _window(qtbot):
    win = main_window.MainWindow(check_updates=False)
    qtbot.addWidget(win)
    return win


def test_stop_then_scan_restarts(qtbot, slow):
    win = _window(qtbot)
    qtbot.waitUntil(lambda: win._scan_count >= 1, timeout=3000)
    assert win._scan_btn.text().endswith("Stop")
    win._toggle_scan()  # Stop while a scan is likely in progress
    assert not win._scan_btn.isEnabled() or not win._worker.isRunning()
    qtbot.waitUntil(lambda: not win._worker.isRunning(), timeout=3000)
    qtbot.waitUntil(lambda: win._scan_btn.text().endswith("Scan"), timeout=1000)
    assert win._scan_btn.isEnabled()
    before = win._scan_count
    win._toggle_scan()  # Scan again: v1 could leave it saying Stop with no scans
    assert win._scan_btn.text().endswith("Stop")
    qtbot.waitUntil(lambda: win._scan_count > before, timeout=3000)
    win.close()


def test_button_ignored_while_stopping(qtbot, slow):
    slow.delay = 0.6
    win = _window(qtbot)
    qtbot.waitUntil(lambda: slow.scans >= 1, timeout=2000)
    win._toggle_scan()
    win._toggle_scan()  # double click during stop must not start a second run
    qtbot.waitUntil(lambda: not win._worker.isRunning(), timeout=3000)
    qtbot.wait(100)
    assert not win._worker.isRunning()
    assert win._scan_btn.text().endswith("Scan")
    win.close()


def test_auto_scan_off_pauses_and_scan_once(qtbot, slow):
    slow.delay = 0.01
    win = _window(qtbot)
    win._interval_combo.setCurrentText("0.25s")
    qtbot.waitUntil(lambda: win._scan_count >= 1, timeout=3000)
    win._auto_scan_cb.setChecked(False)
    assert win._scan_btn.text().endswith("Scan once")
    qtbot.wait(400)
    paused_at = win._scan_count
    qtbot.wait(700)
    assert win._scan_count == paused_at          # no scans while paused
    win._toggle_scan()                          # one scan
    qtbot.waitUntil(lambda: win._scan_count == paused_at + 1, timeout=2000)
    qtbot.wait(600)
    assert win._scan_count == paused_at + 1
    win._auto_scan_cb.setChecked(True)          # resume continuous
    qtbot.waitUntil(lambda: win._scan_count >= paused_at + 3, timeout=3000)
    assert win._scan_btn.text().endswith("Stop")
    win.close()


def test_launch_with_auto_scan_off_takes_one_scan(qtbot, slow, monkeypatch):
    slow.delay = 0.01
    orig = main_window.MainWindow._setup_scanner

    def setup(self):
        self._auto_scan_cb.setChecked(False)
        orig(self)

    monkeypatch.setattr(main_window.MainWindow, "_setup_scanner", setup)
    win = _window(qtbot)
    qtbot.waitUntil(lambda: win._scan_count == 1, timeout=3000)
    qtbot.wait(500)
    assert win._scan_count == 1
    assert win._scan_btn.text().endswith("Scan once")
    win.close()
