"""F8: the synthetic survey fixture end to end — file, AP estimates against
the generator's ground truth, heatmap, and the README demo image.

Set WLANSCAN_WRITE_DOCS=1 to (re)write docs/survey-heatmap.png.
"""

import math
import os
import sys

import pytest

from conftest import FIXTURES, TESTS, need_qt

need_qt()

sys.path.insert(0, str(FIXTURES))
import make_survey_fixture as MF  # noqa: E402

from wlan_scan.survey import aps as A  # noqa: E402
from wlan_scan.survey import heatmap as H  # noqa: E402
from wlan_scan.survey import project as P  # noqa: E402
from wlan_scan.survey.metrics import Filter, point_metric  # noqa: E402

DOCS = TESTS.parent / "docs"


@pytest.fixture(scope="module")
def survey():
    return P.load(FIXTURES / "synthetic_survey.wlansurvey")


def test_fixture_is_marked_synthetic_and_complete(survey):
    assert survey.settings["_synthetic"] is True and "SYNTHETIC" in survey.site
    (f,) = survey.floors
    assert f.image_size == (MF.W, MF.H) and f.image_bytes.startswith(b"\x89PNG")
    assert f.scale is not None and f.boundary
    assert len(survey.points) == len(MF.POINTS)
    assert len(survey.samples) == len(MF.POINTS) * MF.SCANS


def test_fixture_matches_generator(survey, qapp):
    """The committed file is what make_survey_fixture.py produces (the plan
    image is compared by size only: font rendering differs between Qt builds)."""
    fresh = MF.build()
    assert fresh.to_json() == survey.to_json()
    assert [s.to_dict() for s in fresh.samples] == [s.to_dict() for s in survey.samples]


def test_round_trip_keeps_everything(survey, tmp_path):
    P.save(survey, tmp_path / "copy.wlansurvey")
    back = P.load(tmp_path / "copy.wlansurvey")
    assert back.to_json() == survey.to_json()
    assert back.floors[0].image_bytes == survey.floors[0].image_bytes
    assert [s.to_dict() for s in back.samples] == [s.to_dict() for s in survey.samples]


def test_ap_estimates_land_near_ground_truth(survey):
    import copy
    proj = copy.deepcopy(survey)
    f = proj.floors[0]
    A.refresh_estimates(proj, f)
    for name, base, (tx, ty), _c24, _c5 in MF.TRUE_APS:
        (m,) = [m for m in f.ap_markers if base in m.bssids]
        assert len(m.bssids) == 4, name                    # 2 bands × 2 SSIDs grouped
        err_ft = math.dist((m.x, m.y), (tx, ty)) / MF.PX_PER_FT
        assert err_ft < 6, f"{name} estimated {err_ft:.1f} ft from its real spot"


def test_heatmap_on_fixture(survey):
    f = survey.floors[0]
    hm = H.floor_heatmap(survey, f, H.HeatmapSpec())
    assert not hm.auto_boundary and len(hm.point_values) == len(MF.POINTS)
    west = MF.TRUE_APS[0][2]
    assert hm.grid.value_at(*west) > -55                    # strong next to an AP
    voice, data, edge = H.area_at_least(hm.grid, (-67, -70, -80))
    assert 0.5 < voice[1] < data[1] <= edge[1] == 1.0       # the demo has weak spots
    # West and Centre share 5 GHz channel 36: co-channel shows up between them.
    cc = H.floor_heatmap(survey, f, H.HeatmapSpec("cochannel", Filter(band="5 GHz")))
    assert max(cc.point_values.values()) >= 1
    guest = point_metric(survey.samples_for(survey.points[0].id), "best_rssi",
                         Filter({"Corp-Guest"}))
    assert guest is not None


def test_render_demo_image(survey, tmp_path, qapp):
    import copy
    from PySide6.QtGui import QImage
    from wlan_scan.gui.survey_export import RenderOptions, export_png

    proj = copy.deepcopy(survey)
    proj.name = "Example survey (synthetic data)"
    f = proj.floors[0]
    A.refresh_estimates(proj, f)
    out = tmp_path / "demo.png"
    export_png(out, proj, f, H.HeatmapSpec(), RenderOptions(labels=True, opacity=0.6))
    img = QImage(str(out))
    assert img.width() == MF.W and img.height() > MF.H
    if os.environ.get("WLANSCAN_WRITE_DOCS") == "1":
        DOCS.mkdir(exist_ok=True)
        export_png(DOCS / "survey-heatmap.png", proj, f, H.HeatmapSpec(),
                   RenderOptions(labels=True, opacity=0.6))
