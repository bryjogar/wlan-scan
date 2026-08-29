"""File-based logging for WLAN Scan.

Writes all scanner activity and errors to disk so they survive
GUI restarts and status-bar auto-clears.
"""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path


def _log_dir() -> Path:
    """Get the log directory (platform-appropriate)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path.home() / ".local" / "share"
    return base / "WLANScan" / "logs"


def setup_logging() -> logging.Logger:
    """Configure and return a logger that writes to file + console."""
    log_dir = _log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("wifiexplorer")
    logger.setLevel(logging.DEBUG)

    # Avoid duplicate handlers
    if logger.handlers:
        return logger

    # File handler (rotating by date in name)
    today = datetime.now().strftime("%Y-%m-%d")
    log_path = log_dir / f"wifiexplorer-{today}.log"
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
