"""R10: one CSV writer for GUI and CLI; honest success message."""

import csv
import io
import subprocess
import sys

from conftest import FIXTURES, TESTS
from wlan_scan.export import COLUMNS, write_scan_csv
from wlan_scan.recording import ReplayBackend


def _scan():
    rb = ReplayBackend(FIXTURES / "synthetic_office.jsonl")
    rb.open()
    r = rb.scan()
    r.connected_bssid = "A0:36:BC:10:00:02"
    return r


def test_writer_rows_and_columns():
    buf = io.StringIO()
    n = write_scan_csv(_scan(), buf)
    rows = list(csv.reader(io.StringIO(buf.getvalue())))
    assert rows[0] == COLUMNS
    assert n == len(rows) - 1 == 8
    by_bssid = {r[2]: dict(zip(COLUMNS, r)) for r in rows[1:]}
    five = by_bssid["A0:36:BC:10:00:02"]
    assert five["Channel"] == "36" and five["Center Channel"] == "42"
    assert five["Channel Width (MHz)"] == "80"
    assert five["Security"] == "WPA2/WPA3" and five["PMF"] == "Capable"
    assert five["Connected"] == "yes"
    assert five["Max Rate (Mbps)"] == "1201.0"
    assert by_bssid["A0:36:BC:10:00:01"]["Connected"] == ""
    hidden = next(r for r in rows[1:] if r[1] == "")
    assert hidden[COLUMNS.index("Vendor")] == "Randomized"


def test_writer_to_path(tmp_path):
    p = tmp_path / "out.csv"
    assert write_scan_csv(_scan(), p) == 8
    assert p.read_text(encoding="utf-8").startswith("Timestamp,SSID,BSSID")


def test_cli_export_uses_shared_writer(tmp_path):
    out = subprocess.run(
        [sys.executable, str(TESTS.parent / "main.py"), "--export",
         "--replay", str(FIXTURES / "synthetic_office.jsonl")],
        cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "Exported 8 BSS entries from one scan" in out.stdout
    files = list(tmp_path.glob("wifi_scan_*.csv"))
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8").splitlines()[0] == ",".join(COLUMNS)


def test_gui_message_counts_bss_not_scans(qtbot, monkeypatch, tmp_path):
    from conftest import need_qt
    need_qt()
    from wlan_scan.gui import main_window

    win = main_window.MainWindow(replay=str(FIXTURES / "synthetic_office.jsonl"),
                                 check_updates=False)
    qtbot.addWidget(win)
    qtbot.waitUntil(lambda: win._scan_count >= 2, timeout=5000)
    target = tmp_path / "gui.csv"
    msgs = []
    monkeypatch.setattr(main_window.QFileDialog, "getSaveFileName",
                        lambda *a, **k: (str(target), ""))
    monkeypatch.setattr(main_window.QMessageBox, "information",
                        lambda *a, **k: msgs.append(a[2]))
    win._export_csv()
    assert target.exists()
    assert "Exported 8 BSS entries from the latest scan" in msgs[0]
    assert "scans" not in msgs[0]
    win.close()
