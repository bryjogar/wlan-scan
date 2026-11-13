"""Plan geometry: distances and unit conversion."""

from __future__ import annotations

import math


def pixel_distance(a, b) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])
