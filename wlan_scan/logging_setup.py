"""File logging for WLAN Scan.

One file per day, ``wlanscan-YYYY-MM-DD.log``, in the platform log dir;
files older than ``KEEP_DAYS`` are deleted at start-up (including the
``wifiexplorer-*.log`` files v1 wrote and never pruned).

Modules log through ``get_logger()`` or ``logging.getLogger("wlanscan.<name>")``.
"""

import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

LOGGER_NAME = "wlanscan"
KEEP_DAYS = 14
_PATTERNS = ("wlanscan-*.log", "wifiexplorer-*.log")


def log_dir() -> Path:
    """Platform log directory."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "WLANScan" / "logs"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "WLANScan"
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "WLANScan" / "logs"


def prune_logs(directory: Path, keep_days: int = KEEP_DAYS, now: float | None = None) -> int:
    """Delete log files older than ``keep_days``. Returns how many were removed."""
    cutoff = (now if now is not None else time.time()) - keep_days * 86400
    removed = 0
    for pattern in _PATTERNS:
        for p in directory.glob(pattern):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
                    removed += 1
            except OSError:
                pass
    return removed


def setup_logging() -> logging.Logger:
    """Configure (once) and return the app logger: file (DEBUG) + stderr (WARNING)."""
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)

    directory = log_dir()
    log_path = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        prune_logs(directory)
        log_path = directory / f"{LOGGER_NAME}-{datetime.now():%Y-%m-%d}.log"
        fh = logging.FileHandler(str(log_path), encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"))
        logger.addHandler(fh)
    except OSError:
        pass  # read-only home etc.: keep going with stderr only

    sh = logging.StreamHandler()
    sh.setLevel(logging.WARNING)
    sh.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(sh)

    logger.info(f"Logging started — file: {log_path}")
    return logger


def get_logger(name: str = "") -> logging.Logger:
    """The app logger, or a child (``wlanscan.<name>``)."""
    setup_logging()
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)
