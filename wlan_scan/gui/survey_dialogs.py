"""Dialogs used by the Survey tab."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QLabel, QListWidget, QListWidgetItem,
                               QMessageBox, QVBoxLayout, QWidget)

from ..survey import plan_import as PI


class PageSelectDialog(QDialog):
    """Pick one page of a multi-page PDF (thumbnails)."""

    def __init__(self, path: str | Path, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(f"Choose a page — {Path(path).name}")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("This PDF has several pages. Pick the floor plan:"))
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setIconSize(QSize(160, 160))
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        for i, img in enumerate(PI.render_pdf_thumbnails(path)):
            it = QListWidgetItem(QIcon(QPixmap.fromImage(img)), f"Page {i + 1}")
            it.setData(Qt.ItemDataRole.UserRole, i)
            self.list.addItem(it)
        self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda _it: self.accept())
        lay.addWidget(self.list)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.resize(720, 480)

    def page(self) -> int:
        it = self.list.currentItem()
        return int(it.data(Qt.ItemDataRole.UserRole)) if it else 0


def import_plan(path: str | Path, parent: Optional[QWidget] = None,
                page: Optional[int] = None) -> Optional[PI.PlanImage]:
    """Load a PNG/JPG, or a PDF page (asks which page when there are several).
    Shows errors in a message box and returns None on failure/cancel."""
    try:
        if PI.is_pdf(path):
            if page is None:
                n = PI.pdf_page_count(path)
                page = 0
                if n > 1:
                    dlg = PageSelectDialog(path, parent)
                    if dlg.exec() != QDialog.DialogCode.Accepted:
                        return None
                    page = dlg.page()
            return PI.render_pdf_page(path, page)
        return PI.load_image(path)
    except PI.PlanImportError as e:
        QMessageBox.warning(parent, "Import floor plan", str(e))
        return None


class CalibrateDialog(QDialog):
    """Ask for the real distance between the two clicked points."""

    def __init__(self, pixel_length: float, unit: str = "ft",
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Set scale")
        form = QFormLayout(self)
        form.addRow(QLabel(f"The line you drew is {pixel_length:.0f} px long on the plan."))
        self.distance = QDoubleSpinBox()
        self.distance.setRange(0.1, 100000)
        self.distance.setDecimals(2)
        self.distance.setValue(10.0)
        self.unit = QComboBox()
        self.unit.addItems(["ft", "m"])
        self.unit.setCurrentText(unit)
        form.addRow("Real distance:", self.distance)
        form.addRow("Unit:", self.unit)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def values(self) -> tuple[float, str]:
        return self.distance.value(), self.unit.currentText()
