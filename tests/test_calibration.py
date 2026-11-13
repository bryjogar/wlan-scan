"""F3: calibration maths, uncalibrated pixel mode, the two-click tool."""

import math

import pytest

from wlan_scan.survey import geometry as G
from wlan_scan.survey.project import Scale


def test_scale_from_two_points():
    s = G.make_scale((100, 100), (400, 500), 25.0, "ft")   # 500 px apart
    assert s.px_per_unit == pytest.approx(20.0)
    u = G.PlanUnits(s)
    assert u.calibrated and u.label == "ft"
    assert u.to_units(200) == pytest.approx(10.0)
    assert u.to_px(1.5) == pytest.approx(30.0)
    assert u.point_to_units((40, 60)) == pytest.approx((2.0, 3.0))
    assert u.length_text(100) == "5.0 ft"
    assert u.describe() == "Scale: 1 ft = 20.00 px"


def test_display_in_other_unit():
    s = Scale((0, 0), (0, 1000), 100.0, "ft")    # 10 px per ft
    m = G.PlanUnits(s, unit="m")
    assert m.px_per_unit() == pytest.approx(10 * G.FT_PER_M)   # ≈ 32.81 px per m
    assert m.to_units(10 * G.FT_PER_M) == pytest.approx(1.0)
    metric = Scale((0, 0), (300, 0), 3.0, "m")                 # 100 px per m
    assert G.PlanUnits(metric, unit="ft").to_units(100) == pytest.approx(G.FT_PER_M)


def test_uncalibrated_is_pixels_and_says_so():
    u = G.PlanUnits(None)
    assert not u.calibrated and u.label == "px"
    assert u.to_units(123) == 123
    assert "not calibrated" in u.length_text(50)
    assert "Not calibrated" in u.describe()


@pytest.mark.parametrize("p1,p2,d,unit", [
    ((0, 0), (1, 0), 10, "ft"),     # too close
    ((0, 0), (100, 0), 0, "ft"),    # zero distance
    ((0, 0), (100, 0), -5, "m"),
    ((0, 0), (100, 0), 5, "yd"),
])
def test_invalid_calibration(p1, p2, d, unit):
    with pytest.raises(ValueError):
        G.make_scale(p1, p2, d, unit)


def test_unit_conversion():
    assert G.convert(1, "m", "ft") == pytest.approx(3.28084, abs=1e-5)
    assert G.convert(G.convert(7.5, "ft", "m"), "m", "ft") == pytest.approx(7.5)


def test_convex_hull_and_margin():
    pts = [(0, 0), (10, 0), (10, 10), (0, 10), (5, 5)]
    hull = G.convex_hull(pts)
    assert set(hull) == {(0, 0), (10, 0), (10, 10), (0, 10)}
    grown = G.default_boundary(pts, 2.0)
    assert G.point_in_polygon(-1.5, 5, grown) and not G.point_in_polygon(-3, 5, grown)
    assert G.point_in_polygon(5, 5, grown)
    one = G.default_boundary([(50, 50)], 10)
    assert G.point_in_polygon(55, 50, one) and not G.point_in_polygon(62, 50, one)


def test_point_in_polygon():
    sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert G.point_in_polygon(5, 5, sq)
    assert not G.point_in_polygon(15, 5, sq)


def test_calibrate_tool_emits_line(qtbot):
    from conftest import need_qt
    need_qt()
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPixmap
    from wlan_scan.gui.plan_view import PlanView

    v = PlanView()
    qtbot.addWidget(v)
    pm = QPixmap(800, 600)
    pm.fill()
    v.set_plan(pm)
    v.set_tool("calibrate")
    got = []
    v.calibration_line.connect(lambda a, b: got.append((a, b)))
    v.click_at(QPointF(100, 100))
    assert got == []
    v.click_at(QPointF(400, 500))
    (a, b), = got
    assert (a.x(), a.y(), b.x(), b.y()) == (100, 100, 400, 500)
    assert math.isclose(G.pixel_distance((a.x(), a.y()), (b.x(), b.y())), 500)


def test_boundary_tool_and_zoom(qtbot):
    from conftest import need_qt
    need_qt()
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPixmap
    from wlan_scan.gui.plan_view import PlanView

    v = PlanView()
    qtbot.addWidget(v)
    v.resize(400, 300)
    pm = QPixmap(800, 600)
    pm.fill()
    v.set_plan(pm)
    z = v.zoom()
    v.zoom_by(2)
    assert v.zoom() == pytest.approx(2 * z)
    v.fit()
    assert v.zoom() == pytest.approx(z, rel=0.05)
    v.set_tool("boundary")
    out = []
    v.boundary_drawn.connect(out.append)
    for p in [(0, 0), (100, 0), (100, 100)]:
        v.click_at(QPointF(*p))
    v.finish_boundary()
    assert out == [[(0, 0), (100, 0), (100, 100)]]
