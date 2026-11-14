"""Build the synthetic survey fixture tests/fixtures/synthetic_survey.wlansurvey.

    QT_QPA_PLATFORM=offscreen .venv/bin/python tests/fixtures/make_survey_fixture.py

SYNTHETIC: the floor plan is drawn here and every RSSI comes from a
log-distance path-loss model (plus a loss per wall crossed and seeded
noise) around three known AP positions. ``settings["_synthetic"]`` marks it,
and ``TRUE_APS`` below is the ground truth tests compare estimates with.

Plan: 1200 × 800 px, 10 px = 1 ft (a 120 × 80 ft office), calibrated.
"""

from __future__ import annotations

import math
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

OUT = HERE / "synthetic_survey.wlansurvey"
W, H = 1200, 800
PX_PER_FT = 10.0
T0 = datetime(2026, 11, 13, 9, 0, 0)

# Walls as plan-pixel segments (outer walls + rooms).
WALLS = [
    ((40, 40), (1160, 40)), ((1160, 40), (1160, 760)), ((1160, 760), (40, 760)),
    ((40, 760), (40, 40)),
    ((40, 300), (520, 300)), ((620, 300), (1160, 300)),          # corridor north wall
    ((40, 420), (420, 420)), ((520, 420), (1160, 420)),          # corridor south wall
    ((300, 40), (300, 300)), ((800, 40), (800, 300)),            # north offices
    ((420, 420), (420, 760)), ((820, 420), (820, 760)),          # south rooms
]
WALL_DB = 6.0

# Ground truth: (name, base MAC, (x, y) px, 2.4 GHz channel, 5 GHz channel)
TRUE_APS = [
    ("AP-West", "3C:84:6A:10:20:00", (170, 360), 1, 36),
    ("AP-Centre", "3C:84:6A:10:30:00", (640, 200), 6, 36),     # 5 GHz co-channel with West
    ("AP-East", "3C:84:6A:10:40:00", (1000, 560), 11, 149),
]
NEIGHBOR = ("Cafe-Free", "F0:9F:C2:77:00:10", (1400, 900), 6)  # outside the building

# Survey points: a grid over the floor, 3 scans each.
POINTS = [(x, y) for y in (110, 230, 360, 500, 640) for x in (110, 260, 410, 560, 710, 860, 1010)]
SCANS = 3


def _cross(a, b, c, d) -> bool:
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return (orient(a, b, c) * orient(a, b, d) < 0) and (orient(c, d, a) * orient(c, d, b) < 0)


def rssi(ap_xy, xy, band: str, rng: random.Random) -> int:
    d_m = max(1.0, math.dist(ap_xy, xy) / PX_PER_FT * 0.3048)
    ptx = 8.0 if band == "2.4 GHz" else 11.0
    pl1m = 40.0 if band == "2.4 GHz" else 46.5
    walls = sum(_cross(ap_xy, xy, a, b) for a, b in WALLS)
    loss = pl1m + 10 * 3.5 * math.log10(d_m) + walls * (WALL_DB * (1.6 if band == "5 GHz" else 1))
    return int(round(ptx - loss + rng.gauss(0, 2.0)))


def mac(base: str, last: int) -> str:
    parts = base.split(":")
    parts[-1] = f"{(int(parts[-1], 16) + last) & 0xFF:02X}"
    return ":".join(parts)


def radios():
    """(bssid, ssid, band, channel, width, ap xy)"""
    out = []
    for _name, base, xy, ch24, ch5 in TRUE_APS:
        out += [
            (mac(base, 0), "Corp", "2.4 GHz", ch24, 20, xy),
            (mac(base, 1), "Corp-Guest", "2.4 GHz", ch24, 20, xy),
            (mac(base, 8), "Corp", "5 GHz", ch5, 80, xy),
            (mac(base, 9), "Corp-Guest", "5 GHz", ch5, 80, xy),
        ]
    ssid, base, xy, ch = NEIGHBOR
    out.append((base, ssid, "2.4 GHz", ch, 20, xy))
    return out


def plan_png() -> bytes:
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen

    from wlan_scan.survey.plan_import import image_to_png

    _app = QGuiApplication.instance() or QGuiApplication([])
    img = QImage(W, H, QImage.Format.Format_RGB32)
    img.fill(QColor("white"))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor("#1f2937"), 8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.SquareCap))
    for a, b in WALLS:
        p.drawLine(*a, *b)
    f = QFont()
    f.setPixelSize(22)
    p.setFont(f)
    p.setPen(QColor("#6b7280"))
    for text, (x, y) in [("Office 1", (170, 170)), ("Open office", (550, 170)),
                         ("Office 2", (980, 170)), ("Corridor", (600, 360)),
                         ("Meeting", (230, 590)), ("Kitchen", (620, 590)),
                         ("Lab", (990, 650))]:
        p.drawText(QRectF(x - 100, y - 15, 200, 30), Qt.AlignmentFlag.AlignCenter, text)
    p.end()
    return image_to_png(img)


def build():
    from wlan_scan.survey import project as P

    rng = random.Random(20261113)
    proj = P.SurveyProject(name="Synthetic office", site="SYNTHETIC — generated test data",
                           created=T0.isoformat(), modified=T0.isoformat())
    proj.settings["_synthetic"] = True
    f = proj.add_floor("Ground floor", plan_png(), "png", (W, H))
    from wlan_scan.survey.geometry import make_scale
    f.scale = make_scale((40, 40), (1160, 40), 112.0, "ft")
    f.boundary = [(40.0, 40.0), (1160.0, 40.0), (1160.0, 760.0), (40.0, 760.0)]
    t = T0
    for i, xy in enumerate(POINTS):
        pt = proj.add_point(f.id, *xy, t=t.isoformat(timespec="seconds"))
        pt.id = f"pt-{i:04d}"           # stable ids: fixture diffs stay readable
        for _ in range(SCANS):
            t += timedelta(seconds=2)
            readings = []
            for bssid, ssid, band, ch, width, ap in radios():
                r = rssi(ap, xy, band, rng)
                if r < -92:
                    continue
                util = round(15 + 25 * rng.random(), 1) if ssid == "Corp" else 0.0
                readings.append(P.Reading(bssid, ssid, r, ch, width, band,
                                          "WPA2/WPA3" if ssid == "Corp" else "WPA2",
                                          util, rng.randint(2, 18) if util else 0))
            proj.samples.append(P.Sample(pt.id, t.isoformat(timespec="seconds"),
                                         TRUE_APS[0][1] if xy[0] < 400 else "", readings))
        t += timedelta(seconds=10)
    f.id = "floor-ground"
    f.image_file = "plans/floor-ground.png"
    for pt in proj.points:
        pt.floor_id = f.id
    proj.modified = t.isoformat(timespec="seconds")
    return proj


def main():
    from wlan_scan.survey import project as P

    proj = build()
    P.save(proj, OUT)
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes, {len(proj.points)} points, "
          f"{len(proj.samples)} scans)")


if __name__ == "__main__":
    main()
