"""Record scans to JSON lines and play them back.

File format (one JSON object per line, UTF-8):

    {"_meta": {"format": 1, "synthetic": true, "description": "..."}}   (optional, first line)
    {"t": "2026-11-13T10:00:00.123", "interface": "Intel AX211", "bss": [ {...}, ... ]}

Each ``bss`` object holds the ``BSSEntry`` fields by name; ``ies_raw`` is
hex and ``timestamp`` is ISO-8601. Unknown keys are ignored and missing
keys take the dataclass default, so old recordings keep loading as the
model grows.

``ReplayBackend`` plays a file back one scan per ``scan()`` call (the worker
supplies the interval) and loops at the end. When an entry has raw IEs and
``reparse`` is on, the IE-derived fields are recomputed with the current
parser, so recordings double as parser fixtures.
"""

from __future__ import annotations

import dataclasses
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import IO, Optional

from .backends import BaseBackend, DebugFn
from .models import BSSEntry, ScanResult, group_networks
from .vendors import lookup_vendor

FORMAT_VERSION = 1

_BSS_FIELDS = {f.name: f for f in dataclasses.fields(BSSEntry)}


# ---------------------------------------------------------------------------
# (de)serialisation
# ---------------------------------------------------------------------------


def bss_to_dict(bss: BSSEntry) -> dict:
    d = {}
    for name in _BSS_FIELDS:
        v = getattr(bss, name)
        if isinstance(v, (bytes, bytearray)):
            v = bytes(v).hex()
        elif isinstance(v, datetime):
            v = v.isoformat(timespec="milliseconds")
        d[name] = v
    return d


def bss_from_dict(d: dict) -> BSSEntry:
    kwargs = {}
    for name, f in _BSS_FIELDS.items():
        if name not in d:
            continue
        v = d[name]
        if name == "ies_raw":
            v = bytes.fromhex(v) if v else b""
        elif name == "timestamp":
            v = datetime.fromisoformat(v) if v else datetime.now()
        kwargs[name] = v
    return BSSEntry(**kwargs)


def scan_to_dict(result: ScanResult) -> dict:
    return {
        "t": result.timestamp.isoformat(timespec="milliseconds"),
        "interface": result.interface_name,
        "connected": result.connected_bssid,
        "bss": [bss_to_dict(b) for n in result.networks for b in n.bss_list],
    }


def scan_from_dict(d: dict) -> ScanResult:
    entries = [bss_from_dict(b) for b in d.get("bss", [])]
    ts = d.get("t")
    return ScanResult(
        timestamp=datetime.fromisoformat(ts) if ts else datetime.now(),
        networks=group_networks(entries),
        interface_name=d.get("interface", ""),
        connected_bssid=(d.get("connected") or "").upper(),
    )


def load_recording(path: str | Path) -> tuple[dict, list[ScanResult]]:
    """Return ``(meta, scans)`` from a recording file."""
    meta: dict = {}
    scans: list[ScanResult] = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{lineno}: bad JSON ({e})") from e
            if "_meta" in obj:
                meta = obj["_meta"]
                continue
            scans.append(scan_from_dict(obj))
    return meta, scans


class ScanRecorder:
    """Append every ScanResult to a JSON-lines file."""

    def __init__(self, path: str | Path, meta: Optional[dict] = None):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._f: Optional[IO[str]] = open(self.path, "a", encoding="utf-8")
        if self.path.stat().st_size == 0:
            m = {"format": FORMAT_VERSION, "synthetic": False,
                 "created": datetime.now().isoformat(timespec="seconds")}
            m.update(meta or {})
            self._f.write(json.dumps({"_meta": m}) + "\n")
        self.count = 0

    def write(self, result: ScanResult) -> None:
        with self._lock:
            if self._f is None:
                return
            self._f.write(json.dumps(scan_to_dict(result), separators=(",", ":")) + "\n")
            self._f.flush()
            self.count += 1

    def close(self) -> None:
        with self._lock:
            if self._f is not None:
                self._f.close()
                self._f = None


# ---------------------------------------------------------------------------
# Replay backend
# ---------------------------------------------------------------------------


class ReplayBackend(BaseBackend):
    """Plays a recording back, one scan per ``scan()`` call, looping."""

    name = "replay"

    def __init__(self, path: str | Path, debug: Optional[DebugFn] = None,
                 loop: bool = True, reparse: bool = True):
        super().__init__(debug)
        self.path = Path(path)
        self.loop = loop
        self.reparse = reparse
        self.meta: dict = {}
        self._scans: list[ScanResult] = []
        self._i = 0

    def open(self) -> None:
        self.meta, self._scans = load_recording(self.path)
        if not self._scans:
            raise RuntimeError(f"{self.path} has no scans")
        self._i = 0
        kind = "synthetic" if self.meta.get("synthetic") else "recorded"
        self.dbg(f"Replaying {len(self._scans)} {kind} scans from {self.path}")

    def __len__(self) -> int:
        return len(self._scans)

    def scan(self) -> ScanResult:
        if self._i >= len(self._scans):
            if not self.loop:
                raise StopIteration("end of recording")
            self._i = 0
        src = self._scans[self._i]
        self._i += 1
        now = datetime.now()
        entries = []
        for n in src.networks:
            for b in n.bss_list:
                b = dataclasses.replace(b, timestamp=now)
                if self.reparse and b.ies_raw:
                    _reparse(b)
                if not b.vendor:
                    b.vendor = lookup_vendor(b.bssid)
                entries.append(b)
        return ScanResult(
            timestamp=now,
            networks=group_networks(entries),
            interface_name=src.interface_name or f"Replay: {self.path.name}",
            interface_guid="replay",
            connected_bssid=src.connected_bssid,
        )


def _reparse(bss: BSSEntry) -> None:
    """Recompute IE-derived fields with the current parser (in place)."""
    from .ie_parser import apply_ies

    apply_ies(bss)
