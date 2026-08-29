"""MAC address vendor lookup using IEEE OUI database.

On first run, downloads the IEEE OUI list and caches it locally
as a SQLite database for fast lookups.
"""

import os
import re
import sqlite3
import urllib.request
from pathlib import Path

# Cache location
_CACHE_DIR = Path(os.path.expanduser("~")) / ".wlan_scan"
_CACHE_DB = _CACHE_DIR / "oui.db"
_IEEE_OUI_URL = "https://standards-oui.ieee.org/oui/oui.txt"

# In-memory cache for common OUIs (first 3 bytes of MAC)
_OUI_CACHE: dict[str, str] = {}

_db: sqlite3.Connection | None = None


def _ensure_db() -> sqlite3.Connection:
    """Get or create the OUI cache database."""
    global _db
    if _db is not None:
        return _db

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _db = sqlite3.connect(str(_CACHE_DB))
    _db.execute(
        "CREATE TABLE IF NOT EXISTS oui (oui TEXT PRIMARY KEY, vendor TEXT)"
    )
    _db.execute("CREATE INDEX IF NOT EXISTS idx_oui ON oui(oui)")
    _db.commit()
    return _db


def _download_oui_database() -> bool:
    """Download and import the IEEE OUI list. Returns True on success."""
    try:
        with urllib.request.urlopen(_IEEE_OUI_URL, timeout=30) as resp:
            data = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return False

    db = _ensure_db()
    # Parse: lines like "00-00-0C   (hex)\t\tCisco Systems, Inc"
    pattern = re.compile(
        r"^([0-9A-Fa-f]{2})-([0-9A-Fa-f]{2})-([0-9A-Fa-f]{2})\s+\(hex\)\s+(.+)$"
    )

    count = 0
    db.execute("BEGIN TRANSACTION")
    db.execute("DELETE FROM oui")  # fresh import

    for line in data.splitlines():
        m = pattern.match(line)
        if m:
            oui = f"{m.group(1)}{m.group(2)}{m.group(3)}".upper()
            vendor = m.group(4).strip()
            # Clean up: truncate long names
            if len(vendor) > 120:
                vendor = vendor[:117] + "..."
            db.execute(
                "INSERT OR REPLACE INTO oui (oui, vendor) VALUES (?, ?)",
                (oui, vendor),
            )
            count += 1

    db.execute("COMMIT")
    return count > 0


def _init_cache():
    """Initialize OUI cache — use existing DB or trigger download."""
    global _OUI_CACHE
    if _OUI_CACHE:
        return

    db = _ensure_db()
    row = db.execute("SELECT COUNT(*) FROM oui").fetchone()
    if row and row[0] > 0:
        # Load top 500 most common OUIs into memory
        rows = db.execute("SELECT oui, vendor FROM oui LIMIT 500").fetchall()
        _OUI_CACHE = {r[0]: r[1] for r in rows}
    else:
        # Need to download
        _download_oui_database()
        _init_cache()


def lookup_vendor(mac: str) -> str:
    """Look up vendor name for a MAC address (XX:XX:XX:XX:XX:XX)."""
    if not mac or len(mac) < 8:
        return ""

    # Extract OUI (first 3 bytes, no separators)
    oui = mac.replace(":", "").replace("-", "").replace(".", "").upper()[:6]

    # Check in-memory cache
    if oui in _OUI_CACHE:
        return _OUI_CACHE[oui]

    # Check database
    try:
        _init_cache()
    except Exception:
        return ""

    if oui in _OUI_CACHE:
        return _OUI_CACHE[oui]

    # Final DB lookup
    db = _ensure_db()
    row = db.execute("SELECT vendor FROM oui WHERE oui = ?", (oui,)).fetchone()
    if row:
        _OUI_CACHE[oui] = row[0]
        return row[0]

    return ""
