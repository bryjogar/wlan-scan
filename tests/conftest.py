import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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
