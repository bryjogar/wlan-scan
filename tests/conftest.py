import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))          # for ie_builder
sys.path.insert(0, str(TESTS.parent))   # for wlan_scan

FIXTURES = TESTS / "fixtures"
