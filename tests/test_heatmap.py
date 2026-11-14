"""F5: IDW grid, boundary mask, metrics, colour ramps, and the heat layer."""

import numpy as np
import pytest

from conftest import FIXTURES
from wlan_scan.survey import geometry as G
from wlan_scan.survey import heatmap as H
from wlan_scan.survey import metrics as M
from wlan_scan.survey.project import Reading, Sample, SurveyProject


# ----- IDW -----------------------------------------------------------------

def test_idw_exact_at_samples_and_between():
    g = H.idw_grid([(5, 5), (95, 5)], [-40, -80], 100, 10, cell=10)
    assert g.shape == (1, 10)
    assert g.value_at(5, 5) == pytest.approx(-40)        # cell centre on a sample
    assert g.value_at(95, 5) == pytest.approx(-80)
    # Cells at x=45 and x=55 mirror each other about the midpoint.
    assert g.value_at(45, 5) + g.value_at(55, 5) == pytest.approx(-120)
    assert g.value_at(45, 5) > -60 > g.value_at(55, 5)
    row = g.values[0]
    assert np.all(np.diff(row) <= 1e-9)                  # monotone from -40 to -80


def test_idw_power_and_single_point():
    one = H.idw_grid([(50, 50)], [-55], 100, 100, cell=10)
    assert np.allclose(one.values, -55)
    near = H.idw_grid([(0, 0), (100, 0)], [0, 100], 100, 10, power=1, cell=10)
    far = H.idw_grid([(0, 0), (100, 0)], [0, 100], 100, 10, power=4, cell=10)
    # Higher power = more local: cell at x=25 sticks closer to the near sample.
    assert far.value_at(25, 5) < near.value_at(25, 5)


def test_idw_matches_brute_force():
    rng = np.random.default_rng(1)
    pts = rng.uniform(0, 300, size=(12, 2))
    vals = rng.uniform(-90, -40, size=12)
    g = H.idw_grid(pts.tolist(), vals.tolist(), 300, 200, cell=25)
    for (r, c) in [(0, 0), (3, 7), (7, 11)]:
        x, y = (c + 0.5) * 25, (r + 0.5) * 25
        w = 1 / np.hypot(pts[:, 0] - x, pts[:, 1] - y) ** 2
        assert g.values[r, c] == pytest.approx((w * vals).sum() / w.sum())


def test_boundary_mask_matches_scalar_rule():
    poly = [(10, 10), (190, 30), (150, 180), (40, 120)]
    g = H.idw_grid([(100, 100)], [-60], 200, 200, boundary=poly, cell=10)
    for r in range(20):
        for c in range(20):
            x, y = (c + 0.5) * 10, (r + 0.5) * 10
            assert np.isnan(g.values[r, c]) != G.point_in_polygon(x, y, poly)


def test_grid_size_capped():
    assert H.cell_size(3000, 2000) == pytest.approx(np.sqrt(3000 * 2000 / H.MAX_CELLS))
    g = H.idw_grid([(1, 1)], [0], 3000, 2000)
    assert g.values.size <= H.MAX_CELLS * 1.05


def test_to_rgba_transparent_outside_and_opacity():
    g = H.Grid(np.array([[np.nan, -90.0, -40.0]]), 1.0, 3, 1)
    rgba = H.to_rgba(g, M.ramp_for("best_rssi"), 0.5)
    assert rgba.shape == (1, 3, 4)
    assert rgba[0, 0, 3] == 0 and rgba[0, 1, 3] == 128
    assert tuple(rgba[0, 1, :3]) == M._rgb(M.RED)        # clamped below the ramp
    assert tuple(rgba[0, 2, :3]) == pytest.approx(M._rgb(M.DKGREEN), abs=3)


# ----- metrics ------------------------------------------------------------

def R(bssid, rssi, ssid="Corp", ch=36, band="5 GHz", util=0.0, sta=0):
    return Reading(bssid, ssid, rssi, ch, 80, band, "WPA2", util, sta)


def test_same_radio_heuristic():
    assert M.same_radio("A0:36:BC:10:00:01", "A0:36:BC:10:00:05")
    assert M.same_radio("A0:36:BC:10:00:01", "A2:36:BC:10:00:01")   # locally administered
    assert not M.same_radio("A0:36:BC:10:00:01", "A0:36:BC:10:01:01")
    assert not M.same_radio("A0:36:BC:10:00:01", "A0:36:BC:10:00:41")
    assert M.group_radios(["A0:00:00:00:00:10", "A0:00:00:00:00:11", "B0:00:00:00:00:10"]) == [
        ["A0:00:00:00:00:10", "A0:00:00:00:00:11"], ["B0:00:00:00:00:10"]]


def test_metrics_per_scan_then_averaged():
    s1 = Sample("p", "t", readings=[
        R("AA:00:00:00:01:00", -50, util=40, sta=3),
        R("AA:00:00:00:01:01", -51, ssid="Guest"),           # same radio, other SSID
        R("BB:00:00:00:02:00", -65),                          # other AP, same channel
        R("CC:00:00:00:03:00", -70, ch=1, band="2.4 GHz"),
        R("DD:00:00:00:04:00", -85),                          # below CCA: not co-channel
    ])
    s2 = Sample("p", "t", readings=[R("AA:00:00:00:01:00", -60, util=20, sta=3)])
    assert M.point_metric([s1, s2], "best_rssi") == pytest.approx(-55)
    assert M.point_metric([s1], "best_rssi", M.Filter({"Guest"})) == -51
    assert M.point_metric([s1], "best_rssi", M.Filter(band="2.4 GHz")) == -70
    assert M.point_metric([s1, s2], "bssid_rssi", M.Filter(bssid="bb:00:00:00:02:00")) == -65
    # ≥ -67: AA radio (2 BSSIDs = 1 AP) + BB = 2 in s1, 1 in s2
    assert M.point_metric([s1, s2], "coverage", coverage_dbm=-67) == pytest.approx(1.5)
    assert M.point_metric([s1], "coverage", coverage_dbm=-75) == 3
    assert M.point_metric([s1], "cochannel") == 1                 # only BB
    assert M.point_metric([s1, s2], "utilization") == pytest.approx(30)
    no_load = Sample("p", "t", readings=[R("EE:00:00:00:00:00", -50)])
    assert M.point_metric([no_load], "utilization") is None
    assert M.point_metric([], "best_rssi") is None
    with pytest.raises(ValueError):
        M.point_metric([s1], "nope")


def test_ramps():
    r = M.ramp_for("best_rssi", (-67, -70, -80))
    assert r.hex(-67) == M.GREEN and r.hex(-80) == M.ORANGE and r.hex(-200) == M.RED
    assert [t for t, _ in r.ticks] == [-80, -70, -67]
    custom = M.ramp_for("best_rssi", (-60, -75, -85))
    assert custom.hex(-60) == M.GREEN and custom.hex(-85) == M.ORANGE
    assert M.ramp_for("cochannel").hex(0) == M.GREEN
    assert M.ramp_for("utilization").hex(100) == M.RED
    assert M.ramp_for("coverage").hex(2) == M.GREEN
    assert r.lut(16).shape == (16, 3)


def test_floor_heatmap_auto_boundary_and_filters():
    from wlan_scan.recording import load_recording
    _m, scans = load_recording(FIXTURES / "synthetic_walk.jsonl")
    proj = SurveyProject()
    f = proj.add_floor("F1", b"x", "png", (600, 400))
    for i, r in enumerate(scans[:6]):
        p = proj.add_point(f.id, 100 + 80 * i, 200)
        proj.add_sample(p.id, r)
    hm = H.floor_heatmap(proj, f, H.HeatmapSpec())
    assert hm.auto_boundary and len(hm.boundary) > 4
    assert len(hm.point_values) == 6 and hm.grid is not None
    assert np.isnan(hm.grid.value_at(5, 5))               # far outside the points
    assert not np.isnan(hm.grid.value_at(300, 200))
    # Uncalibrated margin = 3 % of the long side; calibrated uses feet.
    assert H.margin_px(proj, f) == pytest.approx(18)
    f.scale = G.make_scale((0, 0), (100, 0), 10, "ft")    # 10 px/ft
    assert H.margin_px(proj, f) == pytest.approx(100)
    f.boundary = [(0, 0), (600, 0), (600, 400), (0, 400)]
    full = H.floor_heatmap(proj, f, H.HeatmapSpec())
    assert not full.auto_boundary and not np.isnan(full.grid.values).any()
    none = H.floor_heatmap(proj, f, H.HeatmapSpec(flt=M.Filter({"no such ssid"})))
    assert none.grid is None and none.point_values == {}


# ----- GUI layer ------------------------------------------------------------

@pytest.fixture
def tab(qtbot, tmp_path):
    from conftest import need_qt
    need_qt()
    from PySide6.QtGui import QColor, QImage
    from wlan_scan.gui import survey_tab as ST
    from wlan_scan.recording import load_recording

    img = QImage(600, 400, QImage.Format.Format_RGB32)
    img.fill(QColor("white"))
    img.save(str(tmp_path / "p.png"))
    t = ST.SurveyTab()
    qtbot.addWidget(t)
    t.ask = lambda *a: True
    t.ask_save = lambda: False
    t.add_floor(str(tmp_path / "p.png"))
    t.scans_spin.setValue(1)
    _m, scans = load_recording(FIXTURES / "synthetic_walk.jsonl")
    for i, r in enumerate(scans[:5]):
        t.on_point_clicked(100 + 90 * i, 150 + 20 * i)
        t.on_scan_result(r)
    t.refresh_heatmap()
    return t


def test_heat_layer_drawn_and_controls(tab):
    assert tab._heat_item is not None and tab._boundary_item is not None
    assert tab._heat_item.zValue() < 0 < list(tab._items.values())[0].zValue()
    assert tab.legend.ramp is not None
    assert tab.ssid_list.count() >= 1 and tab.bssid_combo.count() >= 1
    assert not tab.bssid_combo.isEnabled()
    tab.metric_combo.setCurrentIndex(tab.metric_combo.findData("bssid_rssi"))
    assert tab.bssid_combo.isEnabled()
    assert tab.heat_settings()["metric"] == "bssid_rssi"
    tab.refresh_heatmap()
    assert tab.legend.title == M.METRICS["bssid_rssi"].label
    tab.show_heat_cb.setChecked(False)
    tab.refresh_heatmap()
    assert tab._heat_item is None
    tab.show_heat_cb.setChecked(True)
    tab.labels_cb.setChecked(True)
    assert any(it.label for it in tab._items.values())


def test_ssid_filter_and_thresholds(tab):
    from PySide6.QtCore import Qt
    first = tab.ssid_list.item(0)
    first.setCheckState(Qt.CheckState.Checked)
    assert tab.heat_settings()["ssids"] == [first.data(Qt.ItemDataRole.UserRole)]
    tab.thr_spins[0].setValue(-60)
    assert tab.project.settings["thresholds"][0] == -60
    tab.band_combo.setCurrentText("6 GHz")
    assert tab.heat_spec().flt.band == "6 GHz"


def test_draw_boundary_and_auto(tab):
    from PySide6.QtCore import QPointF
    tab.set_tool("boundary")
    assert tab.view.tool == "boundary"
    for x, y in [(0, 0), (600, 0), (600, 400), (0, 400)]:
        tab.view.click_at(QPointF(x, y))
    tab.view.finish_boundary()
    assert tab.floor.boundary == [(0, 0), (600, 0), (600, 400), (0, 400)]
    assert tab.view.tool == "pan" and "Drawn" in tab.boundary_lbl.text()
    assert not np.isnan(tab.heat.grid.values).any()
    tab.set_boundary(None)
    assert tab.floor.boundary is None and tab.heat.auto_boundary


def test_heat_settings_saved(tab, tmp_path):
    from wlan_scan.survey import project as P
    tab.opacity_slider.setValue(35)
    tab.metric_combo.setCurrentIndex(tab.metric_combo.findData("cochannel"))
    assert tab.save_as(str(tmp_path / "s.wlansurvey"))
    back = P.load(tmp_path / "s.wlansurvey")
    assert back.settings["heatmap"]["opacity"] == 35
    assert back.settings["heatmap"]["metric"] == "cochannel"
