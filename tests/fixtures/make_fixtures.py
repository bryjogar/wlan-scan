"""Regenerate the synthetic scan recordings in tests/fixtures/.

    .venv/bin/python tests/fixtures/make_fixtures.py

All three files are SYNTHETIC (marked ``"synthetic": true`` in their
``_meta`` line): IE blobs come from tests/ie_builder.py, RSSI values from a
seeded random walk. They are not over-the-air captures.
"""

from __future__ import annotations

import json
import math
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
sys.path.insert(0, str(HERE.parent))

import ie_builder as B  # noqa: E402
from wlan_scan.ie_parser import apply_ies  # noqa: E402
from wlan_scan.models import BSSEntry  # noqa: E402
from wlan_scan.recording import bss_to_dict  # noqa: E402

T0 = datetime(2026, 11, 13, 10, 0, 0)


def _freq(band: str, ch: int) -> int:
    if band == "2.4 GHz":
        return 2484 if ch == 14 else 2407 + 5 * ch
    if band == "5 GHz":
        return 5000 + 5 * ch
    return 5950 + 5 * ch


# (ssid, bssid, band, primary channel, phy, ies, (x, y) AP position in feet)
def _office_aps():
    return [
        ("Office", "a0:36:bc:10:00:01", "2.4 GHz", 1, "802.11ax (HE)",
         B.join(B.ssid("Office"), B.rates(), B.ds(1), B.ht_cap(2), B.ht_op(1),
                B.he_cap(2), B.he_op(), B.bss_load(7, 64),
                B.rsn(akms=(2, 8), mfpc=True)), (20, 15)),
        ("Office", "a0:36:bc:10:00:02", "5 GHz", 36, "802.11ax (HE)",
         B.join(B.ssid("Office"), B.rates(), B.ht_cap(2), B.ht_op(36, 1, 1),
                B.vht_cap(2), B.vht_op(1, 42), B.he_cap(2), B.he_op(),
                B.bss_load(4, 40), B.rsn(akms=(2, 8), mfpc=True)), (20, 15)),
        ("Office", "a0:36:bc:10:00:03", "6 GHz", 37, "802.11ax (HE)",
         B.join(B.ssid("Office"), B.rates(), B.he_cap(2, w160_5=True),
                B.he_op(six_ghz=(37, 3, 39, 47)), B.bss_load(1, 12),
                B.rsn(akms=(8,), mfpc=True, mfpr=True)), (20, 15)),
        ("Office", "a0:36:bc:20:00:01", "2.4 GHz", 6, "802.11ax (HE)",
         B.join(B.ssid("Office"), B.rates(), B.ds(6), B.ht_cap(2), B.ht_op(6),
                B.he_cap(2), B.he_op(), B.bss_load(12, 110),
                B.rsn(akms=(2, 8), mfpc=True)), (80, 40)),
        ("Office", "a0:36:bc:20:00:02", "5 GHz", 149, "802.11ax (HE)",
         B.join(B.ssid("Office"), B.rates(), B.ht_cap(2), B.ht_op(149, 1, 1),
                B.vht_cap(2), B.vht_op(1, 155), B.he_cap(2), B.he_op(),
                B.bss_load(9, 80), B.rsn(akms=(2, 8), mfpc=True)), (80, 40)),
        ("Office-Guest", "a2:36:bc:10:00:01", "5 GHz", 36, "802.11ax (HE)",
         B.join(B.ssid("Office-Guest"), B.rates(), B.ht_cap(2), B.ht_op(36, 1, 1),
                B.vht_cap(2), B.vht_op(1, 42), B.he_cap(2), B.he_op(),
                B.rsn(akms=(18,), mfpc=True, mfpr=True)),
         (20, 15)),
        ("Neighbor", "3c:84:6a:aa:bb:cc", "2.4 GHz", 11, "802.11n (HT)",
         B.join(B.ssid("Neighbor"), B.rates(), B.ds(11), B.ht_cap(2, width40=True),
                B.ht_op(11, 3, 1), B.wpa1(), B.rsn(pairwise=(2, 4))), (110, -10)),
        ("", "b6:fb:e4:01:02:03", "5 GHz", 100, "802.11ac (VHT)",
         B.join(B.ie(0, b""), B.rates(), B.ht_cap(2), B.ht_op(100, 1, 1),
                B.vht_cap(2), B.vht_op(1, 106), B.rsn(akms=(1,), mfpc=True)), (60, 70)),
    ]


def _rssi_at(ap_xy, xy, band, rnd) -> int:
    """Log-distance path loss with a little shadowing noise."""
    d = max(3.0, math.dist(ap_xy, xy))
    p0 = {"2.4 GHz": -30, "5 GHz": -36, "6 GHz": -40}[band]
    n = {"2.4 GHz": 2.6, "5 GHz": 3.0, "6 GHz": 3.2}[band]
    return int(round(p0 - 10 * n * math.log10(d / 3.0) + rnd.gauss(0, 2)))


def _scan(aps, xy, t, rnd, floor=-92):
    bss = []
    for ssid, bssid, band, ch, phy, ies, ap_xy in aps:
        rssi = _rssi_at(ap_xy, xy, band, rnd)
        if rssi < floor:
            continue
        e = BSSEntry(bssid=bssid, ssid=ssid, rssi=rssi, channel=ch, band=band,
                     center_frequency=_freq(band, ch), phy_type=phy, ies_raw=ies,
                     timestamp=t)
        apply_ies(e)
        bss.append(bss_to_dict(e))
    return {"t": t.isoformat(timespec="milliseconds"),
            "interface": "Synthetic AX211", "bss": bss}


def write(path: Path, meta: dict, scans: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"_meta": {"format": 1, "synthetic": True, **meta}}) + "\n")
        for s in scans:
            f.write(json.dumps(s, separators=(",", ":")) + "\n")


def main():
    rnd = random.Random(20261113)
    aps = _office_aps()

    # 1. Standing still near AP 1.
    scans = [_scan(aps, (30, 20), T0 + timedelta(seconds=3 * i), rnd) for i in range(6)]
    write(HERE / "synthetic_office.jsonl",
          {"description": "6 scans standing near AP 1 of a 2-AP office (8 BSSIDs, 2.4/5/6 GHz)"},
          scans)

    # 2. Walking from AP 1 to AP 2 and back, one scan per second.
    path = [(10 + 8 * i, 15 + 2 * i) for i in range(10)] + [(90 - 8 * i, 35 - 2 * i) for i in range(10)]
    scans = [_scan(aps, xy, T0 + timedelta(seconds=i), rnd) for i, xy in enumerate(path)]
    write(HERE / "synthetic_walk.jsonl",
          {"description": "20 scans, 1 s apart, walking AP1 -> AP2 -> AP1 (positions in ft)",
           "path_ft": path},
          scans)

    # 3. CoreWLAN-style: no IEs, coarse fields only.
    scans = []
    for i in range(3):
        bss = []
        for ssid, bssid, band, ch, *_rest in aps[:4]:
            bss.append(bss_to_dict(BSSEntry(
                bssid=bssid.upper(), ssid=ssid, rssi=-50 - 5 * i - rnd.randint(0, 4),
                channel=ch, band=band, channel_width=20, security="WPA2",
                timestamp=T0 + timedelta(seconds=3 * i))))
        scans.append({"t": (T0 + timedelta(seconds=3 * i)).isoformat(timespec="milliseconds"),
                      "interface": "en0", "bss": bss})
    write(HERE / "synthetic_no_ies.jsonl",
          {"description": "3 scans without raw IEs (macOS CoreWLAN-style)"}, scans)


if __name__ == "__main__":
    main()
