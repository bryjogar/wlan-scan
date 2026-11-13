"""R11: one version source, release-based update check, real comparison."""

import sys

import pytest

from conftest import TESTS
from wlan_scan import __version__
from wlan_scan import updater as U

sys.path.insert(0, str(TESTS.parent))
from tools import versioning as V  # noqa: E402


def test_single_source():
    from wlan_scan.version import __version__ as v
    assert __version__ == v


@pytest.mark.parametrize("desc,ver", [
    ("wlan-scan-v2.0.0", "2.0.0"),
    ("wlan-scan-v2.1.0-0-gabc1234", "2.1.0"),
    ("wlan-scan-v2.1.0-3-gabc1234", "2.1.0.post3+gabc1234"),
    ("wlan-scan-v2.1.0rc1", "2.1.0rc1"),
    ("v2.0.0", ""),
    ("", ""),
])
def test_version_from_describe(desc, ver):
    assert V.version_from_describe(desc) == ver


def test_ci_tag_env_wins(monkeypatch):
    monkeypatch.setenv("GITHUB_REF_NAME", "wlan-scan-v3.2.1")
    assert V.resolve_version()[0] == "3.2.1"


def test_bundle_version():
    assert V.bundle_version("2.1.0.post3+gabc") == "2.1.0"
    assert V.bundle_version("0.0.0+dev") == "0.0.0"


def test_write_version_file(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REF_NAME", "wlan-scan-v2.0.1")
    p = tmp_path / "version.py"
    V.write_version_file(p)
    ns = {}
    exec(p.read_text(), ns)
    assert ns["__version__"] == "2.0.1"


@pytest.mark.parametrize("a,b,newer", [
    ("2.1.0", "2.0.9", True),
    ("2.0.10", "2.0.9", True),       # numeric, not string, compare
    ("2.0.0", "2.0.0", False),
    ("1.9.0", "2.0.0", False),       # older release must not prompt
    ("wlan-scan-v2.1.0", "2.0.0", True),
    ("2.1.0", "2.1.0rc2", True),
    ("2.1.0rc2", "2.1.0", False),
    ("2.1.0", "2.1.0.post3+gabc", False),
    ("garbage", "2.0.0", False),
])
def test_is_newer(a, b, newer):
    assert U.is_newer(a, b) is newer


def _release(tag, **kw):
    return lambda: {"tag_name": tag, "html_url": f"https://example/{tag}", **kw}


def test_update_available():
    info = U.check_for_updates("2.0.0", fetch=_release("wlan-scan-v2.1.0", name="2.1"))
    assert info.latest == "2.1.0" and info.current == "2.0.0"
    assert info.url.endswith("wlan-scan-v2.1.0")


def test_no_update_when_release_is_older_or_same():
    assert U.check_for_updates("2.1.0", fetch=_release("wlan-scan-v2.0.0")) is None
    assert U.check_for_updates("2.1.0", fetch=_release("wlan-scan-v2.1.0")) is None


def test_dev_build_never_checks():
    called = []
    U.check_for_updates("0.0.0+dev", fetch=lambda: called.append(1))
    assert not called


def test_errors_and_prereleases_ignored():
    def boom():
        raise OSError("offline")
    assert U.check_for_updates("2.0.0", fetch=boom) is None
    assert U.check_for_updates("2.0.0", fetch=_release("wlan-scan-v3.0.0", prerelease=True)) is None
