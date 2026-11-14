"""Survey tab: floor plan, survey points, project file actions.

The tab doesn't own a scanner. It asks the main window for scans
(``scan_needed`` for point mode, ``continuous_needed`` for walk mode) and
is fed every scan through ``on_scan_started`` / ``on_scan_result``.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFormLayout,
                               QGraphicsItem, QGroupBox, QHBoxLayout, QInputDialog, QLabel,
                               QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton,
                               QScrollArea, QSlider, QSpinBox, QSplitter, QToolButton,
                               QVBoxLayout, QWidget)

from ..logging_setup import get_logger
from ..survey import geometry as G
from ..survey import plan_import as PI
from ..survey import project as P
from ..survey.capture import PointCapture, WalkSession
from ..survey import aps as A
from ..survey import heatmap as H
from ..survey.metrics import METRICS, Filter, point_metric
from .background import run_in_background
from .heatmap_widgets import LegendWidget, grid_pixmap
from .plan_view import PlanView
from .survey_dialogs import APDialog, CalibrateDialog, import_plan

log = get_logger("survey")

POINT_RADIUS = 9.0   # screen pixels (points ignore the view's zoom)
AUTOSAVE_MS = 60_000
BANDS = ("All bands", "2.4 GHz", "5 GHz", "6 GHz")
DEFAULT_HEATMAP = {"show": True, "metric": "best_rssi", "ssids": [], "band": "",
                   "bssid": "", "opacity": 60, "labels": False, "show_aps": True}
FILE_FILTER = f"WLAN surveys (*{P.SUFFIX});;All files (*)"


class PointItem(QGraphicsItem):
    """A survey point: filled by its metric colour, with a progress ring
    while scans are being taken. Constant size on screen."""

    def __init__(self, point_id: str, x: float, y: float, walk: bool = False):
        super().__init__()
        self.setData(0, point_id)
        self.setPos(x, y)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setZValue(20)
        self.color = "#6b7280"
        self.progress: Optional[float] = None   # 0..1 while capturing
        self.label = ""
        self.radius = POINT_RADIUS * (0.55 if walk else 1.0)

    def boundingRect(self) -> QRectF:
        r = self.radius + 4
        return QRectF(-r, -r, 2 * r, 2 * r + (14 if self.label else 0)).adjusted(-30, 0, 30, 0)

    def set_state(self, color: str, progress: Optional[float] = None, label: str = "") -> None:
        self.prepareGeometryChange()
        self.color, self.progress, self.label = color, progress, label
        self.update()

    def paint(self, p: QPainter, _opt, _w=None) -> None:
        r = self.radius
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor("#111827"), 1.5))   # dark outline: plans are mostly white
        p.setBrush(QBrush(QColor(self.color)))
        p.drawEllipse(QPointF(0, 0), r, r)
        if self.progress is not None:
            ring = QRectF(-r - 3, -r - 3, 2 * r + 6, 2 * r + 6)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(17, 24, 39, 90), 3))
            p.drawEllipse(ring)
            p.setPen(QPen(QColor("#38bdf8"), 3))
            p.drawArc(ring, 90 * 16, -int(360 * 16 * max(0.02, self.progress)))
        if self.label:
            p.setPen(QColor("#111827"))
            p.drawText(QRectF(-40, r + 1, 80, 14), Qt.AlignmentFlag.AlignHCenter, self.label)


class APItem(QGraphicsItem):
    """AP marker: drag it (Pan tool) to where the AP really is."""

    SIZE = 22.0

    def __init__(self, marker, on_moved: Callable[[str, float, float], None]):
        super().__init__()
        self.setData(0, marker.id)
        self.setPos(marker.x, marker.y)
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations
                      | QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setZValue(30)
        self.label = marker.label
        self.manual = marker.manual
        self._on_moved = on_moved
        self._press = None

    def boundingRect(self) -> QRectF:
        h = self.SIZE / 2
        return QRectF(-90, -h - 2, 180, self.SIZE + 22)

    def paint(self, p: QPainter, _opt, _w=None) -> None:
        h = self.SIZE / 2
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#f8fafc"), 2, Qt.PenStyle.SolidLine if self.manual
                   else Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(QBrush(QColor("#1d4ed8" if self.manual else "#475569")))
        p.drawRoundedRect(QRectF(-h, -h, self.SIZE, self.SIZE), 5, 5)
        p.setPen(QColor("#f8fafc"))
        f = p.font()
        f.setBold(True)
        f.setPointSizeF(7.5)
        p.setFont(f)
        p.drawText(QRectF(-h, -h, self.SIZE, self.SIZE), Qt.AlignmentFlag.AlignCenter, "AP")
        f.setBold(False)
        f.setPointSizeF(8)
        p.setFont(f)
        tw = min(176, p.fontMetrics().horizontalAdvance(self.label) + 8)
        box = QRectF(-tw / 2, h + 3, tw, 15)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(15, 23, 42, 200))
        p.drawRoundedRect(box, 3, 3)
        p.setPen(QColor("#f8fafc"))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter,
                   p.fontMetrics().elidedText(self.label, Qt.TextElideMode.ElideRight, int(tw - 4)))

    def mousePressEvent(self, event):
        self._press = QPointF(self.pos())
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self._press is not None and self.pos() != self._press:
            self._on_moved(self.data(0), self.pos().x(), self.pos().y())
        self._press = None


class SurveyTab(QWidget):
    scan_needed = Signal()         # one more scan please (point mode)
    continuous_needed = Signal()   # keep scanning (walk mode)
    project_changed = Signal()     # project replaced, or edited

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project = P.SurveyProject()
        self.path: Optional[Path] = None
        self.dirty = False
        self.floor_id = ""
        self.capture: Optional[PointCapture] = None
        self.walk: Optional[WalkSession] = None
        self._walk_line: list = []
        self._scan_in_flight = False
        self._items: dict[str, PointItem] = {}
        self._overlay: list = []
        self._recovery_checked = False
        self._heat_item = None
        self._ap_items: dict[str, APItem] = {}
        self._boundary_item = None
        self.heat: Optional[H.FloorHeatmap] = None
        # Hooks so tests can answer dialogs.
        self.ask: Callable[[str, str], bool] = self._ask_yes_no
        self.ask_save: Callable[[], Optional[bool]] = self._ask_save
        self.calibrate_prompt: Callable[[float, str], Optional[tuple[float, str]]] = \
            self._calibrate_prompt
        self.ap_prompt: Callable[[list, str, set], Optional[tuple[str, list[str]]]] = \
            self._ap_prompt
        self.ping_runner: Optional[Callable[[str, int], dict]] = None

        self._build_ui()
        self._autosave = QTimer(self)
        self._autosave.setInterval(AUTOSAVE_MS)
        self._autosave.timeout.connect(self.autosave_now)
        self._autosave.start()
        self._heat_timer = QTimer(self)
        self._heat_timer.setSingleShot(True)
        self._heat_timer.setInterval(120)
        self._heat_timer.timeout.connect(self.refresh_heatmap)
        self._sync_all()

    # ------------------------------------------------------------------ UI

    def _btn(self, text: str, tip: str, slot, checkable=False) -> QPushButton:
        b = QPushButton(text)
        b.setToolTip(tip)
        b.setCheckable(checkable)
        if slot is not None:
            b.clicked.connect(slot)
        return b

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)

        row1 = QHBoxLayout()
        file_btn = QToolButton()
        file_btn.setText("Survey ▾")
        file_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        m = QMenu(file_btn)
        m.addAction("New survey", self.new_project)
        m.addAction("Open…", lambda: self.open_project())
        m.addSeparator()
        self._act_save = m.addAction("Save", self.save)
        m.addAction("Save As…", lambda: self.save_as())
        m.addSeparator()
        m.addAction("Rename survey…", self._rename_project)
        file_btn.setMenu(m)
        self._file_menu = m
        row1.addWidget(file_btn)

        row1.addSpacing(10)
        row1.addWidget(QLabel("Floor:"))
        self.floor_combo = QComboBox()
        self.floor_combo.setMinimumWidth(140)
        self.floor_combo.currentIndexChanged.connect(self._on_floor_combo)
        row1.addWidget(self.floor_combo)
        row1.addWidget(self._btn("+ Floor plan…", "Import a PNG, JPG or PDF floor plan as a new floor",
                                 lambda: self.add_floor()))
        floor_btn = QToolButton()
        floor_btn.setText("⋯")
        floor_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        fm = QMenu(floor_btn)
        fm.addAction("Rename floor…", self._rename_floor)
        fm.addAction("Replace plan image…", self._replace_plan)
        fm.addAction("Remove floor", self._remove_floor)
        floor_btn.setMenu(fm)
        row1.addWidget(floor_btn)

        row1.addSpacing(10)
        self.cal_btn = self._btn("📏 Calibrate", "Click two points on the plan, then type the "
                                 "real distance between them", None, checkable=True)
        self.cal_btn.toggled.connect(lambda on: self.set_tool("calibrate" if on else "pan"))
        row1.addWidget(self.cal_btn)
        row1.addWidget(QLabel("Units:"))
        self.unit_combo = QComboBox()
        self.unit_combo.addItems(list(G.UNITS))
        self.unit_combo.currentTextChanged.connect(self._on_unit)
        row1.addWidget(self.unit_combo)
        row1.addWidget(self._btn("Fit", "Fit the plan in the view", lambda: self.view.fit()))
        row1.addStretch()
        lay.addLayout(row1)

        row2 = QHBoxLayout()
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        self.tool_buttons = {}
        for tool, text, tip in (
                ("pan", "✋ Pan", "Drag to pan; wheel to zoom"),
                ("point", "📍 Point", "Click where you're standing to take scans there"),
                ("walk", "🚶 Walk", "Click a start point, walk at a steady pace, click the next "
                                   "point; scans in between are spread along the line. Esc ends."),
        ):
            b = self._btn(text, tip, None, checkable=True)
            b.clicked.connect(lambda _c=False, t=tool: self.set_tool(t))
            self.tool_group.addButton(b)
            self.tool_buttons[tool] = b
            row2.addWidget(b)
        self.tool_buttons["pan"].setChecked(True)
        row2.addSpacing(10)
        self.undo_btn = self._btn("↶ Undo point", "Remove the last point (or walk segment)",
                                  self.undo_last_point)
        row2.addWidget(self.undo_btn)
        self.cancel_btn = self._btn("✕ Cancel capture", "Stop taking scans at this point",
                                    self.cancel_capture)
        self.cancel_btn.setVisible(False)
        row2.addWidget(self.cancel_btn)
        row2.addSpacing(10)
        row2.addWidget(QLabel("Scans per point:"))
        self.scans_spin = QSpinBox()
        self.scans_spin.setRange(1, 20)
        self.scans_spin.valueChanged.connect(lambda v: self._set_setting("scans_per_point", v))
        row2.addWidget(self.scans_spin)
        self.ping_cb = QCheckBox("Ping gateway at each point")
        self.ping_cb.setToolTip("After the scans, ping the default gateway 5 times "
                                "and store average latency and loss")
        self.ping_cb.toggled.connect(lambda v: self._set_setting("ping_enabled", bool(v)))
        row2.addWidget(self.ping_cb)
        row2.addStretch()
        lay.addLayout(row2)

        self.view = PlanView()
        self.view.point_clicked.connect(self.on_point_clicked)
        self.view.walk_clicked.connect(self.on_walk_clicked)
        self.view.walk_cancelled.connect(lambda: self.set_tool("pan"))
        self.view.calibration_line.connect(self._on_calibration_line)
        self.view.boundary_drawn.connect(self.set_boundary)
        self.view.ap_place_clicked.connect(self.on_ap_place)
        self.view.item_menu.connect(self._on_item_menu)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.view)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._build_heat_panel())
        scroll.setMinimumWidth(290)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        split.addWidget(scroll)
        split.setStretchFactor(0, 1)
        split.setSizes([900, 290])
        lay.addWidget(split, 1)

        self.status = QLabel("")
        self.status.setStyleSheet("color: #9ca3af; padding: 2px 4px;")
        lay.addWidget(self.status)

    def _build_heat_panel(self) -> QWidget:
        panel = QWidget()
        v = QVBoxLayout(panel)
        v.setContentsMargins(4, 0, 4, 0)
        box = QGroupBox("Heatmap")
        form = QFormLayout(box)
        self.show_heat_cb = QCheckBox("Show heatmap")
        self.show_heat_cb.toggled.connect(lambda on: self._set_heat("show", bool(on)))
        form.addRow(self.show_heat_cb)
        self.metric_combo = QComboBox()
        self.metric_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.metric_combo.setMinimumContentsLength(14)
        for key, info in METRICS.items():
            self.metric_combo.addItem(info.label, key)
        self.metric_combo.currentIndexChanged.connect(
            lambda _i: self._set_heat("metric", self.metric_combo.currentData()))
        form.addRow("Metric:", self.metric_combo)
        self.bssid_combo = QComboBox()
        self.bssid_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.bssid_combo.setMinimumContentsLength(14)
        self.bssid_combo.currentIndexChanged.connect(
            lambda _i: self._set_heat("bssid", self.bssid_combo.currentData() or ""))
        form.addRow("BSSID:", self.bssid_combo)
        self.band_combo = QComboBox()
        self.band_combo.addItems(list(BANDS))
        self.band_combo.currentTextChanged.connect(
            lambda t: self._set_heat("band", "" if t == BANDS[0] else t))
        form.addRow("Band:", self.band_combo)
        self.ssid_list = QListWidget()
        self.ssid_list.setMaximumHeight(130)
        self.ssid_list.setToolTip("Tick SSIDs to include. None ticked = all networks.")
        self.ssid_list.itemChanged.connect(self._on_ssid_item)
        form.addRow("SSIDs:", self.ssid_list)
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(5, 100)
        self.opacity_slider.valueChanged.connect(lambda val: self._set_heat("opacity", int(val)))
        form.addRow("Opacity:", self.opacity_slider)
        self.labels_cb = QCheckBox("Point labels")
        self.labels_cb.toggled.connect(lambda on: self._set_heat("labels", bool(on)))
        form.addRow(self.labels_cb)
        v.addWidget(box)

        tbox = QGroupBox("Thresholds (dBm)")
        tf = QFormLayout(tbox)
        self.thr_spins = []
        for i, name in enumerate(("Voice", "Data", "Edge")):
            sp = QSpinBox()
            sp.setRange(-100, -30)
            sp.valueChanged.connect(self._on_thresholds)
            tf.addRow(f"{name}:", sp)
            self.thr_spins.append(sp)
        self.cov_spin = QSpinBox()
        self.cov_spin.setRange(-100, -30)
        self.cov_spin.setToolTip("Signal an AP must reach to count for coverage overlap")
        self.cov_spin.valueChanged.connect(lambda val: self._set_setting("coverage_dbm", int(val),
                                                                         heat=True))
        tf.addRow("Overlap ≥:", self.cov_spin)
        v.addWidget(tbox)

        bbox = QGroupBox("Survey boundary")
        bl = QVBoxLayout(bbox)
        self.boundary_lbl = QLabel("")
        self.boundary_lbl.setWordWrap(True)
        bl.addWidget(self.boundary_lbl)
        row = QHBoxLayout()
        self.draw_boundary_btn = self._btn("Draw…", "Click the outline's corners on the plan; "
                                           "double-click or Enter to finish, Esc to cancel",
                                           lambda: self.set_tool("boundary"))
        row.addWidget(self.draw_boundary_btn)
        row.addWidget(self._btn("Auto", "Use the points' outline plus a margin",
                                lambda: self.set_boundary(None)))
        bl.addLayout(row)
        v.addWidget(bbox)

        abox = QGroupBox("Access points")
        al = QVBoxLayout(abox)
        self.show_aps_cb = QCheckBox("Show APs")
        self.show_aps_cb.setToolTip("Estimated from where each AP was heard strongest "
                                    "(dashed grey). Drag a marker with the Pan tool to its real "
                                    "spot (it turns blue and stays put).")
        self.show_aps_cb.toggled.connect(lambda on: self._set_heat("show_aps", bool(on)))
        al.addWidget(self.show_aps_cb)
        self.aps_lbl = QLabel("")
        self.aps_lbl.setWordWrap(True)
        al.addWidget(self.aps_lbl)
        arow = QHBoxLayout()
        arow.addWidget(self._btn("Place AP…", "Click on the plan where an AP is, then name it "
                                 "and pick its BSSIDs", lambda: self.set_tool("place_ap")))
        arow.addWidget(self._btn("Forget deleted", "Estimate APs you deleted again",
                                 self._unignore_aps))
        al.addLayout(arow)
        v.addWidget(abox)

        self.legend = LegendWidget()
        v.addWidget(self.legend)
        v.addStretch()
        return panel

    # ------------------------------------------------------------- dialogs

    def _ask_yes_no(self, title: str, text: str) -> bool:
        return QMessageBox.question(self, title, text) == QMessageBox.StandardButton.Yes

    def _ask_save(self) -> Optional[bool]:
        """True = save, False = discard, None = cancel."""
        r = QMessageBox.question(
            self, "Unsaved survey", f"Save changes to “{self.project.name}”?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Save:
            return True
        if r == QMessageBox.StandardButton.Discard:
            return False
        return None

    def _ap_prompt(self, choices: list, label: str, selected: set):
        dlg = APDialog(choices, label, selected, self)
        return dlg.values() if dlg.exec() else None

    def _calibrate_prompt(self, px: float, unit: str):
        dlg = CalibrateDialog(px, unit, self)
        return dlg.values() if dlg.exec() else None

    # ------------------------------------------------------------- project

    def mark_dirty(self) -> None:
        self.dirty = True
        self.project.touch()
        self._sync_status()
        self._heat_timer.start()
        self.project_changed.emit()

    def maybe_save(self) -> bool:
        """Ask to save unsaved changes. False means the user cancelled."""
        if not self.dirty:
            return True
        ans = self.ask_save()
        if ans is None:
            return False
        if ans:
            return self.save()
        P.discard_autosave(self.path)
        return True

    def set_project(self, project: P.SurveyProject, path: Optional[Path] = None,
                    dirty: bool = False) -> None:
        self.cancel_capture()
        self._stop_walk()
        self.project = project
        self.path = Path(path) if path else None
        self.dirty = dirty
        self.floor_id = project.floors[0].id if project.floors else ""
        self._sync_all()
        self.project_changed.emit()

    def new_project(self) -> None:
        if self.maybe_save():
            self.set_project(P.SurveyProject())

    def open_project(self, path: Optional[str] = None) -> bool:
        if not self.maybe_save():
            return False
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "Open survey", "", FILE_FILTER)
            if not path:
                return False
        path = Path(path)
        source, dirty = path, False
        rec = P.recovery_candidate(path)
        if rec and self.ask("Recover survey",
                            f"There are autosaved changes to {path.name} newer than the "
                            "file. Recover them?"):
            source, dirty = rec, True
        try:
            proj = P.load(source)
        except (P.UnsupportedSchema, OSError, ValueError, KeyError) as e:
            QMessageBox.warning(self, "Open survey", f"Could not open {path.name}:\n{e}")
            return False
        self.set_project(proj, path, dirty)
        return True

    def check_recovery(self) -> None:
        """Offer an autosaved untitled survey (once, when the tab is first shown)."""
        if self._recovery_checked:
            return
        self._recovery_checked = True
        rec = P.recovery_candidate(None)
        if rec is None:
            return
        if self.ask("Recover survey", "An unsaved survey was autosaved. Recover it?"):
            try:
                self.set_project(P.load(rec), None, dirty=True)
            except Exception as e:  # corrupt autosave: say so and move on
                QMessageBox.warning(self, "Recover survey", f"Could not recover it: {e}")
        else:
            P.discard_autosave(None)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.check_recovery)

    def save(self) -> bool:
        if self.path is None:
            return self.save_as()
        return self._write(self.path)

    def save_as(self, path: Optional[str] = None) -> bool:
        if path is None:
            start = str(self.path or Path.home() / (self.project.name + P.SUFFIX))
            path, _ = QFileDialog.getSaveFileName(self, "Save survey", start, FILE_FILTER)
            if not path:
                return False
        path = Path(path)
        if path.suffix.lower() != P.SUFFIX:
            path = path.with_name(path.name + P.SUFFIX)
        old = self.path
        if self._write(path):
            if old != path:
                P.discard_autosave(old)
            return True
        return False

    def _write(self, path: Path) -> bool:
        try:
            P.save(self.project, path)
        except OSError as e:
            QMessageBox.critical(self, "Save survey", f"Could not save {path.name}:\n{e}")
            return False
        self.path = path
        self.dirty = False
        P.discard_autosave(path)
        self._sync_status()
        log.info(f"Saved survey to {path}")
        return True

    def autosave_now(self) -> Optional[Path]:
        if not self.dirty:
            return None
        try:
            return P.autosave(self.project, self.path)
        except OSError as e:
            log.warning(f"Autosave failed: {e}")
            return None

    def _rename_project(self) -> None:
        name, ok = QInputDialog.getText(self, "Rename survey", "Name:", text=self.project.name)
        if ok and name.strip():
            self.project.name = name.strip()
            self.mark_dirty()
            self._sync_status()

    # --------------------------------------------------------------- floors

    @property
    def floor(self) -> Optional[P.Floor]:
        return self.project.floor(self.floor_id)

    def add_floor(self, path: Optional[str] = None, page: Optional[int] = None,
                  name: Optional[str] = None) -> Optional[P.Floor]:
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "Import floor plan", "", PI.FILE_FILTER)
            if not path:
                return None
        img = import_plan(path, self, page)
        if img is None:
            return None
        n = len(self.project.floors) + 1
        f = self.project.add_floor(name or f"Floor {n}", img.data, img.ext, img.size)
        self.floor_id = f.id
        self.mark_dirty()
        self._sync_all()
        return f

    def _replace_plan(self) -> None:
        f = self.floor
        if f is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Replace floor plan", "", PI.FILE_FILTER)
        img = import_plan(path, self) if path else None
        if img is None:
            return
        f.image_bytes, f.image_size = img.data, img.size
        f.image_file = f"plans/{f.id}.{img.ext}"
        if self.ask("Replace floor plan", "Keep the existing scale? (Choose No if the new "
                    "image has a different resolution, then calibrate again.)") is False:
            f.scale = None
        self.mark_dirty()
        self._sync_all()

    def _rename_floor(self) -> None:
        f = self.floor
        if f is None:
            return
        name, ok = QInputDialog.getText(self, "Rename floor", "Name:", text=f.name)
        if ok and name.strip():
            f.name = name.strip()
            self.mark_dirty()
            self._sync_floors()

    def _remove_floor(self) -> None:
        f = self.floor
        if f is None:
            return
        n = len(self.project.points_on(f.id))
        if not self.ask("Remove floor", f"Remove “{f.name}” and its {n} survey point(s)?"):
            return
        self.cancel_capture()
        self._stop_walk()
        self.project.remove_floor(f.id)
        self.floor_id = self.project.floors[0].id if self.project.floors else ""
        self.mark_dirty()
        self._sync_all()

    def set_floor(self, floor_id: str) -> None:
        if floor_id == self.floor_id:
            return
        self.cancel_capture()
        self._stop_walk()
        self.floor_id = floor_id
        self._sync_plan()

    def _on_floor_combo(self, idx: int) -> None:
        fid = self.floor_combo.itemData(idx)
        if fid:
            self.set_floor(fid)

    # ---------------------------------------------------------- calibration

    def units(self) -> G.PlanUnits:
        f = self.floor
        return G.PlanUnits(f.scale if f else None, self.project.settings.get("unit", "ft"))

    def _on_unit(self, unit: str) -> None:
        if self.project.settings.get("unit") != unit:
            self._set_setting("unit", unit)
        self._sync_status()

    def _on_calibration_line(self, p1: QPointF, p2: QPointF) -> None:
        px = G.pixel_distance((p1.x(), p1.y()), (p2.x(), p2.y()))
        ans = self.calibrate_prompt(px, self.project.settings.get("unit", "ft"))
        self.cal_btn.setChecked(False)
        if ans:
            self.apply_calibration((p1.x(), p1.y()), (p2.x(), p2.y()), *ans)

    def apply_calibration(self, p1, p2, distance: float, unit: str) -> bool:
        f = self.floor
        if f is None:
            return False
        try:
            f.scale = G.make_scale(p1, p2, distance, unit)
        except ValueError as e:
            QMessageBox.warning(self, "Set scale", str(e))
            return False
        self.mark_dirty()
        self._sync_status()
        return True

    # ---------------------------------------------------------------- tools

    def set_tool(self, tool: str) -> None:
        if tool != "walk":
            self._stop_walk()
        if tool in ("point", "walk", "boundary", "calibrate", "place_ap") and self.floor is None:
            self._flash("Import a floor plan first (+ Floor plan…)")
            tool = "pan"
        self.view.set_tool(tool)
        b = self.tool_buttons.get(tool)
        if b:
            b.setChecked(True)
        else:
            self.tool_group.setExclusive(False)
            for btn in self.tool_buttons.values():
                btn.setChecked(False)
            self.tool_group.setExclusive(True)
        if tool != "calibrate" and self.cal_btn.isChecked():
            self.cal_btn.blockSignals(True)
            self.cal_btn.setChecked(False)
            self.cal_btn.blockSignals(False)
        if tool == "walk":
            self.walk = WalkSession(self.floor_id)
            self.continuous_needed.emit()
        self._sync_status()

    def _flash(self, text: str) -> None:
        self.status.setText(text)

    # ------------------------------------------------------- point capture

    def on_point_clicked(self, x: float, y: float) -> Optional[P.SurveyPoint]:
        if self.floor is None:
            return None
        if self.capture is not None:
            self._flash("Still taking scans at the last point — wait or cancel it first")
            return None
        p = self.project.add_point(self.floor_id, x, y)
        self.mark_dirty()
        self._add_item(p)
        self._start_capture(p.id)
        return p

    def _start_capture(self, point_id: str) -> None:
        n = int(self.project.settings.get("scans_per_point", 3))
        self.capture = PointCapture(point_id, n, skip=1 if self._scan_in_flight else 0)
        self.cancel_btn.setVisible(True)
        self._update_item(point_id)
        self._sync_status()
        self.scan_needed.emit()

    def retake_point(self, point_id: str) -> None:
        if self.capture is not None:
            self._flash("Finish or cancel the current capture first")
            return
        self.project.clear_samples(point_id)
        self.mark_dirty()
        self._start_capture(point_id)

    def cancel_capture(self) -> None:
        c = self.capture
        if c is None:
            return
        self.capture = None
        self.cancel_btn.setVisible(False)
        if c.got == 0:
            self.project.remove_point(c.point_id)
            self._remove_item(c.point_id)
        else:
            self._update_item(c.point_id)
        self.mark_dirty()
        self._sync_status()

    def on_scan_started(self) -> None:
        self._scan_in_flight = True

    def on_scan_result(self, result) -> None:
        self._scan_in_flight = False
        if self.walk is not None and self.walk.started:
            self.walk.feed(result, time.time())
            self._sync_status()
        c = self.capture
        if c is None:
            return
        if c.feed(self.project, result):
            self.mark_dirty()
        if c.done:
            self.capture = None
            self.cancel_btn.setVisible(False)
            self._update_item(c.point_id)
            self._after_point(c.point_id)
        else:
            self._update_item(c.point_id)
            self.scan_needed.emit()
        self._sync_status()

    def _after_point(self, point_id: str) -> None:
        if not self.project.settings.get("ping_enabled"):
            return
        count = int(self.project.settings.get("ping_count", 5))
        target = self.project.settings.get("ping_target", "")
        runner = self.ping_runner

        def work():
            from ..connection import default_gateway
            from ..survey.ping import ping_summary
            tgt = target or default_gateway() or ""
            if not tgt:
                return {"target": "", "error": "no default gateway", "sent": 0,
                        "received": 0, "loss_pct": None, "avg_ms": None}
            return (runner or ping_summary)(tgt, count)

        def done(res):
            p = self.project.point(point_id)
            if p is not None and res:
                p.ping = res
                self.mark_dirty()
                self._update_item(point_id)

        run_in_background(work, done, parent=self)

    # ------------------------------------------------------------ walk mode

    def on_walk_clicked(self, x: float, y: float) -> list:
        if self.walk is None:
            self.walk = WalkSession(self.floor_id)
        created = self.walk.click(self.project, x, y, time.time())
        self._walk_line.append((x, y))
        for p in created:
            self._add_item(p)
        if created or len(self._walk_line) == 1:
            self.mark_dirty()
        self._draw_walk_line()
        self._sync_status()
        return created

    def _stop_walk(self) -> None:
        if self.walk is not None:
            self.walk.stop()
            self.walk = None
        self._walk_line = []
        self._draw_walk_line()

    def _draw_walk_line(self) -> None:
        sc = self.view.scene()
        for it in self._overlay:
            sc.removeItem(it)
        self._overlay = []
        pen = QPen(QColor("#38bdf8"), 2, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        pts = self._walk_line
        for a, b in zip(pts, pts[1:]):
            ln = sc.addLine(a[0], a[1], b[0], b[1], pen)
            ln.setZValue(15)
            self._overlay.append(ln)
        if pts:
            x, y = pts[-1]
            dot = sc.addEllipse(-6, -6, 12, 12, QPen(QColor("#38bdf8"), 2),
                                QBrush(QColor(56, 189, 248, 90)))
            dot.setPos(x, y)
            dot.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            dot.setZValue(25)
            self._overlay.append(dot)

    # ------------------------------------------------------------- editing

    def undo_last_point(self) -> None:
        pts = self.project.points_on(self.floor_id)
        if not pts:
            return
        last = pts[-1]
        if self.capture is not None and self.capture.point_id == last.id:
            self.capture = None
            self.cancel_btn.setVisible(False)
        ids = [last.id]
        if last.mode == "walk" and last.segment:
            ids = [p.id for p in pts if p.segment == last.segment]
        for pid in ids:
            self.project.remove_point(pid)
            self._remove_item(pid)
        self.mark_dirty()
        self._sync_status()

    def delete_point(self, point_id: str) -> None:
        if self.capture is not None and self.capture.point_id == point_id:
            self.capture = None
            self.cancel_btn.setVisible(False)
        self.project.remove_point(point_id)
        self._remove_item(point_id)
        self.mark_dirty()
        self._sync_status()

    def _on_item_menu(self, tag: str, global_pos) -> None:
        if tag.startswith("ap-"):
            self._ap_menu(tag, global_pos)
            return
        if not tag.startswith("pt-") or self.project.point(tag) is None:
            return
        m = QMenu(self)
        m.addAction("Retake point", lambda: self.retake_point(tag))
        m.addAction("Delete point", lambda: self.delete_point(tag))
        m.exec(global_pos)

    # -------------------------------------------------------------- display

    def point_value(self, point_id: str) -> Optional[float]:
        return point_metric(self.project.samples_for(point_id), "best_rssi")

    def point_summary(self, point_id: str) -> str:
        p = self.project.point(point_id)
        samples = self.project.samples_for(point_id)
        lines = [f"{'Walk' if p and p.mode == 'walk' else 'Point'}: {len(samples)} scan(s)"]
        v = self.point_value(point_id)
        lines.append(f"Best RSSI: {v:.0f} dBm" if v is not None else "No networks seen")
        best: dict[str, tuple[int, str]] = {}
        for s in samples:
            for r in s.readings:
                if r.bssid not in best or r.rssi > best[r.bssid][0]:
                    best[r.bssid] = (r.rssi, r.ssid)
        for bssid, (rssi, ssid) in sorted(best.items(), key=lambda kv: -kv[1][0])[:5]:
            lines.append(f"  {rssi} dBm  {ssid or '(hidden)'}  {bssid}")
        if p and p.ping:
            pg = p.ping
            if pg.get("avg_ms") is not None:
                lines.append(f"Ping {pg.get('target')}: {pg['avg_ms']:.1f} ms, "
                             f"{pg.get('loss_pct', 0):.0f}% loss")
            else:
                lines.append(f"Ping {pg.get('target') or ''}: no replies "
                             f"{pg.get('error', '')}".rstrip())
        return "\n".join(lines)

    def _add_item(self, p: P.SurveyPoint) -> None:
        it = PointItem(p.id, p.x, p.y, walk=p.mode == "walk")
        self.view.scene().addItem(it)
        self._items[p.id] = it
        self._update_item(p.id)

    def _remove_item(self, point_id: str) -> None:
        it = self._items.pop(point_id, None)
        if it is not None:
            self.view.scene().removeItem(it)

    def _update_item(self, point_id: str) -> None:
        it = self._items.get(point_id)
        if it is None:
            return
        c = self.capture
        progress = c.got / c.needed if c is not None and c.point_id == point_id else None
        spec = self.heat_spec()
        s = self.project.settings
        ramp = H.ramp_for(spec.metric, s.get("thresholds", (-67, -70, -80)),
                          int(s.get("coverage_dbm", -67)))
        v = point_metric(self.project.samples_for(point_id), spec.metric, spec.flt,
                         int(s.get("coverage_dbm", -67)))
        label = ""
        if self.heat_settings().get("labels") and v is not None:
            label = f"{v:.0f}"
        it.set_state(ramp.hex(v), progress, label)
        it.setToolTip(self.point_summary(point_id))

    def _sync_plan(self) -> None:
        for pid in list(self._items):
            self._remove_item(pid)
        f = self.floor
        pm = None
        if f is not None and f.image_bytes:
            pm = QPixmap()
            pm.loadFromData(f.image_bytes)
        self.view.set_plan(pm)
        if f is not None:
            for p in self.project.points_on(f.id):
                self._add_item(p)
        self._draw_walk_line()
        self._sync_status()
        self._sync_filter_lists(force=True)
        self.refresh_heatmap()

    def _sync_floors(self) -> None:
        self.floor_combo.blockSignals(True)
        self.floor_combo.clear()
        for f in self.project.floors:
            self.floor_combo.addItem(f.name, f.id)
        i = self.floor_combo.findData(self.floor_id)
        self.floor_combo.setCurrentIndex(max(0, i))
        self.floor_combo.blockSignals(False)

    def _sync_all(self) -> None:
        s = self.project.settings
        for w, v in ((self.scans_spin, int(s.get("scans_per_point", 3))),
                     (self.ping_cb, bool(s.get("ping_enabled"))),
                     (self.unit_combo, s.get("unit", "ft"))):
            w.blockSignals(True)
            if isinstance(w, QComboBox):
                w.setCurrentText(v)
            elif isinstance(w, QCheckBox):
                w.setChecked(v)
            else:
                w.setValue(v)
            w.blockSignals(False)
        self._sync_floors()
        self._sync_heat_controls()
        self._sync_plan()

    def _set_setting(self, key: str, value, heat: bool = False) -> None:
        if self.project.settings.get(key) != value:
            self.project.settings[key] = value
            self.mark_dirty()
            if heat:
                self._refresh_point_items()

    # -------------------------------------------------------------- heatmap

    def heat_settings(self) -> dict:
        hs = self.project.settings.get("heatmap")
        if not isinstance(hs, dict):
            hs = self.project.settings["heatmap"] = {}
        for k, v in DEFAULT_HEATMAP.items():
            hs.setdefault(k, list(v) if isinstance(v, list) else v)
        return hs

    def heat_spec(self) -> H.HeatmapSpec:
        hs = self.heat_settings()
        return H.HeatmapSpec(hs["metric"], Filter(set(hs["ssids"]) or None,
                                                   hs["band"] or None, hs["bssid"]))

    def _set_heat(self, key: str, value) -> None:
        hs = self.heat_settings()
        if hs.get(key) == value:
            return
        hs[key] = value
        if key == "metric":
            self.bssid_combo.setEnabled(value == "bssid_rssi")
        self.mark_dirty()
        if key in ("metric", "ssids", "band", "bssid", "labels"):
            self._refresh_point_items()

    def _on_ssid_item(self, _item) -> None:
        ticked = [self.ssid_list.item(i).data(Qt.ItemDataRole.UserRole)
                  for i in range(self.ssid_list.count())
                  if self.ssid_list.item(i).checkState() == Qt.CheckState.Checked]
        self._set_heat("ssids", sorted(ticked))

    def _on_thresholds(self) -> None:
        vals = sorted((sp.value() for sp in self.thr_spins), reverse=True)
        self._set_setting("thresholds", vals, heat=True)

    def _sync_heat_controls(self) -> None:
        hs = self.heat_settings()
        s = self.project.settings
        widgets = [self.show_heat_cb, self.metric_combo, self.band_combo, self.opacity_slider,
                   self.labels_cb, self.cov_spin, *self.thr_spins]
        for w in widgets:
            w.blockSignals(True)
        self.show_heat_cb.setChecked(bool(hs["show"]))
        self.metric_combo.setCurrentIndex(max(0, self.metric_combo.findData(hs["metric"])))
        self.band_combo.setCurrentText(hs["band"] or BANDS[0])
        self.opacity_slider.setValue(int(hs["opacity"]))
        self.labels_cb.setChecked(bool(hs["labels"]))
        self.cov_spin.setValue(int(s.get("coverage_dbm", -67)))
        for sp, val in zip(self.thr_spins, sorted(s.get("thresholds", (-67, -70, -80)),
                                                  reverse=True)):
            sp.setValue(int(val))
        for w in widgets:
            w.blockSignals(False)
        self.bssid_combo.setEnabled(hs["metric"] == "bssid_rssi")
        self.show_aps_cb.blockSignals(True)
        self.show_aps_cb.setChecked(bool(hs["show_aps"]))
        self.show_aps_cb.blockSignals(False)
        self._sync_filter_lists(force=True)

    def _floor_readings(self):
        ids = {p.id for p in self.project.points_on(self.floor_id)}
        for smp in self.project.samples:
            if smp.point_id in ids:
                yield from smp.readings

    def _sync_filter_lists(self, force: bool = False) -> None:
        hs = self.heat_settings()
        ssids, bssids = set(hs["ssids"]), {}
        for r in self._floor_readings():
            if r.ssid:
                ssids.add(r.ssid)
            if r.bssid not in bssids or (r.ssid and not bssids[r.bssid]):
                bssids[r.bssid] = r.ssid
        if hs["bssid"]:
            bssids.setdefault(hs["bssid"], "")
        have_s = [self.ssid_list.item(i).data(Qt.ItemDataRole.UserRole)
                  for i in range(self.ssid_list.count())]
        if force or sorted(ssids) != have_s:
            self.ssid_list.blockSignals(True)
            self.ssid_list.clear()
            for name in sorted(ssids):
                it = QListWidgetItem(name)
                it.setData(Qt.ItemDataRole.UserRole, name)
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(Qt.CheckState.Checked if name in hs["ssids"]
                                 else Qt.CheckState.Unchecked)
                self.ssid_list.addItem(it)
            self.ssid_list.blockSignals(False)
        have_b = [self.bssid_combo.itemData(i) for i in range(self.bssid_combo.count())]
        want_b = sorted(bssids, key=lambda b: (bssids[b].lower(), b))
        if force or want_b != have_b:
            self.bssid_combo.blockSignals(True)
            self.bssid_combo.clear()
            for b in want_b:
                self.bssid_combo.addItem(f"{bssids[b] or '(hidden)'} — {b}", b)
            i = self.bssid_combo.findData(hs["bssid"])
            if i < 0 and want_b:
                i = 0
                hs["bssid"] = want_b[0]
            self.bssid_combo.setCurrentIndex(i)
            self.bssid_combo.blockSignals(False)

    # ------------------------------------------------------------------ APs

    def _sync_aps(self) -> None:
        """Re-estimate automatic markers and redraw all of them."""
        sc = self.view.scene()
        for it in self._ap_items.values():
            sc.removeItem(it)
        self._ap_items = {}
        f = self.floor
        if f is None:
            self.aps_lbl.setText("")
            return
        A.refresh_estimates(self.project, f)
        n_man = sum(1 for m in f.ap_markers if m.manual)
        self.aps_lbl.setText(f"{len(f.ap_markers) - n_man} estimated, {n_man} placed"
                             + (f", {len(f.ignored_bssids)} BSSID(s) ignored"
                                if f.ignored_bssids else ""))
        if not self.heat_settings().get("show_aps", True):
            return
        for m in f.ap_markers:
            it = APItem(m, self._on_ap_moved)
            how = "placed by hand" if m.manual else \
                f"estimated from its {A.TOP_K} strongest points"
            it.setToolTip(f"{m.label}\n{how}\n" + "\n".join(m.bssids))
            sc.addItem(it)
            self._ap_items[m.id] = it

    def _ap_choices(self) -> list[tuple[str, str, Optional[float]]]:
        means, ssids = A.bssid_stats(self.project, self.floor_id)
        rows = []
        for b, pts in means.items():
            ssid = ssids[b].most_common(1)[0][0] if ssids.get(b) else ""
            rows.append((b, ssid, max(pts.values()) if pts else None))
        return sorted(rows, key=lambda r: (-(r[2] if r[2] is not None else -999), r[0]))

    def on_ap_place(self, x: float, y: float):
        f = self.floor
        if f is None:
            return None
        ans = self.ap_prompt(self._ap_choices(), f"AP {len(f.ap_markers) + 1}", set())
        self.set_tool("pan")
        if not ans:
            return None
        m = A.add_manual(f, x, y, *ans)
        self.mark_dirty()
        self._sync_aps()
        return m

    def _on_ap_moved(self, ap_id: str, x: float, y: float) -> None:
        f = self.floor
        if f is not None and A.move(f, ap_id, x, y):
            self.mark_dirty()

    def _marker(self, ap_id: str):
        f = self.floor
        return next((m for m in f.ap_markers if m.id == ap_id), None) if f else None

    def _ap_menu(self, ap_id: str, global_pos) -> None:
        m = self._marker(ap_id)
        if m is None:
            return
        menu = QMenu(self)
        menu.addAction("Rename / BSSIDs…", lambda: self.edit_ap(ap_id))
        if m.manual:
            menu.addAction("Back to estimated position", lambda: self.reset_ap(ap_id))
        menu.addAction("Delete", lambda: self.delete_ap(ap_id))
        menu.exec(global_pos)

    def edit_ap(self, ap_id: str) -> None:
        m = self._marker(ap_id)
        if m is None:
            return
        ans = self.ap_prompt(self._ap_choices(), m.label, set(m.bssids))
        if not ans:
            return
        f = self.floor
        label, bssids = ans
        f.ap_markers = [a for a in f.ap_markers if a.id != ap_id]
        new = A.add_manual(f, m.x, m.y, label or m.label, bssids or m.bssids)
        new.id = ap_id
        self.mark_dirty()
        self._sync_aps()

    def reset_ap(self, ap_id: str) -> None:
        f = self.floor
        if f is not None:
            A.reset(f, ap_id)
            self.mark_dirty()
            self._sync_aps()

    def delete_ap(self, ap_id: str) -> None:
        f = self.floor
        if f is not None:
            A.remove(f, ap_id)
            self.mark_dirty()
            self._sync_aps()

    def _unignore_aps(self) -> None:
        f = self.floor
        if f is not None and f.ignored_bssids:
            f.ignored_bssids = []
            self.mark_dirty()
            self._sync_aps()

    def _refresh_point_items(self) -> None:
        for pid in self._items:
            self._update_item(pid)

    def set_boundary(self, pts) -> None:
        f = self.floor
        if f is None:
            return
        f.boundary = [tuple(map(float, p)) for p in pts] if pts else None
        if self.view.tool == "boundary":
            self.set_tool("pan")
        self.mark_dirty()
        self.refresh_heatmap()

    def refresh_heatmap(self) -> None:
        """Recompute the floor's heatmap and redraw the layer."""
        self._heat_timer.stop()
        sc = self.view.scene()
        for attr in ("_heat_item", "_boundary_item"):
            it = getattr(self, attr)
            if it is not None:
                sc.removeItem(it)
                setattr(self, attr, None)
        f = self.floor
        if f is None:
            self.heat = None
            self.legend.set_ramp(None, "")
            self.boundary_lbl.setText("")
            self._sync_aps()
            return
        self._sync_filter_lists()
        hs = self.heat_settings()
        self.heat = H.floor_heatmap(self.project, f, self.heat_spec())
        info = METRICS[hs["metric"]]
        self.legend.set_ramp(self.heat.ramp, info.label)
        n = len(self.heat.point_values)
        self.boundary_lbl.setText(
            ("Auto: points' outline + margin" if self.heat.auto_boundary else
             f"Drawn ({len(f.boundary)} corners)")
            + f". {n} point(s) with data for this metric.")
        self._sync_aps()
        if not hs["show"]:
            return
        if self.heat.grid is not None:
            from .heatmap_widgets import grid_image
            img = grid_image(self.heat.grid, self.heat.ramp, hs["opacity"] / 100.0)
            item = sc.addPixmap(QPixmap.fromImage(img))
            item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            item.setScale(self.heat.grid.cell)
            item.setZValue(-50)
            self._heat_item = item
        if len(self.heat.boundary) >= 3:
            from PySide6.QtGui import QPolygonF
            pen = QPen(QColor("#0891b2"), 1.5, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            poly = sc.addPolygon(QPolygonF([QPointF(x, y) for x, y in self.heat.boundary]), pen)
            poly.setZValue(-40)
            self._boundary_item = poly

    def _sync_status(self) -> None:
        parts = [self.project.name + (" •" if self.dirty else ""),
                 str(self.path) if self.path else "not saved"]
        f = self.floor
        if f is None:
            parts.append("No floor plan — use “+ Floor plan…” to import one")
        else:
            parts.append(self.units().describe())
            parts.append(f"{len(self.project.points_on(f.id))} point(s) on {f.name}")
        c = self.capture
        if c is not None:
            parts.append(f"Taking scans: {c.got}/{c.needed}")
        elif self.walk is not None:
            parts.append(f"Walking: {len(self.walk.pending)} scan(s) since last click"
                         if self.walk.started else "Walk: click your start point")
        elif self.view.tool == "calibrate":
            parts.append("Calibrate: click two points a known distance apart")
        elif self.view.tool == "place_ap":
            parts.append("Place AP: click where the access point is")
        elif self.view.tool == "boundary":
            parts.append("Boundary: click the corners; double-click or Enter to finish, Esc cancels")
        self.status.setText("   |   ".join(parts))
