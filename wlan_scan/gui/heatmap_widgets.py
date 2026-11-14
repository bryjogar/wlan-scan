"""Heatmap drawing helpers: grid → pixmap, and the colour legend."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..survey.heatmap import Grid, to_rgba
from ..survey.metrics import ColorRamp


def grid_image(grid: Grid, ramp: ColorRamp, opacity: float) -> QImage:
    rgba = to_rgba(grid, ramp, opacity)
    h, w = rgba.shape[:2]
    img = QImage(rgba.data, w, h, 4 * w, QImage.Format.Format_RGBA8888)
    return img.copy()   # own the pixels (rgba is freed with this frame)


def grid_pixmap(grid: Grid, ramp: ColorRamp, opacity: float) -> QPixmap:
    """Pixmap at plan resolution (smoothly upscaled from the grid)."""
    img = grid_image(grid, ramp, opacity)
    big = img.scaled(int(round(grid.shape[1] * grid.cell)), int(round(grid.shape[0] * grid.cell)),
                     Qt.AspectRatioMode.IgnoreAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)
    return QPixmap.fromImage(big)


def paint_legend(p: QPainter, rect: QRectF, ramp: ColorRamp, title: str,
                 dark: bool = True) -> None:
    """Horizontal colour bar with labelled ticks and a title."""
    fg = QColor("#e5e7eb" if dark else "#111827")
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    f = QFont(p.font())
    f.setPointSizeF(max(7.0, rect.height() * 0.13))
    p.setFont(f)
    fm_h = p.fontMetrics().height()
    p.setPen(fg)
    p.drawText(QRectF(rect.left(), rect.top(), rect.width(), fm_h),
               Qt.AlignmentFlag.AlignLeft, f"{title} ({ramp.unit})" if ramp.unit else title)
    bar = QRectF(rect.left() + 4, rect.top() + fm_h + 4, rect.width() - 8,
                 max(8.0, rect.height() - 3 * fm_h - 14))   # room for 2 label lines
    g = QLinearGradient(bar.left(), 0, bar.right(), 0)
    span = (ramp.hi - ramp.lo) or 1
    for v, c in ramp.stops:
        g.setColorAt((v - ramp.lo) / span, QColor(c))
    p.fillRect(bar, g)
    p.setPen(QPen(fg, 1))
    p.drawRect(bar)
    # Labels that would overlap the previous one on a line drop to the next line.
    fm = p.fontMetrics()
    line_end: list[float] = []
    for v, label in ramp.ticks:
        x = bar.left() + bar.width() * (v - ramp.lo) / span
        w = fm.horizontalAdvance(label) + 6
        left = min(max(x - w / 2, rect.left()), rect.right() - w)
        line = next((i for i, end in enumerate(line_end) if left >= end), len(line_end))
        if line == len(line_end):
            line_end.append(0.0)
        line_end[line] = left + w
        y = bar.bottom() + 4 + line * fm_h
        p.drawLine(int(x), int(bar.bottom()), int(x), int(y if line else bar.bottom() + 4))
        p.drawText(QRectF(left, y, w, fm_h), Qt.AlignmentFlag.AlignHCenter, label)
    p.restore()


class LegendWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.ramp: Optional[ColorRamp] = None
        self.title = ""
        self.setMinimumHeight(84)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_ramp(self, ramp: Optional[ColorRamp], title: str) -> None:
        self.ramp, self.title = ramp, title
        self.update()

    def paintEvent(self, _ev):
        if self.ramp is None:
            return
        p = QPainter(self)
        paint_legend(p, QRectF(self.rect()).adjusted(2, 2, -2, -2), self.ramp, self.title)
        p.end()
