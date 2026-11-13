"""R7: blocking subprocess calls stay off the GUI thread."""

import threading
import time

from conftest import need_qt
from wlan_scan.connection import (parse_linux_ip_route, parse_macos_route_get,
                                  parse_windows_route_print)

ROUTE_PRINT = """\
===========================================================================
IPv4 Route Table
===========================================================================
Active Routes:
Network Destination        Netmask          Gateway       Interface  Metric
          0.0.0.0          0.0.0.0      192.168.1.1    192.168.1.50     35
===========================================================================
"""

ROUTE_GET = """\
   route to: default
destination: default
       mask: default
    gateway: 10.0.0.1
  interface: en0
"""


def test_gateway_parsers():
    assert parse_windows_route_print(ROUTE_PRINT) == "192.168.1.1"
    assert parse_windows_route_print("no routes") is None
    assert parse_macos_route_get(ROUTE_GET) == "10.0.0.1"
    assert parse_linux_ip_route("default via 172.16.0.1 dev wlp2s0 proto dhcp\n") == "172.16.0.1"
    assert parse_linux_ip_route("") is None


def test_run_in_background_delivers_on_gui_thread(qtbot):
    need_qt()
    from wlan_scan.gui.background import run_in_background

    gui = threading.get_ident()
    seen = {}

    def slow():
        seen["worker"] = threading.get_ident()
        time.sleep(0.2)
        return "192.168.1.1"

    t = time.monotonic()
    run_in_background(slow, lambda r: seen.update(result=r, cb=threading.get_ident()))
    assert time.monotonic() - t < 0.1  # returned immediately
    qtbot.waitUntil(lambda: "result" in seen, timeout=3000)
    assert seen["result"] == "192.168.1.1"
    assert seen["worker"] != gui and seen["cb"] == gui


def test_worker_polls_connected_bssid_off_gui_thread(qtbot, monkeypatch):
    need_qt()
    from wlan_scan import connection
    from wlan_scan.backends import BaseBackend
    from wlan_scan.gui import main_window
    from wlan_scan.models import ScanResult

    gui = threading.get_ident()
    calls = []

    def fake_connected():
        calls.append(threading.get_ident())
        return "AA:BB:CC:DD:EE:FF"

    class Fake(BaseBackend):
        name = "fake"

        def scan(self):
            time.sleep(0.05)
            return ScanResult()

    monkeypatch.setattr(connection, "connected_bssid", fake_connected)
    monkeypatch.setattr(main_window, "candidate_backends", lambda replay=None: [Fake])
    w = main_window.ScannerWorker()
    w.set_interval(0.25)
    results = []
    w.scan_complete.connect(results.append)
    w.start()
    qtbot.waitUntil(lambda: any(r.connected_bssid for r in results), timeout=5000)
    w.stop()
    assert w.wait(3000)
    assert calls and gui not in calls


def test_ping_dialog_does_not_block(qtbot, monkeypatch):
    need_qt()
    from wlan_scan.gui import main_window
    from conftest import FIXTURES

    def slow_gateway():
        time.sleep(0.3)
        return "192.168.1.1"

    monkeypatch.setattr(main_window, "default_gateway", slow_gateway)
    win = main_window.MainWindow(replay=str(FIXTURES / "synthetic_office.jsonl"),
                                 check_updates=False)
    qtbot.addWidget(win)
    t = time.monotonic()
    win._on_ping_clicked()
    assert time.monotonic() - t < 0.2
    dlg = win._ping_dialogs[-1]
    qtbot.waitUntil(lambda: dlg._target_input.text() == "192.168.1.1", timeout=3000)
    dlg.close()
    win.close()
