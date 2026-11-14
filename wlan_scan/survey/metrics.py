"""Per-point survey metrics and their colours."""

from __future__ import annotations

from typing import Iterable, Optional

from .project import Reading, Sample

DEFAULT_THRESHOLDS = (-67, -70, -80)   # voice / data / edge


def _matches(r: Reading, ssids: Optional[set[str]], band: Optional[str]) -> bool:
    if ssids and r.ssid not in ssids:
        return False
    if band and r.band != band:
        return False
    return True


def best_rssi(samples: Iterable[Sample], ssids: Optional[set[str]] = None,
              band: Optional[str] = None) -> Optional[float]:
    """Mean over the point's scans of the strongest matching RSSI.
    Scans that saw nothing matching don't count; None if none did."""
    vals = []
    for s in samples:
        rs = [r.rssi for r in s.readings if _matches(r, ssids, band)]
        if rs:
            vals.append(max(rs))
    return sum(vals) / len(vals) if vals else None


def rssi_color(value: Optional[float], thresholds=DEFAULT_THRESHOLDS) -> str:
    """Hex colour for an RSSI against voice/data/edge thresholds."""
    if value is None:
        return "#6b7280"
    voice, data, edge = thresholds
    if value >= voice:
        return "#22c55e"
    if value >= data:
        return "#a3e635"
    if value >= edge:
        return "#f59e0b"
    return "#ef4444"
