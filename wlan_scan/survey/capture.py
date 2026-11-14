"""Survey capture logic, independent of Qt.

Point mode: click where you stand; the next ``scans_per_point`` scans are
stored at that point (``PointCapture``).

Walk mode: click a start point, walk, click the next point. Scans that
arrived between the two clicks are spread along the segment by arrival time
(``spread_along_segment``) and each becomes its own walk point
(``WalkSession``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .geometry import lerp
from .project import SurveyProject, _new_id


@dataclass
class PointCapture:
    """N scans at one point."""
    point_id: str
    needed: int
    got: int = 0
    skip: int = 0   # results to drop first (a scan already in flight at the click)

    @property
    def done(self) -> bool:
        return self.got >= self.needed

    def feed(self, project: SurveyProject, result) -> bool:
        """Store ``result`` at the point. Returns True if it was used."""
        if self.done:
            return False
        if self.skip > 0:
            self.skip -= 1
            return False
        project.add_sample(self.point_id, result)
        self.got += 1
        return True


def spread_along_segment(start, end, t0: float, t1: float, times: list[float]
                         ) -> list[tuple[float, float]]:
    """Positions for samples taken at ``times`` while walking ``start``→``end``
    between ``t0`` and ``t1`` (constant speed). Times outside are clamped to
    the ends; a zero-length interval puts everything at the midpoint."""
    span = t1 - t0
    out = []
    for t in times:
        frac = 0.5 if span <= 0 else min(1.0, max(0.0, (t - t0) / span))
        out.append(lerp(start, end, frac))
    return out


@dataclass
class WalkSession:
    floor_id: str
    start: Optional[tuple[float, float]] = None
    t_start: float = 0.0
    segment: str = ""
    pending: list = field(default_factory=list)   # [(arrival time, ScanResult)]

    @property
    def started(self) -> bool:
        return self.start is not None

    def feed(self, result, t: float) -> None:
        """Buffer a scan that arrived while walking (ignored before the first click)."""
        if self.started:
            self.pending.append((t, result))

    def click(self, project: SurveyProject, x: float, y: float, t: float) -> list:
        """Start or extend the walk. Returns the walk points created for the
        segment that just ended (empty on the first click)."""
        created = []
        if self.started:
            times = [pt for pt, _r in self.pending if self.t_start <= pt <= t]
            results = [r for pt, r in self.pending if self.t_start <= pt <= t]
            seg = _new_id("seg")
            for (px, py), at, res in zip(
                    spread_along_segment(self.start, (x, y), self.t_start, t, times),
                    times, results):
                when = datetime.fromtimestamp(at).isoformat(timespec="milliseconds")
                p = project.add_point(self.floor_id, px, py, mode="walk", segment=seg, t=when)
                project.add_sample(p.id, res, t=when)
                created.append(p)
            self.segment = seg
        self.start = (float(x), float(y))
        self.t_start = t
        self.pending = []
        return created

    def stop(self) -> None:
        self.start = None
        self.pending = []
