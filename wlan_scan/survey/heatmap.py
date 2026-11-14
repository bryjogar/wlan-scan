"""Heatmap: inverse-distance weighting on a grid clipped to a boundary.

All coordinates are floor-plan image pixels. The grid covers the image;
cells outside the boundary are NaN (drawn transparent).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from . import geometry as G
from .metrics import ColorRamp, Filter, point_metric, ramp_for

MAX_CELLS = 40_000      # grid resolution cap (≈200 × 200)
CHUNK = 4096            # cells per IDW batch (bounds memory: CHUNK × points)


@dataclass
class Grid:
    values: np.ndarray          # (rows, cols) float, NaN outside the boundary
    cell: float                 # cell size in image pixels
    width: int                  # image size the grid covers
    height: int

    @property
    def shape(self) -> tuple[int, int]:
        return self.values.shape

    def value_at(self, x: float, y: float) -> float:
        r = min(self.shape[0] - 1, max(0, int(y / self.cell)))
        c = min(self.shape[1] - 1, max(0, int(x / self.cell)))
        return float(self.values[r, c])


def cell_size(width: int, height: int, max_cells: int = MAX_CELLS) -> float:
    return max(1.0, float(np.sqrt(width * height / max_cells)))


def polygon_mask(xs: np.ndarray, ys: np.ndarray, poly: Sequence[tuple[float, float]]
                 ) -> np.ndarray:
    """Even-odd point-in-polygon for arrays of points (same rule as
    ``geometry.point_in_polygon``)."""
    inside = np.zeros(xs.shape, dtype=bool)
    n = len(poly)
    if n < 3:
        return inside
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        crosses = (yi > ys) != (yj > ys)
        dy = (yj - yi) or 1e-12
        xint = (xj - xi) * (ys - yi) / dy + xi
        inside ^= crosses & (xs < xint)
        j = i
    return inside


def _centres(n: int, cell: float, size: float) -> np.ndarray:
    """Cell centres; a partial last cell is centred on the part inside the image."""
    c = (np.arange(n) + 0.5) * cell
    c[-1] = ((n - 1) * cell + size) / 2
    return c


def idw_grid(xy: Sequence[tuple[float, float]], values: Sequence[float],
             width: int, height: int, power: float = 2.0,
             boundary: Optional[Sequence[tuple[float, float]]] = None,
             cell: Optional[float] = None) -> Grid:
    """Inverse-distance-weighted surface. A cell centre that coincides with
    a sample takes its value exactly."""
    cell = cell or cell_size(width, height)
    cols = max(1, int(np.ceil(width / cell)))
    rows = max(1, int(np.ceil(height / cell)))
    gx, gy = np.meshgrid(_centres(cols, cell, width), _centres(rows, cell, height))
    out = np.full(rows * cols, np.nan)
    pts = np.asarray(xy, dtype=float).reshape(-1, 2)
    vals = np.asarray(values, dtype=float)
    if len(pts) == 0:
        return Grid(out.reshape(rows, cols), cell, width, height)
    fx, fy = gx.ravel(), gy.ravel()
    mask = (polygon_mask(fx, fy, boundary) if boundary is not None
            else np.ones(fx.shape, dtype=bool))
    idx = np.flatnonzero(mask)
    for s in range(0, len(idx), CHUNK):
        sel = idx[s:s + CHUNK]
        dx = fx[sel, None] - pts[None, :, 0]
        dy = fy[sel, None] - pts[None, :, 1]
        d2 = dx * dx + dy * dy
        exact = d2 < 1e-12
        with np.errstate(divide="ignore"):
            w = 1.0 / np.power(d2, power / 2.0)
        w[exact] = 0.0
        res = (w @ vals) / w.sum(axis=1)
        hit = exact.any(axis=1)
        if hit.any():
            res[hit] = vals[exact[hit].argmax(axis=1)]
        out[sel] = res
    return Grid(out.reshape(rows, cols), cell, width, height)


def to_rgba(grid: Grid, ramp: ColorRamp, opacity: float = 0.6) -> np.ndarray:
    """(rows, cols, 4) uint8, transparent where NaN."""
    lut = ramp.lut(256)
    v = grid.values
    ok = ~np.isnan(v)
    t = np.zeros(v.shape)
    t[ok] = (np.clip(v[ok], ramp.lo, ramp.hi) - ramp.lo) / ((ramp.hi - ramp.lo) or 1)
    i = np.round(t * 255).astype(np.int64)
    rgba = np.zeros(v.shape + (4,), dtype=np.uint8)
    rgba[..., :3] = lut[i]
    rgba[..., 3] = np.where(ok, int(round(255 * max(0.0, min(1.0, opacity)))), 0)
    return np.ascontiguousarray(rgba)


# ---------------------------------------------------------------------------
# A floor's heatmap
# ---------------------------------------------------------------------------


@dataclass
class HeatmapSpec:
    metric: str = "best_rssi"
    flt: Filter = field(default_factory=Filter)


@dataclass
class FloorHeatmap:
    grid: Optional[Grid]
    ramp: ColorRamp
    boundary: list[tuple[float, float]]
    point_values: dict[str, float]          # point id → metric value
    auto_boundary: bool


def margin_px(project, floor) -> float:
    """Default boundary margin: the project's ``boundary_margin_ft`` on a
    calibrated floor, else 3 % of the plan's long side."""
    if floor.scale is not None:
        ft = float(project.settings.get("boundary_margin_ft", 10.0))
        return G.PlanUnits(floor.scale, "ft").to_px(ft)
    w, h = floor.image_size
    return 0.03 * max(w, h, 1)


def floor_heatmap(project, floor, spec: HeatmapSpec) -> FloorHeatmap:
    s = project.settings
    cov = int(s.get("coverage_dbm", -67))
    ramp = ramp_for(spec.metric, s.get("thresholds", (-67, -70, -80)), cov)
    vals: dict[str, float] = {}
    xy = []
    for p in project.points_on(floor.id):
        v = point_metric(project.samples_for(p.id), spec.metric, spec.flt, cov)
        if v is not None:
            vals[p.id] = v
            xy.append((p.x, p.y))
    all_xy = [(p.x, p.y) for p in project.points_on(floor.id)]
    auto = not floor.boundary
    boundary = list(floor.boundary) if floor.boundary else (
        G.default_boundary(all_xy, margin_px(project, floor)) if all_xy else [])
    w, h = floor.image_size
    grid = None
    if vals and w > 0 and h > 0:
        grid = idw_grid(xy, list(vals.values()), w, h,
                        float(s.get("idw_power", 2.0)), boundary or None)
    return FloorHeatmap(grid, ramp, boundary, vals, auto)


def area_at_least(grid: Grid, thresholds: Sequence[float]) -> list[tuple[float, float]]:
    """Fraction of the surveyed area (cells inside the boundary) whose value
    is ≥ each threshold."""
    v = grid.values[~np.isnan(grid.values)]
    if v.size == 0:
        return [(t, 0.0) for t in thresholds]
    return [(t, float((v >= t).mean())) for t in thresholds]
