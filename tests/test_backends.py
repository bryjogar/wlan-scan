"""Backend picker and fallback (no real scanners needed)."""

from wlan_scan import backends as bk
from wlan_scan.backends import BaseBackend, BackendUnavailable, candidate_backends, open_first
from wlan_scan.models import ScanResult


def _names(factories):
    return [f.name if isinstance(f, type) else f(None).name for f in factories]


def test_windows_x64_order():
    assert _names(candidate_backends("win32", "AMD64")) == ["wlanapi", "powershell", "netsh"]


def test_windows_arm_skips_ctypes():
    assert _names(candidate_backends("win32", "ARM64")) == ["powershell", "netsh"]


def test_macos_order():
    assert _names(candidate_backends("darwin", "arm64")) == ["corewlan", "airport"]


def test_replay_overrides_platform(tmp_path):
    f = tmp_path / "r.jsonl"
    f.write_text("")
    assert _names(candidate_backends("win32", "AMD64", replay=str(f))) == ["replay"]


class _Broken(BaseBackend):
    name = "broken"

    def open(self):
        raise BackendUnavailable("nope")


class _Works(BaseBackend):
    name = "works"

    def scan(self):
        return ScanResult()


def test_open_first_falls_back_and_reports():
    msgs = []
    backend, rest = open_first([_Broken, _Works, _Broken], debug=msgs.append)
    assert backend.name == "works"
    assert len(rest) == 1
    assert any("broken unavailable" in m for m in msgs)
    assert any("Using scanner backend: works" in m for m in msgs)


def test_open_first_none_available():
    backend, rest = open_first([_Broken])
    assert backend is None and rest == []


def test_platform_backends_refuse_off_platform():
    # On this Linux test host every real backend must refuse cleanly.
    for cls in (bk.WlanApiBackend, bk.PwshBackend, bk.NetshBackend,
                bk.CoreWLANBackend, bk.AirportBackend):
        backend, _ = open_first([cls])
        assert backend is None
