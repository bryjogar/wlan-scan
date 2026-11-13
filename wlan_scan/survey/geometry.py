"""Plan geometry: distances, calibration, unit conversion, boundaries.

Everything on a plan is stored in image pixels. A floor's ``Scale``
(two points + a real distance) turns pixels into feet or metres. Until a
floor is calibrated, lengths are reported in pixels and labelled as such.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

FT_PER_M = 1 / 0.3048
UNITS = ("ft", "m")


def pixel_distance(a, b) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def convert(value: float, src: str, dst: str) -> float:
    """Length between 'ft' and 'm'."""
    if src == dst:
        return value
    if (src, dst) == ("m", "ft"):
        return value * FT_PER_M
    if (src, dst) == ("ft", "m"):
        return value / FT_PER_M
    raise ValueError(f"unknown units {src!r} -> {dst!r}")


def make_scale(p1, p2, distance: float, unit: str = "ft"):
    """Validate and build a Scale from two clicked points and a typed distance."""
    from .project import Scale

    if unit not in UNITS:
        raise ValueError("unit must be 'ft' or 'm'")
    if distance <= 0:
        raise ValueError("distance must be positive")
    if pixel_distance(p1, p2) < 2:
        raise ValueError("the two points are too close together")
    return Scale(tuple(map(float, p1)), tuple(map(float, p2)), float(distance), unit)


class PlanUnits:
    """Pixel ↔ real-world conversion for one floor (or pixels if uncalibrated)."""

    def __init__(self, scale=None, unit: Optional[str] = None):
        self.scale = scale
        self.unit = unit or (scale.unit if scale else "ft")

    @property
    def calibrated(self) -> bool:
        return self.scale is not None

    @property
    def label(self) -> str:
        return self.unit if self.calibrated else "px"

    def px_per_unit(self) -> float:
        """Pixels per display unit (1.0 when uncalibrated: units *are* pixels)."""
        if not self.scale:
            return 1.0
        per_scale_unit = self.scale.px_per_unit
        # pixels per 1 display unit = pixels per scale unit * scale units per display unit
        return per_scale_unit * convert(1.0, self.unit, self.scale.unit)

    def to_units(self, px: float) -> float:
        return px / self.px_per_unit()

    def to_px(self, units: float) -> float:
        return units * self.px_per_unit()

    def point_to_units(self, xy) -> tuple[float, float]:
        k = self.px_per_unit()
        return xy[0] / k, xy[1] / k

    def length_text(self, px: float) -> str:
        if not self.calibrated:
            return f"{px:.0f} px (not calibrated)"
        return f"{self.to_units(px):.1f} {self.unit}"

    def describe(self) -> str:
        if not self.calibrated:
            return "Not calibrated — distances in pixels"
        return f"Scale: 1 {self.unit} = {self.px_per_unit():.2f} px"


# ---------------------------------------------------------------------------
# Boundaries
# ---------------------------------------------------------------------------


def convex_hull(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Andrew's monotone chain; counter-clockwise, no repeated first point."""
    pts = sorted(set((float(x), float(y)) for x, y in points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def offset_polygon(poly: Sequence[tuple[float, float]], margin: float,
                   segments: int = 8) -> list[tuple[float, float]]:
    """Grow a convex polygon by ``margin`` (rounded corners).

    1-2 points become a circle / stadium around them.
    """
    pts = list(poly)
    if not pts or margin <= 0:
        return pts
    # Minkowski sum with a circle, then hull: simple and robust for convex input.
    ring = []
    for x, y in pts:
        for i in range(segments * 4):
            a = 2 * math.pi * i / (segments * 4)
            ring.append((x + margin * math.cos(a), y + margin * math.sin(a)))
    return convex_hull(ring)


def default_boundary(points: Sequence[tuple[float, float]], margin_px: float
                     ) -> list[tuple[float, float]]:
    """Convex hull of survey points grown by a margin."""
    return offset_polygon(convex_hull(points), margin_px)


def point_in_polygon(x: float, y: float, poly: Sequence[tuple[float, float]]) -> bool:
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def lerp(a, b, t: float) -> tuple[float, float]:
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
