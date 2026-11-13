"""File-based logging for WLAN Scan.

Writes all scanner activity and errors to disk so they survive
GUI restarts and status-bar auto-clears.
"""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path


def log_dir() -> Path:
    """Get the log directory (platform-appropriate)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "WLANScan" / "logs"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "WLANScan"
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "WLANScan" / "logs"


def setup_logging() -> logging.Logger:
    """Configure and return a logger that writes to file + console."""
    log_dir_ = log_dir()
    log_dir_.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("wifiexplorer")
    logger.setLevel(logging.DEBUG)

    # Avoid duplicate handlers
    if logger.handlers:
        return logger

    # File handler (rotating by date in name)
    today = datetime.now().strftime("%Y-%m-%d")
    log_path = log_dir_ / f"wifiexplorer-{today}.log"
    fh = logging.FileHandler(str(log_path), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    ))
    logger.addHandler(fh)

    # Also log to stderr so it appears in console if launched from terminal
    sh = logging.StreamHandler()
    sh.setLevel(logging.WARNING)
    sh.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(sh)

    logger.info(f"Logging started — file: {log_path}")
    return logger


def get_logger() -> logging.Logger:
    """Get (or create) the WLAN Scan logger."""
    return setup_logging()
