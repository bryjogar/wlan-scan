"""Survey samples as CSV: one row per point × BSSID.

RSSI is summarised over the point's scans (avg/min/max, how many of the
point's scans saw the BSSID). X/Y are given in feet when the floor is
calibrated (blank otherwise) and always in plan pixels.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Optional

from .geometry import PlanUnits
from .project import SurveyProject

COLUMNS = [
    "Floor", "Point", "Mode", "Time", "X (ft)", "Y (ft)", "X (px)", "Y (px)",
    "SSID", "BSSID", "Band", "Channel", "Width (MHz)", "Security",
    "RSSI avg (dBm)", "RSSI min (dBm)", "RSSI max (dBm)", "Seen in scans", "Scans at point",
    "Utilization (%)", "Stations", "Connected", "Ping target", "Ping avg (ms)", "Ping loss (%)",
]


def rows(project: SurveyProject, floor_ids: Optional[Iterable[str]] = None):
    floors = [f for f in project.floors if floor_ids is None or f.id in set(floor_ids)]
    for f in floors:
        ft = PlanUnits(f.scale, "ft") if f.scale else None
        for n, p in enumerate(project.points_on(f.id), 1):
            samples = project.samples_for(p.id)
            per: dict[str, list] = defaultdict(list)
            for s in samples:
                for r in s.readings:
                    per[r.bssid].append(r)
            connected = {s.connected for s in samples if s.connected}
            ping = p.ping or {}
            xft, yft = ft.point_to_units((p.x, p.y)) if ft else ("", "")
            for bssid, rs in sorted(per.items(), key=lambda kv: -max(r.rssi for r in kv[1])):
                last = rs[-1]
                rssi = [r.rssi for r in rs]
                yield {
                    "Floor": f.name, "Point": n, "Mode": p.mode, "Time": p.t,
                    "X (ft)": f"{xft:.1f}" if ft else "", "Y (ft)": f"{yft:.1f}" if ft else "",
                    "X (px)": f"{p.x:.0f}", "Y (px)": f"{p.y:.0f}",
                    "SSID": last.ssid, "BSSID": bssid, "Band": last.band,
                    "Channel": last.channel, "Width (MHz)": last.width,
                    "Security": last.security,
                    "RSSI avg (dBm)": f"{sum(rssi) / len(rssi):.1f}",
                    "RSSI min (dBm)": min(rssi), "RSSI max (dBm)": max(rssi),
                    "Seen in scans": len(rs), "Scans at point": len(samples),
                    "Utilization (%)": f"{max(r.utilization for r in rs):.1f}",
                    "Stations": max(r.stations for r in rs),
                    "Connected": "yes" if bssid in connected else "",
                    "Ping target": ping.get("target", ""),
                    "Ping avg (ms)": "" if ping.get("avg_ms") is None else ping["avg_ms"],
                    "Ping loss (%)": "" if ping.get("loss_pct") is None else ping["loss_pct"],
                }


def write(project: SurveyProject, path: str | Path,
          floor_ids: Optional[Iterable[str]] = None) -> int:
    n = 0
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:   # BOM: Excel opens UTF-8
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for row in rows(project, floor_ids):
            w.writerow(row)
            n += 1
    return n
