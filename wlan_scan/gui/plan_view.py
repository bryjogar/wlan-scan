"""Floor plan view: pan, zoom, fit, and click tools.

Scene coordinates are floor-plan image pixels (the plan pixmap sits at
0,0), so everything the survey stores maps 1:1 onto the scene.

Tools (``set_tool``):
    "pan"        drag to pan (default)
    "calibrate"  click two points → ``calibration_line(p1, p2)``
    "point"      click → ``point_clicked(x, y)``
    "walk"       click → ``walk_clicked(x, y)``
    "boundary"   click vertices, double-click / Enter to finish → ``boundary_drawn(list)``;
                 Esc cancels
    "place_ap"   click → ``ap_place_clicked(x, y)``
Wheel zooms around the cursor; middle-drag pans in every tool.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QGraphicsLineItem, QGraphicsPixmapItem, QGraphicsPolygonItem,
                               QGraphicsScene, QGraphicsView)

ZOOM_STEP = 1.25
ZOOM_MIN, ZOOM_MAX = 0.02, 40.0


class PlanView(QGraphicsView):
    calibration_line = Signal(QPointF, QPointF)
    point_clicked = Signal(float, float)
    walk_clicked = Signal(float, float)
    ap_place_clicked = Signal(float, float)
    boundary_drawn = Signal(list)
    zoom_changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.RenderHint.Antialiasing
                            | QPainter.RenderHint.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setBackgroundBrush(QBrush(QColor("#1a1d2e")))
        self._plan: Optional[QGraphicsPixmapItem] = None
        self._tool = "pan"
        self._cal_p1: Optional[QPointF] = None
        self._cal_line: Optional[QGraphicsLineItem] = None
        self._poly: list[QPointF] = []
        self._poly_item: Optional[QGraphicsPolygonItem] = None
        self._mid_pan: Optional[QPointF] = None
        self.set_tool("pan")

    # ----- content -----

    def set_plan(self, pixmap: Optional[QPixmap]) -> None:
        sc = self.scene()
        self._cancel_drawing()
        if self._plan is not None:
            sc.removeItem(self._plan)
            self._plan = None
        if pixmap is None or pixmap.isNull():
            sc.setSceneRect(QRectF(0, 0, 1, 1))
            return
        self._plan = sc.addPixmap(pixmap)
        self._plan.setZValue(-100)
        self._plan.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        sc.setSceneRect(QRectF(pixmap.rect()).adjusted(-200, -200, 200, 200))
        self.fit()

    def plan_rect(self) -> QRectF:
        return self._plan.boundingRect() if self._plan else QRectF()

    def has_plan(self) -> bool:
        return self._plan is not None

    # ----- zoom / fit -----

    def zoom(self) -> float:
        return self.transform().m11()

    def zoom_by(self, factor: float) -> None:
        z = self.zoom()
        factor = max(ZOOM_MIN / z, min(ZOOM_MAX / z, factor))
        self.scale(factor, factor)
        self.zoom_changed.emit(self.zoom())

    def fit(self) -> None:
        if self._plan is not None:
            self.fitInView(self._plan, Qt.AspectRatioMode.KeepAspectRatio)
            self.zoom_changed.emit(self.zoom())

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120.0
        if steps:
            self.zoom_by(ZOOM_STEP ** steps)
        event.accept()

    # ----- tools -----

    @property
    def tool(self) -> str:
        return self._tool

    def set_tool(self, tool: str) -> None:
        self._cancel_drawing()
        self._tool = tool
        if tool == "pan":
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)

    def _cancel_drawing(self) -> None:
        sc = self.scene()
        if self._cal_line is not None:
            sc.removeItem(self._cal_line)
            self._cal_line = None
        self._cal_p1 = None
        if self._poly_item is not None:
            sc.removeItem(self._poly_item)
            self._poly_item = None
        self._poly = []

    def _pen(self, color="#f59e0b", width=2.0) -> QPen:
        pen = QPen(QColor(color), width)
        pen.setCosmetic(True)  # constant on-screen width at any zoom
        return pen

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._mid_pan = event.position()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton or self._tool == "pan":
            super().mousePressEvent(event)
            return
        pos = self.mapToScene(event.position().toPoint())
        self.click_at(pos)
        event.accept()

    def click_at(self, pos: QPointF) -> None:
        """Apply the current tool at a scene position (also used by tests)."""
        t = self._tool
        if t == "calibrate":
            if self._cal_p1 is None:
                self._cal_p1 = pos
                self._cal_line = self.scene().addLine(pos.x(), pos.y(), pos.x(), pos.y(),
                                                      self._pen())
                self._cal_line.setZValue(50)
            else:
                p1 = self._cal_p1
                self._cal_line.setLine(p1.x(), p1.y(), pos.x(), pos.y())
                self.calibration_line.emit(p1, pos)
                self._cancel_drawing()
        elif t == "point":
            self.point_clicked.emit(pos.x(), pos.y())
        elif t == "walk":
            self.walk_clicked.emit(pos.x(), pos.y())
        elif t == "place_ap":
            self.ap_place_clicked.emit(pos.x(), pos.y())
        elif t == "boundary":
            self._poly.append(pos)
            if self._poly_item is None:
                self._poly_item = self.scene().addPolygon(
                    QPolygonF(self._poly), self._pen("#22d3ee"), QBrush(QColor(34, 211, 238, 30)))
                self._poly_item.setZValue(50)
            else:
                self._poly_item.setPolygon(QPolygonF(self._poly))

    def finish_boundary(self) -> None:
        if self._tool == "boundary" and len(self._poly) >= 3:
            pts = [(p.x(), p.y()) for p in self._poly]
            self._cancel_drawing()
            self.boundary_drawn.emit(pts)

    def mouseDoubleClickEvent(self, event):
        if self._tool == "boundary":
            self.finish_boundary()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event):
        if self._mid_pan is not None:
            d = event.position() - self._mid_pan
            self._mid_pan = event.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            event.accept()
            return
        if self._tool == "calibrate" and self._cal_p1 is not None and self._cal_line is not None:
            pos = self.mapToScene(event.position().toPoint())
            self._cal_line.setLine(self._cal_p1.x(), self._cal_p1.y(), pos.x(), pos.y())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton and self._mid_pan is not None:
            self._mid_pan = None
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor if self._tool == "pan"
                                      else Qt.CursorShape.CrossCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._cancel_drawing()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.finish_boundary()
            event.accept()
            return
        super().keyPressEvent(event)
