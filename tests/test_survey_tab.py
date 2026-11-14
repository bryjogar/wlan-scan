"""F4: the Survey tab — floors, calibration, point and walk capture, editing,
project save/open/autosave, and the main-window wiring."""

import pytest

from conftest import FIXTURES, need_qt

need_qt()

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402

from wlan_scan.gui import survey_tab as ST  # noqa: E402
from wlan_scan.recording import load_recording  # noqa: E402
from wlan_scan.survey import project as P  # noqa: E402


@pytest.fixture(scope="module")
def scans():
    return load_recording(FIXTURES / "synthetic_office.jsonl")[1]


@pytest.fixture
def plan_png(tmp_path):
    img = QImage(800, 600, QImage.Format.Format_RGB32)
    img.fill(QColor("white"))
    path = tmp_path / "plan.png"
    assert img.save(str(path))
    return path


@pytest.fixture
def tab(qtbot, plan_png):
    t = ST.SurveyTab()
    qtbot.addWidget(t)
    t.ask = lambda title, text: True
    t.ask_save = lambda: False
    t.add_floor(str(plan_png))
    return t


def test_add_floor_shows_plan_and_uncalibrated_note(tab):
    assert len(tab.project.floors) == 1 and tab.floor is not None
    assert tab.floor.image_size == (800, 600)
    assert tab.view.has_plan()
    assert tab.floor_combo.count() == 1
    assert "Not calibrated" in tab.status.text()
    assert tab.dirty


def test_tools_need_a_floor(qtbot):
    t = ST.SurveyTab()
    qtbot.addWidget(t)
    t.set_tool("point")
    assert t.view.tool == "pan"
    assert "floor plan" in t.status.text()


def test_point_mode_takes_n_scans(tab, scans):
    asked = []
    tab.scan_needed.connect(lambda: asked.append(1))
    tab.scans_spin.setValue(3)
    tab.set_tool("point")
    tab.view.click_at(QPointF(100, 200))
    (p,) = tab.project.points
    assert (p.x, p.y) == (100, 200)
    assert tab.capture is not None and asked == [1]
    assert tab._items[p.id].progress == 0
    assert tab.cancel_btn.isVisibleTo(tab)
    for r in scans[:3]:
        tab.on_scan_started()
        tab.on_scan_result(r)
    assert tab.capture is None
    assert len(tab.project.samples_for(p.id)) == 3
    assert asked == [1, 1, 1]          # one more after each scan until done
    it = tab._items[p.id]
    from wlan_scan.survey.metrics import ramp_for
    assert it.progress is None
    assert it.color == ramp_for("best_rssi").hex(tab.point_value(p.id))
    assert tab.point_value(p.id) > -50                    # strong signal at the AP
    assert "3 scan(s)" in it.toolTip()
    # A click during capture is refused, not queued.
    tab.view.click_at(QPointF(300, 300))
    tab.view.click_at(QPointF(310, 300))
    assert len(tab.project.points) == 2


def test_scan_in_flight_at_click_is_skipped(tab, scans):
    tab.scans_spin.setValue(1)
    tab.on_scan_started()                       # a scan was already running
    tab.on_point_clicked(50, 50)
    tab.on_scan_result(scans[0])
    assert tab.capture is not None and not tab.project.samples
    tab.on_scan_started()
    tab.on_scan_result(scans[1])
    assert tab.capture is None and len(tab.project.samples) == 1


def test_cancel_undo_retake_delete(tab, scans):
    tab.scans_spin.setValue(2)
    tab.on_point_clicked(10, 10)
    tab.cancel_capture()                        # no scans yet: point removed
    assert tab.project.points == [] and tab._items == {}

    a = tab.on_point_clicked(10, 10)
    tab.on_scan_result(scans[0])
    tab.cancel_capture()                        # partial: point kept with 1 scan
    assert len(tab.project.samples_for(a.id)) == 1

    tab.retake_point(a.id)
    assert tab.project.samples_for(a.id) == [] and tab.capture.point_id == a.id
    tab.on_scan_result(scans[1])
    tab.on_scan_result(scans[2])
    assert len(tab.project.samples_for(a.id)) == 2

    b = tab.on_point_clicked(20, 20)
    tab.on_scan_result(scans[0])
    tab.on_scan_result(scans[1])
    tab.undo_last_point()
    assert [p.id for p in tab.project.points] == [a.id]
    assert b.id not in tab._items
    tab.delete_point(a.id)
    assert tab.project.points == [] and tab.project.samples == []


def test_context_menu_finds_point(tab, scans):
    p = tab.on_point_clicked(400, 300)
    tab.cancel_capture()
    p = tab.on_point_clicked(400, 300)
    tab.view.resize(800, 600)
    tab.view.fit()
    vp = tab.view.mapFromScene(QPointF(400, 300))
    assert tab.view.item_id_at(vp) == p.id
    assert tab.view.item_id_at(tab.view.mapFromScene(QPointF(10, 10))) == ""


def test_calibration_via_view(tab):
    tab.calibrate_prompt = lambda px, unit: (round(px / 20, 3), "ft")   # 20 px per ft
    tab.cal_btn.setChecked(True)
    assert tab.view.tool == "calibrate"
    tab.view.click_at(QPointF(100, 100))
    tab.view.click_at(QPointF(500, 100))
    assert tab.floor.scale is not None
    assert tab.units().px_per_unit() == pytest.approx(20)
    assert "1 ft = 20.00 px" in tab.status.text()
    assert not tab.cal_btn.isChecked() and tab.view.tool == "pan"
    tab.unit_combo.setCurrentText("m")
    assert tab.project.settings["unit"] == "m"
    assert "1 m =" in tab.status.text()


def test_walk_mode(tab, scans, monkeypatch):
    clock = iter([100.0, 101.0, 102.0, 103.0, 104.0])
    monkeypatch.setattr(ST.time, "time", lambda: next(clock))
    wants = []
    tab.continuous_needed.connect(lambda: wants.append(1))
    tab.set_tool("walk")
    assert wants == [1]
    tab.view.click_at(QPointF(0, 0))            # t=100
    tab.on_scan_result(scans[0])                # t=101
    tab.on_scan_result(scans[1])                # t=102
    tab.on_scan_result(scans[2])                # t=103
    tab.view.click_at(QPointF(0, 400))          # t=104
    pts = tab.project.points
    assert [c for p in pts for c in (p.x, p.y)] == pytest.approx([0, 100, 0, 200, 0, 300])
    assert all(p.mode == "walk" for p in pts)
    assert len(tab._overlay) == 2               # dashed segment + current position
    tab.undo_last_point()                       # removes the whole segment
    assert tab.project.points == []
    tab.set_tool("pan")
    assert tab.walk is None and tab._overlay == []


def test_ping_after_point(tab, scans, monkeypatch):
    monkeypatch.setattr("wlan_scan.connection.default_gateway", lambda: "192.0.2.1")
    calls = []

    def fake_ping(target, count):
        calls.append((target, count))
        return {"target": target, "sent": count, "received": count, "loss_pct": 0.0,
                "avg_ms": 1.5}
    tab.ping_runner = fake_ping
    tab.ping_cb.setChecked(True)
    tab.scans_spin.setValue(1)
    p = tab.on_point_clicked(10, 10)
    tab.on_scan_result(scans[0])
    from PySide6.QtWidgets import QApplication
    import time as _t
    deadline = _t.monotonic() + 3
    while p.ping is None and _t.monotonic() < deadline:
        QApplication.processEvents()
    assert calls == [("192.0.2.1", 5)]
    assert p.ping["avg_ms"] == 1.5
    assert "Ping 192.0.2.1: 1.5 ms, 0% loss" in tab.point_summary(p.id)


def test_floors_switch_points(tab, plan_png, scans):
    first = tab.floor_id
    tab.scans_spin.setValue(1)
    tab.on_point_clicked(1, 1)
    tab.on_scan_result(scans[0])
    second = tab.add_floor(str(plan_png), name="Upstairs")
    assert tab.floor_id == second.id and tab._items == {}
    assert tab.floor_combo.currentText() == "Upstairs"
    tab.floor_combo.setCurrentIndex(0)
    assert tab.floor_id == first and len(tab._items) == 1


def test_save_open_autosave_recover(tab, scans, tmp_path, qtbot):
    tab.scans_spin.setValue(2)
    p = tab.on_point_clicked(30, 40)
    tab.on_scan_result(scans[0])
    tab.on_scan_result(scans[1])
    target = tmp_path / "site"
    assert tab.save_as(str(target))
    saved = tmp_path / "site.wlansurvey"
    assert tab.path == saved and saved.is_file() and not tab.dirty
    assert tab.autosave_now() is None            # nothing to autosave when clean

    tab.delete_point(p.id)
    side = tab.autosave_now()
    assert side == P.autosave_path(saved) and side.is_file()

    t2 = ST.SurveyTab()
    qtbot.addWidget(t2)
    t2.ask = lambda title, text: False           # don't recover: the saved file
    assert t2.open_project(str(saved))
    assert len(t2.project.points) == 1 and not t2.dirty
    assert t2.floor.image_size == (800, 600) and len(t2._items) == 1

    t3 = ST.SurveyTab()
    qtbot.addWidget(t3)
    t3.ask = lambda title, text: True            # recover the autosave
    assert t3.open_project(str(saved))
    assert t3.project.points == [] and t3.dirty and t3.path == saved
    assert t3.save()
    assert not P.autosave_path(saved).exists()


def test_maybe_save_cancel_keeps_project(tab):
    tab.ask_save = lambda: None
    proj = tab.project
    tab.new_project()
    assert tab.project is proj
    tab.ask_save = lambda: False
    tab.new_project()
    assert tab.project is not proj and tab.project.floors == []


def test_untitled_recovery_on_first_show(qtbot, tmp_path):
    proj = P.SurveyProject(name="Lost work")
    P.autosave(proj, None)
    t = ST.SurveyTab()
    qtbot.addWidget(t)
    t.ask = lambda title, text: True
    t.check_recovery()
    assert t.project.name == "Lost work" and t.dirty
    P.discard_autosave(None)


def test_main_window_feeds_survey(qtbot, plan_png):
    from wlan_scan.gui import main_window

    win = main_window.MainWindow(replay=str(FIXTURES / "synthetic_office.jsonl"),
                                 check_updates=False)
    qtbot.addWidget(win)
    tab = win._survey_tab
    tab.ask = lambda title, text: False
    tab.ask_save = lambda: False
    assert win._tabs.count() == 2 and win._tabs.tabText(1) == "Survey"
    win._auto_scan_cb.setChecked(False)          # paused: each point asks for scans
    qtbot.waitUntil(lambda: win._worker.isRunning() and win._worker.paused, timeout=3000)
    qtbot.waitUntil(lambda: not tab._scan_in_flight, timeout=3000)
    tab.add_floor(str(plan_png))
    tab.scans_spin.setValue(2)
    p = tab.on_point_clicked(100, 100)
    qtbot.waitUntil(lambda: tab.capture is None, timeout=5000)
    assert len(tab.project.samples_for(p.id)) == 2
    tab.set_tool("walk")                         # walk turns Auto-scan back on
    assert win._auto_scan_cb.isChecked()
    win.close()
