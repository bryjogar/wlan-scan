"""Estimate where APs are from the survey, and keep user corrections.

Each AP is a group of BSSIDs that look like one radio/AP
(``metrics.group_radios``). Its estimated position is the RSSI-weighted
centroid (weights in mW, so the strongest readings dominate) of the
``TOP_K`` points where it was heard strongest.

Markers the user placed or dragged are ``manual`` and never moved by a
re-estimate; their BSSIDs aren't estimated again. BSSIDs the user deleted
an estimate for go in ``Floor.ignored_bssids``.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Optional

from .metrics import group_radios
from .project import APMarker, Floor, SurveyProject, _new_id

TOP_K = 3
MIN_RSSI = -85   # weaker readings say little about where the AP is


def bssid_stats(project: SurveyProject, floor_id: str):
    """Per BSSID: {point id: mean RSSI} and the SSIDs seen."""
    per: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    ssids: dict[str, Counter] = defaultdict(Counter)
    ids = {p.id for p in project.points_on(floor_id)}
    for s in project.samples:
        if s.point_id not in ids:
            continue
        for r in s.readings:
            b = r.bssid.upper()
            per[b][s.point_id].append(r.rssi)
            if r.ssid:
                ssids[b][r.ssid] += 1
    means = {b: {pid: sum(v) / len(v) for pid, v in pts.items()} for b, pts in per.items()}
    return means, ssids


def ap_label(bssids: list[str], ssids: dict[str, Counter]) -> str:
    c = Counter()
    for b in bssids:
        c.update(ssids.get(b, {}))
    names = [n for n, _ in c.most_common(2)]
    base = " / ".join(names) if names else "Hidden"
    return f"{base} ({bssids[0][-8:]})"


def estimate(project: SurveyProject, floor: Floor, top_k: int = TOP_K,
             min_rssi: int = MIN_RSSI) -> list[APMarker]:
    """Fresh estimates for every AP heard on the floor (ignoring manual
    markers' and ignored BSSIDs)."""
    means, ssids = bssid_stats(project, floor.id)
    taken = {b.upper() for m in floor.ap_markers if m.manual for b in m.bssids}
    taken |= {b.upper() for b in floor.ignored_bssids}
    pts = {p.id: p for p in project.points_on(floor.id)}
    out = []
    for group in group_radios(b for b in means if b not in taken):
        best: dict[str, float] = {}
        for b in group:
            for pid, v in means[b].items():
                best[pid] = max(best.get(pid, -999.0), v)
        top = sorted(((v, pid) for pid, v in best.items() if v >= min_rssi),
                     reverse=True)[:top_k]
        if not top:
            continue
        ws = [10 ** (v / 10.0) for v, _ in top]
        tot = sum(ws)
        x = sum(w * pts[pid].x for w, (_v, pid) in zip(ws, top)) / tot
        y = sum(w * pts[pid].y for w, (_v, pid) in zip(ws, top)) / tot
        out.append(APMarker(_new_id("ap"), ap_label(group, ssids), sorted(group), x, y, False))
    return out


def refresh_estimates(project: SurveyProject, floor: Floor) -> list[APMarker]:
    """Replace the floor's automatic markers with new estimates; manual ones
    stay. An estimate reuses the id (and label, if the user renamed it) of
    the old automatic marker that shared the most BSSIDs."""
    manual = [m for m in floor.ap_markers if m.manual]
    old = [m for m in floor.ap_markers if not m.manual]
    new = estimate(project, floor)
    used: set[str] = set()
    for m in new:
        match: Optional[APMarker] = max(
            (o for o in old if o.id not in used and set(o.bssids) & set(m.bssids)),
            key=lambda o: len(set(o.bssids) & set(m.bssids)), default=None)
        if match is not None:
            used.add(match.id)
            m.id = match.id
            if match.label and not match.label.endswith(f"({match.bssids[0][-8:]})"):
                m.label = match.label     # user-renamed
    floor.ap_markers = manual + new
    return floor.ap_markers


def add_manual(floor: Floor, x: float, y: float, label: str, bssids: list[str]) -> APMarker:
    bss = sorted({b.upper() for b in bssids})
    # A BSSID belongs to one marker: take it off any others.
    for m in floor.ap_markers:
        m.bssids = [b for b in m.bssids if b not in bss]
    floor.ap_markers = [m for m in floor.ap_markers if m.bssids or m.manual]
    m = APMarker(_new_id("ap"), label or "AP", bss, float(x), float(y), True)
    floor.ap_markers.append(m)
    return m


def move(floor: Floor, ap_id: str, x: float, y: float) -> Optional[APMarker]:
    m = next((a for a in floor.ap_markers if a.id == ap_id), None)
    if m is not None:
        m.x, m.y, m.manual = float(x), float(y), True
    return m


def remove(floor: Floor, ap_id: str) -> None:
    """Delete a marker. An automatic one's BSSIDs are ignored from now on
    (otherwise the next estimate would bring it back)."""
    m = next((a for a in floor.ap_markers if a.id == ap_id), None)
    if m is None:
        return
    if not m.manual:
        floor.ignored_bssids = sorted(set(floor.ignored_bssids) | set(m.bssids))
    floor.ap_markers = [a for a in floor.ap_markers if a.id != ap_id]


def reset(floor: Floor, ap_id: str) -> None:
    """Return a manual marker to automatic placement."""
    m = next((a for a in floor.ap_markers if a.id == ap_id), None)
    if m is not None:
        m.manual = False
