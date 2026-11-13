"""F2: floor plan import (PNG/JPG, PDF via QtPdf, page choice)."""

import pytest

from conftest import need_qt

need_qt()

from PySide6.QtCore import QMarginsF, QRectF  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPageLayout, QPageSize, QPainter, QPdfWriter  # noqa: E402

from wlan_scan.survey import plan_import as PI  # noqa: E402


@pytest.fixture
def png(tmp_path, qapp):
    img = QImage(400, 300, QImage.Format.Format_RGB32)
    img.fill(QColor("white"))
    p = tmp_path / "plan.png"
    img.save(str(p))
    return p


@pytest.fixture
def pdf(tmp_path, qapp):
    """Two-page PDF: page 1 landscape with a red box, page 2 portrait."""
    p = tmp_path / "plans.pdf"
    w = QPdfWriter(str(p))
    w.setPageLayout(QPageLayout(QPageSize(QPageSize.PageSizeId.A4),
                                QPageLayout.Orientation.Landscape, QMarginsF()))
    painter = QPainter(w)
    painter.fillRect(QRectF(100, 100, 2000, 1000), QColor("red"))
    w.setPageLayout(QPageLayout(QPageSize(QPageSize.PageSizeId.A4),
                                QPageLayout.Orientation.Portrait, QMarginsF()))
    w.newPage()
    painter.fillRect(QRectF(100, 100, 500, 500), QColor("blue"))
    painter.end()
    return p


def test_png_kept_as_is(png):
    plan = PI.load_image(png)
    assert plan.ext == "png" and plan.size == (400, 300)
    assert plan.data == png.read_bytes()


def test_jpeg(tmp_path, qapp):
    img = QImage(64, 32, QImage.Format.Format_RGB32)
    img.fill(QColor("gray"))
    p = tmp_path / "x.JPEG"
    img.save(str(p), "JPG")
    plan = PI.load_image(p)
    assert plan.ext == "jpg" and plan.size == (64, 32)


def test_bad_image(tmp_path, qapp):
    p = tmp_path / "broken.png"
    p.write_bytes(b"not an image")
    with pytest.raises(PI.PlanImportError):
        PI.load_image(p)
    with pytest.raises(PI.PlanImportError):
        PI.load_image(tmp_path / "plan.gif")


def test_pdf_pages(pdf):
    assert PI.pdf_page_count(pdf) == 2
    first = PI.render_pdf_page(pdf, 0)
    second = PI.render_pdf_page(pdf, 1)
    assert first.ext == "png"
    assert first.size[0] > first.size[1]          # landscape
    assert second.size[1] > second.size[0]        # portrait
    assert max(first.size) == pytest.approx(PI.PDF_TARGET_PX, abs=2)
    img = PI.decode(first.data)
    assert QColor(img.pixel(img.width() // 4, img.height() // 4)).red() > 200
    assert "p.2" in second.source


def test_pdf_bad_page(pdf):
    with pytest.raises(PI.PlanImportError):
        PI.render_pdf_page(pdf, 5)


def test_import_plan_picks_page_with_dialog(pdf, qtbot, monkeypatch):
    from wlan_scan.gui import survey_dialogs as D

    monkeypatch.setattr(D.PageSelectDialog, "exec", lambda self: (
        self.list.setCurrentRow(1), D.QDialog.DialogCode.Accepted)[1])
    plan = D.import_plan(pdf)
    assert plan.size[1] > plan.size[0]  # got page 2


def test_import_plan_cancel(pdf, qtbot, monkeypatch):
    from wlan_scan.gui import survey_dialogs as D

    monkeypatch.setattr(D.PageSelectDialog, "exec", lambda self: D.QDialog.DialogCode.Rejected)
    assert D.import_plan(pdf) is None


def test_single_page_pdf_needs_no_dialog(tmp_path, qtbot, monkeypatch):
    from wlan_scan.gui import survey_dialogs as D

    p = tmp_path / "one.pdf"
    w = QPdfWriter(str(p))
    QPainter(w).end()
    monkeypatch.setattr(D.PageSelectDialog, "exec",
                        lambda self: pytest.fail("dialog shown for 1-page PDF"))
    assert D.import_plan(p) is not None
