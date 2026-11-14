"""F7: samples CSV, heatmap PNG, PDF report."""

import csv
import re

import pytest

from conftest import FIXTURES
from wlan_scan.recording import load_recording
from wlan_scan.survey import geometry as G
from wlan_scan.survey import survey_csv
from wlan_scan.survey.project import SurveyProject


@pytest.fixture(scope="module")
def walk():
    return load_recording(FIXTURES / "synthetic_walk.jsonl")[1]


def make_project(walk, image=b"", size=(800, 400)):
    proj = SurveyProject(name="HQ", site="Main St")
    f = proj.add_floor("Ground", image, "png", size)
    for i in range(10):
        p = proj.add_point(f.id, 40 + 80 * i, 200)
        proj.add_sample(p.id, walk[i])
        proj.add_sample(p.id, walk[19 - i] if i else walk[0])
    proj.points[0].ping = {"target": "10.0.0.1", "avg_ms": 2.5, "loss_pct": 0.0}
    return proj, f


def test_csv_one_row_per_point_and_bssid(walk, tmp_path):
    proj, f = make_project(walk)
    out = tmp_path / "s.csv"
    n = survey_csv.write(proj, out)
    with open(out, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == n
    assert list(rows[0]) == survey_csv.COLUMNS
    p1 = [r for r in rows if r["Point"] == "1"]
    assert len({r["BSSID"] for r in p1}) == len(p1)          # one row per BSSID
    assert p1[0]["Scans at point"] == "2" and p1[0]["Ping avg (ms)"] == "2.5"
    assert all(int(r["RSSI min (dBm)"]) <= float(r["RSSI avg (dBm)"]) <= int(r["RSSI max (dBm)"])
               for r in rows)
    # strongest first within a point
    assert [float(r["RSSI max (dBm)"]) for r in p1] == sorted(
        (float(r["RSSI max (dBm)"]) for r in p1), reverse=True)
    assert rows[0]["X (ft)"] == "" and rows[0]["X (px)"] == "40"   # not calibrated

    f.scale = G.make_scale((0, 0), (0, 100), 10, "ft")             # 10 px per ft
    survey_csv.write(proj, out)
    with open(out, encoding="utf-8-sig") as fh:
        r0 = next(csv.DictReader(fh))
    assert (r0["X (ft)"], r0["Y (ft)"]) == ("4.0", "20.0")
    f.scale = G.make_scale((0, 0), (0, 100), 1, "m")               # metres → still feet out
    assert next(survey_csv.rows(proj))["X (ft)"] == f"{0.4 * G.FT_PER_M:.1f}"


@pytest.fixture
def qt_project(qtbot, walk):
    from conftest import need_qt
    need_qt()
    from PySide6.QtGui import QColor, QImage
    from wlan_scan.survey.plan_import import image_to_png

    img = QImage(800, 400, QImage.Format.Format_RGB32)
    img.fill(QColor("white"))
    return make_project(walk, image_to_png(img))


def test_png_render_has_heat_legend_and_title(qt_project, tmp_path):
    from PySide6.QtGui import QColor, QImage
    from wlan_scan.gui import survey_export as E
    from wlan_scan.survey.heatmap import HeatmapSpec

    proj, f = qt_project
    out = tmp_path / "h.png"
    E.export_png(out, proj, f, HeatmapSpec(), E.RenderOptions(labels=True))
    img = QImage(str(out))
    assert img.width() == 800 and img.height() > 400
    title_h = round(max(10.0, 800 / 90) * 3.4)
    mid = QColor(img.pixel(400, title_h + 200))
    assert mid != QColor("white")                       # heat over the plan
    corner = QColor(img.pixel(2, title_h + 2))
    assert corner == QColor("white")                    # outside the boundary: plan only
    assert E.stats_text(__import__("wlan_scan.survey.heatmap", fromlist=["x"]).floor_heatmap(
        proj, f, HeatmapSpec()), "best_rssi", (-67, -70, -80)).startswith("Area ≥ -67 dBm")


def test_large_plan_is_downscaled(qt_project):
    from wlan_scan.gui import survey_export as E
    from wlan_scan.survey.heatmap import HeatmapSpec

    proj, f = qt_project
    img = E.render_floor(proj, f, HeatmapSpec(), E.RenderOptions(max_px=400))
    assert img.width() == 600                           # never below 600 px wide


def test_pdf_report_pages(qt_project, tmp_path):
    from wlan_scan.gui import survey_export as E
    from wlan_scan.survey.heatmap import HeatmapSpec
    from wlan_scan.survey.metrics import Filter

    proj, f = qt_project
    empty = proj.add_floor("Attic", b"", "png", (100, 100))   # no points: no pages
    assert empty
    out = tmp_path / "r.pdf"
    n = E.export_pdf(out, proj, HeatmapSpec())
    assert n == 4                                       # 5 metrics, one-BSSID skipped
    data = out.read_bytes()
    assert data.startswith(b"%PDF")
    assert len(re.findall(rb"/Type\s*/Page[^s]", data)) == 4
    n2 = E.export_pdf(out, proj, HeatmapSpec(flt=Filter(bssid="A0:36:BC:10:00:01")),
                      metrics=["bssid_rssi"])
    assert n2 == 1


def test_tab_exports(qtbot, tmp_path, walk):
    from conftest import need_qt
    need_qt()
    from wlan_scan.gui import survey_tab as ST
    from PySide6.QtGui import QColor, QImage

    img = QImage(400, 300, QImage.Format.Format_RGB32)
    img.fill(QColor("white"))
    img.save(str(tmp_path / "p.png"))
    t = ST.SurveyTab()
    qtbot.addWidget(t)
    t.ask_save = lambda: False
    t.add_floor(str(tmp_path / "p.png"))
    t.scans_spin.setValue(1)
    for i in range(4):
        t.on_point_clicked(50 + 90 * i, 150)
        t.on_scan_result(walk[i])
    assert t.export_png(str(tmp_path / "a.png")).is_file()
    assert t.export_csv(str(tmp_path / "a.csv")).is_file()
    assert t.export_pdf(str(tmp_path / "a.pdf")).is_file()
    assert "4-page" in t.status.text()


def test_legend_ignores_callers_brush(qtbot):
    """The legend outline once used the caller's leftover brush and painted
    over the colour bar."""
    from conftest import need_qt
    need_qt()
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage, QPainter
    from wlan_scan.gui.heatmap_widgets import paint_legend
    from wlan_scan.survey.metrics import ramp_for

    img = QImage(400, 120, QImage.Format.Format_RGB32)
    img.fill(QColor("white"))
    p = QPainter(img)
    p.setBrush(QColor(15, 23, 42, 210))
    paint_legend(p, QRectF(0, 0, 400, 120), ramp_for("best_rssi"), "Signal", dark=False)
    p.end()
    reds = [QColor(img.pixel(8, y)) for y in range(120)]
    assert any(c.red() > 200 and c.green() < 120 for c in reds)
