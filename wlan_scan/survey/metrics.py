"""Per-point survey metrics, filters and colour ramps.

Each metric is computed per scan at a point and averaged over the point's
scans (scans that saw nothing relevant don't count). A point with no
value is left out of the heatmap.

Metrics:
    best_rssi       strongest RSSI of any BSS passing the filter
    bssid_rssi      RSSI of one chosen BSSID
    coverage        number of APs (radios) at or above ``coverage_dbm``
    cochannel       other APs heard (≥ ``CCA_DBM``) on the strongest AP's
                    primary channel and band, any SSID
    utilization     channel utilization the strongest AP reports (BSS Load)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from .project import Reading, Sample

DEFAULT_THRESHOLDS = (-67, -70, -80)   # voice / data / edge
CCA_DBM = -82                           # weakest signal that still defers transmissions


@dataclass(frozen=True)
class MetricInfo:
    key: str
    label: str
    unit: str


METRICS = {
    "best_rssi": MetricInfo("best_rssi", "Signal: best RSSI", "dBm"),
    "bssid_rssi": MetricInfo("bssid_rssi", "Signal: one BSSID", "dBm"),
    "coverage": MetricInfo("coverage", "Coverage overlap (APs ≥ threshold)", "APs"),
    "cochannel": MetricInfo("cochannel", "Co-channel APs", "APs"),
    "utilization": MetricInfo("utilization", "Channel utilization", "%"),
}


@dataclass
class Filter:
    ssids: Optional[set[str]] = None   # None/empty = all
    band: Optional[str] = None         # "2.4 GHz" / "5 GHz" / "6 GHz" / None
    bssid: str = ""                    # for bssid_rssi

    def matches(self, r: Reading) -> bool:
        if self.ssids and r.ssid not in self.ssids:
            return False
        if self.band and r.band != self.band:
            return False
        return True


def _mac_bytes(bssid: str) -> Optional[bytes]:
    try:
        return bytes(int(x, 16) for x in bssid.replace("-", ":").split(":"))
    except ValueError:
        return None


def same_radio(a: str, b: str) -> bool:
    """Heuristic: two BSSIDs belong to one AP radio (virtual SSIDs).

    Vendors derive extra BSSIDs from the base MAC by changing the low
    nibble(s) of the last octet and/or setting the locally-administered
    bit of the first. Same octets 2–5, first octet equal ignoring the
    locally-administered bit, and last octets within 16 counts as one radio.
    """
    x, y = _mac_bytes(a), _mac_bytes(b)
    if not x or not y or len(x) != 6 or len(y) != 6:
        return a.upper() == b.upper()
    return ((x[0] | 0x02) == (y[0] | 0x02) and x[1:5] == y[1:5]
            and abs(x[5] - y[5]) < 16)


def group_radios(bssids: Iterable[str]) -> list[list[str]]:
    """Group BSSIDs into radios with ``same_radio`` (single-link)."""
    groups: list[list[str]] = []
    for b in sorted(set(bssids)):
        for g in groups:
            if any(same_radio(b, o) for o in g):
                g.append(b)
                break
        else:
            groups.append([b])
    return groups


def _scan_value(s: Sample, metric: str, flt: Filter, coverage_dbm: int) -> Optional[float]:
    rs = [r for r in s.readings if flt.matches(r)]
    if metric == "bssid_rssi":
        want = flt.bssid.upper()
        vals = [r.rssi for r in s.readings if r.bssid.upper() == want]
        return float(max(vals)) if vals else None
    if not rs:
        return None
    best = max(rs, key=lambda r: r.rssi)
    if metric == "best_rssi":
        return float(best.rssi)
    if metric == "coverage":
        strong = [r.bssid for r in rs if r.rssi >= coverage_dbm]
        return float(len(group_radios(strong)))
    if metric == "cochannel":
        others = [r.bssid for r in s.readings
                  if r.channel == best.channel and r.band == best.band
                  and r.rssi >= CCA_DBM and not same_radio(r.bssid, best.bssid)]
        return float(len(group_radios(others)))
    if metric == "utilization":
        if best.utilization <= 0 and best.stations <= 0:
            return None   # no BSS Load element: unknown, not 0 %
        return float(best.utilization)
    raise ValueError(f"unknown metric {metric!r}")


def point_metric(samples: Iterable[Sample], metric: str = "best_rssi",
                 flt: Optional[Filter] = None, coverage_dbm: int = -67) -> Optional[float]:
    flt = flt or Filter()
    vals = [v for v in (_scan_value(s, metric, flt, coverage_dbm) for s in samples)
            if v is not None]
    return sum(vals) / len(vals) if vals else None


def best_rssi(samples: Iterable[Sample], ssids: Optional[set[str]] = None,
              band: Optional[str] = None) -> Optional[float]:
    return point_metric(samples, "best_rssi", Filter(ssids, band))


# ---------------------------------------------------------------------------
# Colour ramps
# ---------------------------------------------------------------------------


def _rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


@dataclass
class ColorRamp:
    """Piecewise-linear colour scale. ``stops`` ascend by value; values
    outside are clamped. ``ticks`` are the labelled legend values."""
    stops: list[tuple[float, str]]
    ticks: list[tuple[float, str]] = field(default_factory=list)
    unit: str = ""

    @property
    def lo(self) -> float:
        return self.stops[0][0]

    @property
    def hi(self) -> float:
        return self.stops[-1][0]

    def rgb(self, v: float) -> tuple[int, int, int]:
        st = self.stops
        if v <= st[0][0]:
            return _rgb(st[0][1])
        for (v0, c0), (v1, c1) in zip(st, st[1:]):
            if v <= v1:
                t = (v - v0) / ((v1 - v0) or 1)
                a, b = _rgb(c0), _rgb(c1)
                return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))
        return _rgb(st[-1][1])

    def hex(self, v: Optional[float]) -> str:
        if v is None:
            return "#6b7280"
        return "#%02x%02x%02x" % self.rgb(v)

    def lut(self, n: int = 256):
        """(n, 3) uint8 table over [lo, hi] for vectorised colouring."""
        import numpy as np
        vals = np.linspace(self.lo, self.hi, n)
        return np.array([self.rgb(float(v)) for v in vals], dtype=np.uint8)


RED, ORANGE, YELLOW, LIME, GREEN, DKGREEN = (
    "#ef4444", "#f97316", "#facc15", "#a3e635", "#22c55e", "#15803d")


def ramp_for(metric: str, thresholds=DEFAULT_THRESHOLDS, coverage_dbm: int = -67) -> ColorRamp:
    if metric in ("best_rssi", "bssid_rssi"):
        voice, data, edge = sorted(thresholds, reverse=True)
        return ColorRamp(
            [(edge - 10, RED), (edge, ORANGE), (data, YELLOW), (voice, GREEN), (voice + 20, DKGREEN)],
            [(edge, f"{edge} edge"), (data, f"{data} data"), (voice, f"{voice} voice")], "dBm")
    if metric == "coverage":
        return ColorRamp([(0, RED), (1, ORANGE), (2, GREEN), (4, DKGREEN)],
                         [(0, "0"), (1, "1"), (2, "2"), (3, "3"), (4, "4+")],
                         f"APs ≥ {coverage_dbm} dBm")
    if metric == "cochannel":
        return ColorRamp([(0, GREEN), (1, YELLOW), (2, ORANGE), (4, RED)],
                         [(0, "0"), (1, "1"), (2, "2"), (3, "3"), (4, "4+")], "APs")
    if metric == "utilization":
        return ColorRamp([(0, GREEN), (30, YELLOW), (50, ORANGE), (80, RED)],
                         [(0, "0"), (30, "30"), (50, "50"), (80, "80+")], "%")
    raise ValueError(f"unknown metric {metric!r}")


def rssi_color(value: Optional[float], thresholds=DEFAULT_THRESHOLDS) -> str:
    """Hex colour for a point's RSSI (stepped at the voice/data/edge thresholds)."""
    if value is None:
        return "#6b7280"
    voice, data, edge = thresholds
    if value >= voice:
        return GREEN
    if value >= data:
        return LIME
    if value >= edge:
        return "#f59e0b"
    return RED
