"""F6: AP position estimates, manual markers, and the AP layer."""

import pytest

from wlan_scan.survey import aps as A
from wlan_scan.survey import project as P
from wlan_scan.survey.project import Reading, Sample, SurveyProject

AP1 = ["A0:36:BC:10:00:01", "A0:36:BC:10:00:02", "A2:36:BC:10:00:01"]   # one AP, 3 BSSIDs
AP2 = ["B4:FB:E4:01:02:03"]


def survey(positions_rssi):
    """positions_rssi: [((x, y), {bssid: rssi})]"""
    proj = SurveyProject()
    f = proj.add_floor("F1", b"x", "png", (1000, 1000))
    for (x, y), seen in positions_rssi:
        p = proj.add_point(f.id, x, y)
        proj.samples.append(Sample(p.id, "t", readings=[
            Reading(b, "Office" if b in AP1 else "Lab", r) for b, r in seen.items()]))
    return proj, f


@pytest.fixture
def two_aps():
    return survey([
        ((100, 100), {AP1[0]: -40, AP1[1]: -41, AP2[0]: -80}),
        ((200, 100), {AP1[0]: -50, AP2[0]: -75}),
        ((300, 100), {AP1[2]: -60, AP2[0]: -60}),
        ((800, 800), {AP2[0]: -38, AP1[0]: -88}),
        ((900, 800), {AP2[0]: -48}),
    ])


def test_estimates_group_bssids_and_weight_strongest(two_aps):
    proj, f = two_aps
    est = {tuple(m.bssids): m for m in A.estimate(proj, f)}
    assert set(est) == {tuple(sorted(AP1)), tuple(AP2)}
    a1 = est[tuple(sorted(AP1))]
    # Top 3 for AP1: -40 @100, -50 @200, -60 @300 → mW weights 1 : 0.1 : 0.01
    want_x = (100 * 1 + 200 * 0.1 + 300 * 0.01) / 1.11
    assert a1.x == pytest.approx(want_x) and a1.y == pytest.approx(100)
    assert a1.label.startswith("Office") and not a1.manual
    a2 = est[tuple(AP2)]
    assert 800 < a2.x < 900 and a2.y > 700


def test_weak_only_aps_are_not_placed():
    proj, f = survey([((0, 0), {AP2[0]: -90})])
    assert A.estimate(proj, f) == []


def test_manual_markers_and_moves_survive_refresh(two_aps):
    proj, f = two_aps
    A.refresh_estimates(proj, f)
    a1 = next(m for m in f.ap_markers if AP1[0] in m.bssids)
    A.move(f, a1.id, 150, 120)
    assert a1.manual
    A.refresh_estimates(proj, f)
    kept = next(m for m in f.ap_markers if m.id == a1.id)
    assert (kept.x, kept.y, kept.manual) == (150, 120, True)
    assert sum(1 for m in f.ap_markers if AP1[0] in m.bssids) == 1   # not estimated twice

    A.reset(f, a1.id)
    A.refresh_estimates(proj, f)
    back = next(m for m in f.ap_markers if AP1[0] in m.bssids)
    assert back.id == a1.id and not back.manual and back.x != 150


def test_refresh_keeps_ids_and_user_labels(two_aps):
    proj, f = two_aps
    A.refresh_estimates(proj, f)
    ids = {m.id for m in f.ap_markers}
    f.ap_markers[0].label = "Lobby"
    A.refresh_estimates(proj, f)
    assert {m.id for m in f.ap_markers} == ids
    assert "Lobby" in {m.label for m in f.ap_markers}


def test_add_manual_takes_bssids_from_estimates(two_aps):
    proj, f = two_aps
    A.refresh_estimates(proj, f)
    m = A.add_manual(f, 500, 500, "Hall", [AP2[0].lower()])
    assert m.bssids == [AP2[0]] and m.manual
    assert all(AP2[0] not in o.bssids for o in f.ap_markers if o.id != m.id)
    A.refresh_estimates(proj, f)
    assert [o for o in f.ap_markers if AP2[0] in o.bssids] == [m]


def test_delete_estimate_ignores_its_bssids(two_aps, tmp_path):
    proj, f = two_aps
    A.refresh_estimates(proj, f)
    a2 = next(m for m in f.ap_markers if AP2[0] in m.bssids)
    A.remove(f, a2.id)
    assert f.ignored_bssids == AP2
    A.refresh_estimates(proj, f)
    assert all(AP2[0] not in m.bssids for m in f.ap_markers)
    P.save(proj, tmp_path / "a.wlansurvey")
    back = P.load(tmp_path / "a.wlansurvey").floors[0]
    assert back.ignored_bssids == AP2
    assert [m.to_dict() for m in back.ap_markers] == [m.to_dict() for m in f.ap_markers]


# ----- GUI ------------------------------------------------------------------

@pytest.fixture
def tab(qtbot, tmp_path):
    from conftest import FIXTURES, need_qt
    need_qt()
    from PySide6.QtGui import QColor, QImage
    from wlan_scan.gui import survey_tab as ST
    from wlan_scan.recording import load_recording

    img = QImage(800, 400, QImage.Format.Format_RGB32)
    img.fill(QColor("white"))
    img.save(str(tmp_path / "p.png"))
    t = ST.SurveyTab()
    qtbot.addWidget(t)
    t.ask = lambda *a: True
    t.ask_save = lambda: False
    t.add_floor(str(tmp_path / "p.png"))
    t.scans_spin.setValue(1)
    _m, scans = load_recording(FIXTURES / "synthetic_walk.jsonl")
    for i, r in enumerate(scans[:11]):            # walk AP1 → AP2
        t.on_point_clicked(50 + 70 * i, 200)
        t.on_scan_result(r)
    t.refresh_heatmap()
    return t


def test_ap_layer_estimates_and_toggle(tab):
    f = tab.floor
    assert len(f.ap_markers) >= 2 and len(tab._ap_items) == len(f.ap_markers)
    assert "estimated" in tab.aps_lbl.text()
    xs = sorted(m.x for m in f.ap_markers if m.label.startswith("Office"))
    assert xs[0] < 400 < xs[-1]                    # one AP at each end of the walk
    tab.show_aps_cb.setChecked(False)
    tab.refresh_heatmap()
    assert tab._ap_items == {}


def test_place_move_edit_delete(tab):
    tab.ap_prompt = lambda choices, label, sel: ("Closet", [choices[0][0]])
    tab.set_tool("place_ap")
    from PySide6.QtCore import QPointF
    tab.view.click_at(QPointF(400, 50))
    m = next(a for a in tab.floor.ap_markers if a.label == "Closet")
    assert m.manual and (m.x, m.y) == (400, 50) and tab.view.tool == "pan"
    assert m.id in tab._ap_items

    auto = next(a for a in tab.floor.ap_markers if not a.manual)
    tab._ap_items[auto.id].setPos(10, 20)
    tab._on_ap_moved(auto.id, 10, 20)               # what a drag-release reports
    assert auto.manual and (auto.x, auto.y) == (10, 20)

    tab.ap_prompt = lambda choices, label, sel: ("Renamed", sorted(sel))
    tab.edit_ap(m.id)
    assert next(a for a in tab.floor.ap_markers if a.id == m.id).label == "Renamed"

    tab.reset_ap(auto.id)
    assert not next(a for a in tab.floor.ap_markers if a.id == auto.id).manual
    tab.delete_ap(auto.id)
    assert auto.id not in tab._ap_items and tab.floor.ignored_bssids
    tab._unignore_aps()
    assert tab.floor.ignored_bssids == []


def test_ap_item_is_draggable_and_tagged(tab):
    from PySide6.QtWidgets import QGraphicsItem
    it = next(iter(tab._ap_items.values()))
    assert it.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable
    assert it.data(0).startswith("ap-")
    tab.view.resize(800, 400)
    tab.view.fit()
    # (Estimates can overlap, so the topmost tag may be another AP.)
    assert tab.view.item_id_at(tab.view.mapFromScene(it.pos())).startswith("ap-")
