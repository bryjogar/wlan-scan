import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Keep test runs out of the user's real log and cache directories.
import tempfile  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="wlanscan-tests-")
os.environ["XDG_STATE_HOME"] = os.path.join(_TMP, "state")
os.environ["XDG_CACHE_HOME"] = os.path.join(_TMP, "cache")

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))          # for ie_builder
sys.path.insert(0, str(TESTS.parent))   # for wlan_scan

FIXTURES = TESTS / "fixtures"


def need_qt():
    """Skip the calling test module when pytest-qt or Qt's libraries are missing.

    The tag-triggered release workflow installs only requirements.txt +
    pytest, so GUI tests must skip there rather than fail.
    """
    import pytest

    pytest.importorskip("pytestqt")
    pytest.importorskip("PySide6.QtWidgets")
