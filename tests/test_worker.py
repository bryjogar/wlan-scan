"""ScannerWorker drives a ReplayBackend headless."""

from conftest import FIXTURES, need_qt

need_qt()


def test_worker_replays_fixture(qtbot):
    from wlan_scan.gui.main_window import ScannerWorker

    w = ScannerWorker(replay=str(FIXTURES / "synthetic_office.jsonl"))
    w.set_interval(0.25)
    names = []
    w.backend_changed.connect(names.append)
    with qtbot.waitSignal(w.scan_complete, timeout=5000) as blocker:
        w.start()
    result = blocker.args[0]
    assert sum(len(n.bss_list) for n in result.networks) == 8
    w.stop()
    assert w.wait(3000)
    assert names == ["replay"]


def test_worker_records(qtbot, tmp_path):
    from wlan_scan.gui.main_window import ScannerWorker
    from wlan_scan.recording import load_recording

    out = tmp_path / "out.jsonl"
    w = ScannerWorker(replay=str(FIXTURES / "synthetic_office.jsonl"), record=str(out))
    w.set_interval(0.25)
    with qtbot.waitSignal(w.scan_complete, timeout=5000):
        w.start()
    w.stop()
    assert w.wait(3000)
    _meta, scans = load_recording(out)
    assert len(scans) >= 1


def test_main_window_with_replay(qtbot):
    from wlan_scan.gui.main_window import MainWindow

    win = MainWindow(replay=str(FIXTURES / "synthetic_office.jsonl"), check_updates=False)
    qtbot.addWidget(win)
    qtbot.waitUntil(lambda: win._scan_count >= 1, timeout=8000)
    assert win._status_backend.text() == "Backend: replay"
    assert "s/scan" in win._status_scan.text()
    win.close()
