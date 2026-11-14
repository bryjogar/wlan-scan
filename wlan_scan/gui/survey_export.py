"""Survey exports that need Qt: heatmap PNG and the PDF report.

Rendering works from the project alone (not the on-screen view), so it
runs headless and gives the same picture whatever the zoom.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from PySide6.QtCore import QMarginsF, QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QImage, QPageLayout, QPageSize, QPainter,
                           QPdfWriter, QPen, QPolygonF)

from ..survey import heatmap as H
from ..survey.geometry import PlanUnits
from ..survey.metrics import METRICS, point_metric
from ..survey.plan_import import decode
from .heatmap_widgets import grid_image, paint_legend

MAX_PX = 2400


@dataclass
class RenderOptions:
    opacity: float = 0.6
    points: bool = True
    labels: bool = False
    aps: bool = True
    title: str = ""
    max_px: int = MAX_PX


def filter_text(spec: H.HeatmapSpec) -> str:
    f = spec.flt
    parts = []
    if spec.metric == "bssid_rssi" and f.bssid:
        parts.append(f"BSSID {f.bssid}")
    parts.append("SSIDs: " + ", ".join(sorted(f.ssids)) if f.ssids else "All SSIDs")
    parts.append(f.band or "all bands")
    return " · ".join(parts)


def stats_text(hm: H.FloorHeatmap, metric: str, thresholds) -> str:
    if hm.grid is None:
        return "No data for this metric and filter."
    if metric in ("best_rssi", "bssid_rssi"):
        names = ("voice", "data", "edge")
        fr = H.area_at_least(hm.grid, sorted(thresholds, reverse=True))
        return "Area ≥ " + ", ".join(f"{t:.0f} dBm ({n}): {100 * a:.0f}%"
                                     for (t, a), n in zip(fr, names))
    vals = list(hm.point_values.values())
    unit = METRICS[metric].unit
    return (f"Points: min {min(vals):.1f}, mean {sum(vals) / len(vals):.1f}, "
            f"max {max(vals):.1f} {unit}")


def render_floor(project, floor, spec: H.HeatmapSpec,
                 opts: Optional[RenderOptions] = None) -> QImage:
    """Plan + heatmap + points + APs, with a title strip and a legend footer."""
    opts = opts or RenderOptions()
    plan = decode(floor.image_bytes) if floor.image_bytes else QImage()
    pw, ph = floor.image_size if plan.isNull() else (plan.width(), plan.height())
    pw, ph = max(pw, 1), max(ph, 1)
    k = min(1.0, opts.max_px / max(pw, ph))
    if pw * k < 600:
        k = 600 / pw                             # small plans are scaled up to 600 px wide
    W, Hh = round(pw * k), round(ph * k)
    unit = max(10.0, W / 90)                     # base text/marker size
    title_h, foot_h = round(unit * 3.4), round(unit * 6.5)

    img = QImage(W, title_h + Hh + foot_h, QImage.Format.Format_RGB32)
    img.fill(QColor("white"))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    # Title
    f = QFont()
    f.setPixelSize(round(unit * 1.3))
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor("#111827"))
    info = METRICS[spec.metric]
    title = opts.title or f"{project.name} — {floor.name}"
    p.drawText(QRectF(unit, unit * 0.4, W - 2 * unit, unit * 1.6),
               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
    f.setBold(False)
    f.setPixelSize(round(unit * 0.9))
    p.setFont(f)
    sub = " · ".join(x for x in (info.label, filter_text(spec), project.site,
                                 datetime.now().strftime("%Y-%m-%d")) if x)
    p.drawText(QRectF(unit, unit * 1.9, W - 2 * unit, unit * 1.3),
               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, sub)

    # Plan and layers, in plan pixel coordinates.
    p.save()
    p.translate(0, title_h)
    p.scale(k, k)
    if not plan.isNull():
        p.drawImage(QRectF(0, 0, pw, ph), plan)
    hm = H.floor_heatmap(project, floor, spec)
    if hm.grid is not None:
        gi = grid_image(hm.grid, hm.ramp, opts.opacity)
        p.drawImage(QRectF(0, 0, gi.width() * hm.grid.cell, gi.height() * hm.grid.cell), gi)
    if len(hm.boundary) >= 3:
        pen = QPen(QColor("#0891b2"), 1.5 / k, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in hm.boundary]))
    p.restore()

    def to_img(x, y):
        return QPointF(x * k, title_h + y * k)

    cov = int(project.settings.get("coverage_dbm", -67))
    if opts.points:
        r = unit * 0.45
        f.setPixelSize(round(unit * 0.8))
        p.setFont(f)
        for pt in project.points_on(floor.id):
            v = point_metric(project.samples_for(pt.id), spec.metric, spec.flt, cov)
            c = to_img(pt.x, pt.y)
            p.setPen(QPen(QColor("#111827"), max(1.0, unit / 12)))
            p.setBrush(QBrush(QColor(hm.ramp.hex(v))))
            rr = r * (0.6 if pt.mode == "walk" else 1.0)
            p.drawEllipse(c, rr, rr)
            if opts.labels and v is not None:
                p.setPen(QColor("#111827"))
                p.drawText(QRectF(c.x() - 3 * unit, c.y() + rr, 6 * unit, unit),
                           Qt.AlignmentFlag.AlignHCenter, f"{v:.0f}")
    if opts.aps:
        s = unit * 1.1
        f.setPixelSize(round(unit * 0.75))
        for m in floor.ap_markers:
            c = to_img(m.x, m.y)
            box = QRectF(c.x() - s / 2, c.y() - s / 2, s, s)
            p.setPen(QPen(QColor("#f8fafc"), max(1.0, unit / 10),
                          Qt.PenStyle.SolidLine if m.manual else Qt.PenStyle.DashLine))
            p.setBrush(QColor("#1d4ed8" if m.manual else "#475569"))
            p.drawRoundedRect(box, s / 5, s / 5)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor("#f8fafc"))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, "AP")
            f.setBold(False)
            p.setFont(f)
            tw = p.fontMetrics().horizontalAdvance(m.label) + unit * 0.6
            lab = QRectF(c.x() - tw / 2, c.y() + s / 2 + 2, tw, unit * 1.1)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(15, 23, 42, 210))
            p.drawRoundedRect(lab, 3, 3)
            p.setPen(QColor("#f8fafc"))
            p.drawText(lab, Qt.AlignmentFlag.AlignCenter, m.label)

    # Footer: legend + stats
    top = title_h + Hh
    p.setPen(QPen(QColor("#d1d5db"), 1))
    p.drawLine(0, top, W, top)
    f.setPixelSize(round(unit * 0.9))
    p.setFont(f)
    leg_w = min(W * 0.5, unit * 32)
    paint_legend(p, QRectF(unit, top + unit * 0.5, leg_w, foot_h - unit), hm.ramp,
                 info.label, dark=False)
    p.setPen(QColor("#111827"))
    n_pts = len(project.points_on(floor.id))
    n_scans = sum(len(project.samples_for(x.id)) for x in project.points_on(floor.id))
    scale = (f"Scale: 1 ft = {PlanUnits(floor.scale, 'ft').px_per_unit():.1f} px"
             if floor.scale else "Not calibrated")
    text = "\n".join([stats_text(hm, spec.metric, project.settings.get("thresholds",
                                                                        (-67, -70, -80))),
                      f"{n_pts} points, {n_scans} scans · {len(floor.ap_markers)} AP markers · "
                      f"{scale}"])
    p.drawText(QRectF(leg_w + 2 * unit, top + unit * 0.6, W - leg_w - 3 * unit, foot_h - unit),
               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
               text)
    p.end()
    return img


def export_png(path: str | Path, project, floor, spec: H.HeatmapSpec,
               opts: Optional[RenderOptions] = None) -> QImage:
    img = render_floor(project, floor, spec, opts)
    if not img.save(str(path), "PNG"):
        raise OSError(f"Could not write {path}")
    return img


def report_pages(project, base: H.HeatmapSpec, metrics: Iterable[str]):
    """(floor, spec) for every floor × metric (one-BSSID only if one is chosen)."""
    for f in project.floors:
        if not project.points_on(f.id):
            continue
        for m in metrics:
            if m == "bssid_rssi" and not base.flt.bssid:
                continue
            yield f, H.HeatmapSpec(m, base.flt)


def export_pdf(path: str | Path, project, base: H.HeatmapSpec,
               metrics: Iterable[str] = tuple(METRICS),
               opts: Optional[RenderOptions] = None) -> int:
    """One landscape A4 page per floor × metric. Returns the page count."""
    pages = list(report_pages(project, base, list(metrics)))
    w = QPdfWriter(str(path))
    w.setTitle(f"{project.name} — Wi-Fi survey")
    w.setCreator("WLAN Scan")
    w.setResolution(150)
    w.setPageLayout(QPageLayout(QPageSize(QPageSize.PageSizeId.A4),
                                QPageLayout.Orientation.Landscape, QMarginsF(10, 10, 10, 10),
                                QPageLayout.Unit.Millimeter))
    p = QPainter(w)
    if not pages:
        p.drawText(QRectF(0, 0, w.width(), w.height()), Qt.AlignmentFlag.AlignCenter,
                   "No survey points yet.")
    for i, (floor, spec) in enumerate(pages):
        if i:
            w.newPage()
        img = render_floor(project, floor, spec, opts)
        area = QRectF(0, 0, w.width(), w.height())
        s = min(area.width() / img.width(), area.height() / img.height())
        tgt = QRectF(0, 0, img.width() * s, img.height() * s)
        tgt.moveCenter(area.center())
        tgt.moveTop(0)
        p.drawImage(tgt, img)
    p.end()
    return len(pages)
