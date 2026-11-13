"""Floor plan import: PNG / JPG as-is, PDF pages rendered with QtPdf.

Requires a Q(Gui)Application (QImage / QPdfDocument).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize
from PySide6.QtGui import QImage

IMAGE_SUFFIXES = {".png": "png", ".jpg": "jpg", ".jpeg": "jpg"}
PDF_SUFFIX = ".pdf"
FILE_FILTER = "Floor plans (*.png *.jpg *.jpeg *.pdf);;Images (*.png *.jpg *.jpeg);;PDF (*.pdf)"

#: PDF pages render so the long side is about this many pixels.
PDF_TARGET_PX = 3000


class PlanImportError(ValueError):
    pass


@dataclass
class PlanImage:
    data: bytes          # encoded image bytes stored in the project
    ext: str             # "png" / "jpg"
    size: tuple[int, int]
    source: str          # original file name (+ page)


def image_to_png(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba.data())


def decode(data: bytes) -> QImage:
    img = QImage()
    img.loadFromData(data)
    return img


def load_image(path: str | Path) -> PlanImage:
    path = Path(path)
    ext = IMAGE_SUFFIXES.get(path.suffix.lower())
    if not ext:
        raise PlanImportError(f"Unsupported image type: {path.suffix}")
    data = path.read_bytes()
    img = decode(data)
    if img.isNull():
        raise PlanImportError(f"Could not read image: {path.name}")
    return PlanImage(data, ext, (img.width(), img.height()), path.name)


def _open_pdf(path: Path):
    from PySide6.QtPdf import QPdfDocument

    doc = QPdfDocument()
    err = doc.load(str(path))
    if err != QPdfDocument.Error.None_ or doc.pageCount() < 1:
        raise PlanImportError(f"Could not open PDF: {path.name} ({err})")
    return doc


def pdf_page_count(path: str | Path) -> int:
    doc = _open_pdf(Path(path))
    n = doc.pageCount()
    doc.close()
    return n


def render_pdf_page(path: str | Path, page: int = 0, target_px: int = PDF_TARGET_PX) -> PlanImage:
    """Render one page (0-based) to PNG, long side ≈ ``target_px``, white background."""
    path = Path(path)
    doc = _open_pdf(path)
    try:
        if not 0 <= page < doc.pageCount():
            raise PlanImportError(f"{path.name} has no page {page + 1}")
        pts = doc.pagePointSize(page)
        long_side = max(pts.width(), pts.height()) or 1.0
        k = target_px / long_side
        size = QSize(max(1, round(pts.width() * k)), max(1, round(pts.height() * k)))
        img = doc.render(page, size)
        if img.isNull():
            raise PlanImportError(f"Could not render page {page + 1} of {path.name}")
        flat = QImage(img.size(), QImage.Format.Format_RGB32)
        flat.fill(0xFFFFFFFF)
        from PySide6.QtGui import QPainter
        p = QPainter(flat)
        p.drawImage(0, 0, img)
        p.end()
        return PlanImage(image_to_png(flat), "png", (flat.width(), flat.height()),
                         f"{path.name} p.{page + 1}")
    finally:
        doc.close()


def render_pdf_thumbnails(path: str | Path, px: int = 160) -> list[QImage]:
    doc = _open_pdf(Path(path))
    try:
        out = []
        for i in range(doc.pageCount()):
            pts = doc.pagePointSize(i)
            k = px / (max(pts.width(), pts.height()) or 1.0)
            out.append(doc.render(i, QSize(max(1, round(pts.width() * k)),
                                           max(1, round(pts.height() * k)))))
        return out
    finally:
        doc.close()


def is_pdf(path: str | Path) -> bool:
    return Path(path).suffix.lower() == PDF_SUFFIX
