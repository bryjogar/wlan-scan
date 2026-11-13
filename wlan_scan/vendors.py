"""MAC address → vendor name. One module, no network on the lookup path.

Data: the IEEE MA-L registry snapshot bundled at ``resources/oui.txt.gz``
(regenerated at build time by ``tools/build_oui.py``). Optionally,
``refresh_in_background()`` fetches a newer copy into the user cache dir
on a daemon thread; lookups use whichever copy was fetched more recently.
Nothing here ever sends a MAC address anywhere.

Locally administered addresses (bit 1 of the first octet set) are in no
registry: randomized client MACs and AP virtual BSSIDs. They are reported
as "Randomized".

Snapshot format: gzip'd UTF-8, header ``# source <url> fetched <YYYY-MM-DD>
entries <n>``, then one ``XXXXXX<TAB>Vendor`` line per assignment.
"""

from __future__ import annotations

import csv
import gzip
import io
import os
import re
import sys
import threading
import urllib.request
from datetime import date
from pathlib import Path
from typing import Callable, Optional

RANDOMIZED = "Randomized"
IEEE_URL = "https://standards-oui.ieee.org/oui/oui.csv"
BUNDLED = Path(__file__).resolve().parent / "resources" / "oui.txt.gz"

_lock = threading.Lock()
_table: Optional[dict[str, str]] = None
_source: Optional[Path] = None


# ---------------------------------------------------------------------------
# Snapshot files
# ---------------------------------------------------------------------------


def cache_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "WLANScan"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "WLANScan"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "WLANScan"


def cached_path() -> Path:
    return cache_dir() / "oui.txt.gz"


def snapshot_date(path: Path) -> Optional[date]:
    """Fetch date from a snapshot's header line (None if unreadable)."""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            head = f.readline()
        return date.fromisoformat(head.split(" fetched ", 1)[1].split()[0])
    except Exception:
        return None


def _newest_snapshot() -> Optional[Path]:
    best, best_date = None, None
    for p in (BUNDLED, cached_path()):
        d = snapshot_date(p) if p.is_file() else None
        if d is not None and (best_date is None or d >= best_date):  # cache wins ties
            best, best_date = p, d
    return best


def load_snapshot(path: Path) -> dict[str, str]:
    table: dict[str, str] = {}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            oui, _, name = line.rstrip("\n").partition("\t")
            if len(oui) == 6 and name:
                table[oui] = name
    return table


_SUFFIXES = re.compile(
    r"[\s,.]+(inc|incorporated|corp|corporation|co|company|ltd|limited|llc|"
    r"gmbh|ag|s\.?a|s\.?p\.?a|b\.?v|oy|ab|a/s|plc|pte|pty|kg|sarl|s\.?r\.?l|"
    r"technologies|technology)\.?$",
    re.IGNORECASE,
)


def tidy_name(name: str) -> str:
    """'Cisco Systems, Inc' → 'Cisco Systems'; collapses whitespace."""
    name = " ".join(name.split()).strip(" ,.")
    for _ in range(3):
        new = _SUFFIXES.sub("", name).strip(" ,.")
        if new == name or len(new) < 2:
            break
        name = new
    return name


def parse_ieee_csv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in csv.DictReader(io.StringIO(text)):
        if row.get("Registry") != "MA-L":
            continue
        oui = (row.get("Assignment") or "").strip().upper()
        if re.fullmatch(r"[0-9A-F]{6}", oui):
            out[oui] = tidy_name(row.get("Organization Name") or "")
    return out


def write_snapshot(entries: dict[str, str], path: Path, source: str = IEEE_URL,
                   fetched: Optional[date] = None) -> None:
    fetched = fetched or date.today()
    lines = [f"# source {source} fetched {fetched.isoformat()} entries {len(entries)}"]
    lines += [f"{k}\t{v}" for k, v in sorted(entries.items())]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with gzip.GzipFile(tmp, "wb", mtime=0) as f:  # mtime=0: reproducible bytes
        f.write(("\n".join(lines) + "\n").encode("utf-8"))
    os.replace(tmp, path)


def download_ieee(timeout: float = 60) -> str:
    req = urllib.request.Request(IEEE_URL, headers={"User-Agent": "wlan-scan"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def _ensure_loaded() -> dict[str, str]:
    global _table, _source
    t = _table
    if t is not None:
        return t
    with _lock:
        if _table is None:
            table: dict[str, str] = {}
            src = _newest_snapshot()
            for path in (src, BUNDLED):
                if path is None:
                    continue
                try:
                    table = load_snapshot(path)
                    _source = path
                    break
                except Exception:
                    continue
            _table = table
        return _table


def reload() -> None:
    """Forget the loaded table; the next lookup reads the newest snapshot."""
    global _table
    with _lock:
        _table = None


def normalize_mac(mac: str) -> str:
    """'aa-bb-cc-dd-ee-ff' / 'aabb.ccdd.eeff' / 'aa:bb:..' → 'AABBCCDDEEFF' ('' if bad)."""
    if not mac:
        return ""
    h = "".join(ch for ch in mac if ch not in ":-. ").upper()
    if len(h) < 6 or any(c not in "0123456789ABCDEF" for c in h):
        return ""
    return h


def is_locally_administered(mac: str) -> bool:
    h = normalize_mac(mac)
    return bool(h) and bool(int(h[:2], 16) & 0x02)


def lookup_vendor(mac: str) -> str:
    """Vendor for a MAC/BSSID; "Randomized" if locally administered; "" if unknown.

    Thread-safe and local-only (the first call loads ~40k entries).
    """
    h = normalize_mac(mac)
    if not h:
        return ""
    if int(h[:2], 16) & 0x02:
        return RANDOMIZED
    return _ensure_loaded().get(h[:6], "")


def snapshot_info() -> tuple[Optional[Path], int, Optional[date]]:
    """(file, entry count, fetch date) of the loaded table."""
    t = _ensure_loaded()
    return _source, len(t), snapshot_date(_source) if _source else None


def refresh_in_background(max_age_days: int = 90,
                          on_done: Optional[Callable[[bool], None]] = None
                          ) -> Optional[threading.Thread]:
    """Fetch the IEEE list into the cache dir when the newest snapshot is
    older than ``max_age_days``. Daemon thread; never touches the scan path.
    Returns the thread, or None when no refresh is due.
    """
    newest = _newest_snapshot()
    d = snapshot_date(newest) if newest else None
    if d is not None and (date.today() - d).days < max_age_days:
        return None

    def run():
        ok = False
        try:
            entries = parse_ieee_csv(download_ieee(timeout=30))
            if len(entries) > 10000:
                write_snapshot(entries, cached_path())
                reload()
                ok = True
        except Exception:
            ok = False
        if on_done:
            on_done(ok)

    th = threading.Thread(target=run, name="oui-refresh", daemon=True)
    th.start()
    return th
