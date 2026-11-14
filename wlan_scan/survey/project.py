"""Survey project model and the ``.wlansurvey`` file format.

A ``.wlansurvey`` file is a zip:

    project.json     schema, name, site, created/modified, settings,
                     floors (id, name, image file, scale, boundary, AP markers,
                     ignored BSSIDs),
                     points (id, floor, x/y in image pixels, mode, time, ping)
    samples.jsonl    one line per scan taken at a point:
                     {"point", "t", "connected", "bss": [{...}, ...]}
    plans/<floor>.<ext>   the floor plan images

Coordinates are image pixels of the floor plan; the floor's ``Scale``
converts to feet/metres. ``schema`` is bumped on incompatible changes;
``load()`` migrates older files and refuses newer ones.
"""

from __future__ import annotations

import io
import json
import os
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

SCHEMA_VERSION = 1
SUFFIX = ".wlansurvey"
AUTOSAVE_SUFFIX = ".autosave"

DEFAULT_SETTINGS = {
    "scans_per_point": 3,
    "unit": "ft",
    "ping_enabled": False,
    "ping_count": 5,
    "thresholds": [-67, -70, -80],   # voice / data / edge (dBm)
    "coverage_dbm": -67,              # "APs >= x dBm" metric
    "idw_power": 2.0,
    "idw_neighbors": 8,               # 0 = use every point for every cell
    "boundary_margin_ft": 10.0,
}


class UnsupportedSchema(ValueError):
    pass


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Scale:
    """Calibration: two image points ``distance`` units apart."""
    p1: tuple[float, float]
    p2: tuple[float, float]
    distance: float
    unit: str = "ft"  # "ft" or "m"

    @property
    def px_per_unit(self) -> float:
        from .geometry import pixel_distance
        return pixel_distance(self.p1, self.p2) / self.distance

    def to_dict(self) -> dict:
        return {"p1": list(self.p1), "p2": list(self.p2), "distance": self.distance,
                "unit": self.unit}

    @classmethod
    def from_dict(cls, d: dict) -> "Scale":
        return cls(tuple(d["p1"]), tuple(d["p2"]), float(d["distance"]), d.get("unit", "ft"))


@dataclass
class APMarker:
    id: str
    label: str
    bssids: list[str]
    x: float
    y: float
    manual: bool = False  # placed or moved by the user (not re-estimated)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "APMarker":
        return cls(d["id"], d.get("label", ""), list(d.get("bssids", [])),
                   float(d["x"]), float(d["y"]), bool(d.get("manual", False)))


@dataclass
class Floor:
    id: str
    name: str
    image_file: str = ""            # path inside the zip, e.g. "plans/floor-ab12.png"
    image_bytes: bytes = b""        # not in project.json; stored as its own zip entry
    image_size: tuple[int, int] = (0, 0)
    scale: Optional[Scale] = None
    boundary: Optional[list[tuple[float, float]]] = None  # None = auto (convex hull + margin)
    ap_markers: list[APMarker] = field(default_factory=list)
    ignored_bssids: list[str] = field(default_factory=list)  # deleted AP estimates

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "image": self.image_file,
            "image_size": list(self.image_size),
            "scale": self.scale.to_dict() if self.scale else None,
            "boundary": [list(p) for p in self.boundary] if self.boundary else None,
            "aps": [a.to_dict() for a in self.ap_markers],
            "ignored_bssids": list(self.ignored_bssids),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Floor":
        return cls(
            id=d["id"], name=d.get("name", ""), image_file=d.get("image", ""),
            image_size=tuple(d.get("image_size") or (0, 0)),
            scale=Scale.from_dict(d["scale"]) if d.get("scale") else None,
            boundary=[tuple(p) for p in d["boundary"]] if d.get("boundary") else None,
            ap_markers=[APMarker.from_dict(a) for a in d.get("aps", [])],
            ignored_bssids=list(d.get("ignored_bssids", [])),
        )


@dataclass
class Reading:
    """One BSS seen in one scan at a point."""
    bssid: str
    ssid: str
    rssi: int
    channel: int = 0
    width: int = 20
    band: str = ""
    security: str = ""
    utilization: float = 0.0
    stations: int = 0

    @classmethod
    def from_bss(cls, b) -> "Reading":
        return cls(b.bssid, b.ssid, int(b.rssi), int(b.channel), int(b.channel_width),
                   b.band, b.security, float(b.channel_utilization), int(b.station_count))


@dataclass
class Sample:
    point_id: str
    t: str
    connected: str = ""
    readings: list[Reading] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"point": self.point_id, "t": self.t, "connected": self.connected,
                "bss": [asdict(r) for r in self.readings]}

    @classmethod
    def from_dict(cls, d: dict) -> "Sample":
        return cls(d["point"], d.get("t", ""), d.get("connected", ""),
                   [Reading(**{k: v for k, v in r.items() if k in Reading.__dataclass_fields__})
                    for r in d.get("bss", [])])


@dataclass
class SurveyPoint:
    id: str
    floor_id: str
    x: float
    y: float
    t: str = ""
    mode: str = "point"            # "point" or "walk"
    segment: str = ""               # walk segment id
    ping: Optional[dict] = None     # {"target", "avg_ms", "loss_pct", "sent"}

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SurveyPoint":
        return cls(d["id"], d["floor_id"], float(d["x"]), float(d["y"]), d.get("t", ""),
                   d.get("mode", "point"), d.get("segment", ""), d.get("ping"))


@dataclass
class SurveyProject:
    name: str = "Untitled survey"
    site: str = ""
    created: str = field(default_factory=_now)
    modified: str = field(default_factory=_now)
    settings: dict = field(default_factory=lambda: dict(DEFAULT_SETTINGS))
    floors: list[Floor] = field(default_factory=list)
    points: list[SurveyPoint] = field(default_factory=list)
    samples: list[Sample] = field(default_factory=list)

    # ----- editing -----

    def add_floor(self, name: str, image_bytes: bytes = b"", ext: str = "png",
                  size: tuple[int, int] = (0, 0)) -> Floor:
        fid = _new_id("floor")
        f = Floor(id=fid, name=name, image_bytes=image_bytes,
                  image_file=f"plans/{fid}.{ext.lstrip('.').lower()}" if image_bytes else "",
                  image_size=size)
        self.floors.append(f)
        self.touch()
        return f

    def floor(self, floor_id: str) -> Optional[Floor]:
        return next((f for f in self.floors if f.id == floor_id), None)

    def add_point(self, floor_id: str, x: float, y: float, mode: str = "point",
                  segment: str = "", t: Optional[str] = None) -> SurveyPoint:
        p = SurveyPoint(_new_id("pt"), floor_id, float(x), float(y), t or _now(), mode, segment)
        self.points.append(p)
        self.touch()
        return p

    def point(self, point_id: str) -> Optional[SurveyPoint]:
        return next((p for p in self.points if p.id == point_id), None)

    def add_sample(self, point_id: str, scan_result, t: Optional[str] = None) -> Sample:
        readings = [Reading.from_bss(b) for n in scan_result.networks for b in n.bss_list]
        s = Sample(point_id, t or scan_result.timestamp.isoformat(timespec="milliseconds"),
                   (scan_result.connected_bssid or "").upper(), readings)
        self.samples.append(s)
        self.touch()
        return s

    def remove_point(self, point_id: str) -> None:
        self.points = [p for p in self.points if p.id != point_id]
        self.samples = [s for s in self.samples if s.point_id != point_id]
        self.touch()

    def clear_samples(self, point_id: str) -> None:
        """For retake: keep the point, drop its scans."""
        self.samples = [s for s in self.samples if s.point_id != point_id]
        p = self.point(point_id)
        if p:
            p.ping = None
        self.touch()

    def remove_floor(self, floor_id: str) -> None:
        ids = {p.id for p in self.points if p.floor_id == floor_id}
        self.floors = [f for f in self.floors if f.id != floor_id]
        self.points = [p for p in self.points if p.floor_id != floor_id]
        self.samples = [s for s in self.samples if s.point_id not in ids]
        self.touch()

    def points_on(self, floor_id: str) -> list[SurveyPoint]:
        return [p for p in self.points if p.floor_id == floor_id]

    def samples_for(self, point_id: str) -> list[Sample]:
        return [s for s in self.samples if s.point_id == point_id]

    def touch(self) -> None:
        self.modified = _now()

    # ----- (de)serialisation -----

    def to_json(self) -> dict:
        return {
            "schema": SCHEMA_VERSION,
            "app": "WLAN Scan",
            "name": self.name, "site": self.site,
            "created": self.created, "modified": self.modified,
            "settings": self.settings,
            "floors": [f.to_dict() for f in self.floors],
            "points": [p.to_dict() for p in self.points],
        }


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


def _migrate(doc: dict) -> dict:
    schema = int(doc.get("schema", 0))
    if schema > SCHEMA_VERSION:
        raise UnsupportedSchema(
            f"This survey was saved by a newer WLAN Scan (schema {schema}; "
            f"this version reads up to {SCHEMA_VERSION}).")
    # (no older schemas yet — add migrations here as schema N-1 -> N steps)
    return doc


def to_bytes(project: SurveyProject) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("project.json", json.dumps(project.to_json(), indent=1))
        z.writestr("samples.jsonl", "".join(
            json.dumps(s.to_dict(), separators=(",", ":")) + "\n" for s in project.samples))
        for f in project.floors:
            if f.image_bytes and f.image_file:
                # images are already compressed: store
                z.writestr(zipfile.ZipInfo(f.image_file, (1980, 1, 1, 0, 0, 0)),
                           f.image_bytes, compress_type=zipfile.ZIP_STORED)
    return buf.getvalue()


def from_bytes(data: bytes) -> SurveyProject:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        doc = _migrate(json.loads(z.read("project.json")))
        names = set(z.namelist())
        settings = dict(DEFAULT_SETTINGS)
        settings.update(doc.get("settings") or {})
        p = SurveyProject(
            name=doc.get("name", ""), site=doc.get("site", ""),
            created=doc.get("created", ""), modified=doc.get("modified", ""),
            settings=settings,
            floors=[Floor.from_dict(f) for f in doc.get("floors", [])],
            points=[SurveyPoint.from_dict(x) for x in doc.get("points", [])],
        )
        for f in p.floors:
            if f.image_file and f.image_file in names:
                f.image_bytes = z.read(f.image_file)
        if "samples.jsonl" in names:
            for line in z.read("samples.jsonl").decode("utf-8").splitlines():
                if line.strip():
                    p.samples.append(Sample.from_dict(json.loads(line)))
    return p


def save(project: SurveyProject, path: str | Path) -> None:
    """Write atomically (temp file + rename) so a crash never truncates a survey."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(to_bytes(project))
    os.replace(tmp, path)


def load(path: str | Path) -> SurveyProject:
    return from_bytes(Path(path).read_bytes())


def autosave_path(path: Optional[str | Path]) -> Path:
    """Sidecar next to the file, or in the user cache dir for an unsaved survey."""
    if path:
        p = Path(path)
        return p.with_name(p.name + AUTOSAVE_SUFFIX)
    from ..vendors import cache_dir
    return cache_dir() / ("untitled" + SUFFIX + AUTOSAVE_SUFFIX)


def autosave(project: SurveyProject, path: Optional[str | Path]) -> Path:
    target = autosave_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    save(project, target)
    return target


def recovery_candidate(path: Optional[str | Path]) -> Optional[Path]:
    """An autosave newer than its saved file (or any autosave for untitled)."""
    a = autosave_path(path)
    if not a.is_file():
        return None
    if path and Path(path).is_file() and Path(path).stat().st_mtime >= a.stat().st_mtime:
        return None
    return a


def discard_autosave(path: Optional[str | Path]) -> None:
    try:
        autosave_path(path).unlink()
    except OSError:
        pass


def iter_readings(project: SurveyProject, floor_id: str) -> Iterable[tuple[SurveyPoint, Sample]]:
    pts = {p.id: p for p in project.points_on(floor_id)}
    for s in project.samples:
        p = pts.get(s.point_id)
        if p is not None:
            yield p, s
