"""R12: logger name, file name, 14-day pruning."""

import os
import time

from wlan_scan import logging_setup as L


def test_prune_removes_old_files_only(tmp_path):
    now = time.time()
    old_new = tmp_path / "wlanscan-2026-10-01.log"
    old_v1 = tmp_path / "wifiexplorer-2026-01-01.log"
    fresh = tmp_path / "wlanscan-2026-11-12.log"
    other = tmp_path / "notes.txt"
    for p in (old_new, old_v1, fresh, other):
        p.write_text("x")
    for p in (old_new, old_v1, other):
        os.utime(p, (now - 30 * 86400, now - 30 * 86400))
    assert L.prune_logs(tmp_path, keep_days=14, now=now) == 2
    assert fresh.exists() and other.exists()
    assert not old_new.exists() and not old_v1.exists()


def test_logger_name_and_file():
    log = L.setup_logging()
    assert log.name == "wlanscan"
    assert L.get_logger("pwsh").name == "wlanscan.pwsh"
    files = [h.baseFilename for h in log.handlers if hasattr(h, "baseFilename")]
    assert files and os.path.basename(files[0]).startswith("wlanscan-")
