"""Scanner backends: one interface over every platform scanner.

Each backend implements ``ScannerBackend``. ``candidate_backends()`` returns
the ordered list to try on this platform; ``open_first()`` opens the first
one that works. The GUI worker and the CLI both go through this module, so
there is exactly one place that decides which scanner runs.

Order per platform:
    Windows x64    wlanapi (ctypes) -> PowerShell bridge -> netsh
    Windows ARM64  PowerShell bridge -> netsh   (ctypes struct passing is broken)
    macOS          CoreWLAN -> airport CLI (only where airport still exists)
    other          none — use --replay
    --replay FILE  replay only
"""

from __future__ import annotations

import platform as _platform
import sys
from typing import Callable, Optional, Protocol, runtime_checkable

from .models import ScanResult

DebugFn = Callable[[str], None]


class BackendUnavailable(RuntimeError):
    """Raised by ``open()`` when a backend cannot run on this machine."""


@runtime_checkable
class ScannerBackend(Protocol):
    name: str

    def open(self) -> None: ...
    def close(self) -> None: ...
    def scan(self) -> ScanResult: ...
    def capability(self): ...


class BaseBackend:
    """Defaults shared by the concrete backends."""

    name = "base"
    #: True when the backend's numbers are estimates (e.g. RSSI from a percent).
    estimated = False

    def __init__(self, debug: Optional[DebugFn] = None):
        self._debug = debug

    def dbg(self, msg: str) -> None:
        if self._debug:
            self._debug(msg)

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def scan(self) -> ScanResult:  # pragma: no cover - abstract
        raise NotImplementedError

    def capability(self):
        return None

    def diagnostics(self) -> list[str]:
        return []

    def request_stop(self) -> None:
        """Ask a scan in progress to return early (best effort)."""


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------


class WlanApiBackend(BaseBackend):
    name = "wlanapi"

    def __init__(self, debug: Optional[DebugFn] = None):
        super().__init__(debug)
        self._scanner = None
        self._cap = None

    def open(self) -> None:
        if sys.platform != "win32":
            raise BackendUnavailable("wlanapi is Windows-only")
        from .scanner import WiFiScanner

        s = WiFiScanner()
        s.set_debug_callback(self.dbg)
        s.open()
        diag = s.run_diagnostics()
        self._diag = diag
        for line in diag:
            self.dbg(line)
        if not any(line.startswith("✓") for line in diag):
            s.close()
            raise BackendUnavailable("wlanapi calls all failed")
        try:
            self._cap = s.detect_capability()
        except Exception as e:  # capability is informational only
            self.dbg(f"Capability detection: {e}")
        self._scanner = s

    def close(self) -> None:
        if self._scanner is not None:
            try:
                self._scanner.close()
            finally:
                self._scanner = None

    def scan(self) -> ScanResult:
        return self._scanner.get_scan_results()

    def capability(self):
        return self._cap

    def diagnostics(self) -> list[str]:
        return list(getattr(self, "_diag", []))

    def request_stop(self) -> None:
        stop = getattr(self._scanner, "request_stop", None)
        if stop is not None:
            stop()


class PwshBackend(BaseBackend):
    name = "powershell"

    def __init__(self, debug: Optional[DebugFn] = None):
        super().__init__(debug)
        self._scanner = None

    def open(self) -> None:
        if sys.platform != "win32":
            raise BackendUnavailable("PowerShell bridge is Windows-only")
        from .pwsh_scanner import PwshScanner

        self._scanner = PwshScanner()
        self._scanner.set_debug_callback(self.dbg)

    def request_stop(self) -> None:
        if self._scanner is not None:
            self._scanner.request_stop()

    def scan(self) -> ScanResult:
        result = self._scanner.scan()
        if self._scanner.last_error:
            raise RuntimeError(f"PowerShell bridge: {self._scanner.last_error}")
        return result


class NetshBackend(BaseBackend):
    name = "netsh"
    estimated = True

    def open(self) -> None:
        if sys.platform != "win32":
            raise BackendUnavailable("netsh is Windows-only")

    def scan(self) -> ScanResult:
        from . import netsh_scanner

        return netsh_scanner.scan()


# ---------------------------------------------------------------------------
# macOS
# ---------------------------------------------------------------------------


class CoreWLANBackend(BaseBackend):
    name = "corewlan"

    def open(self) -> None:
        from . import corewlan_scanner

        if not corewlan_scanner.corewlan_available():
            raise BackendUnavailable("CoreWLAN (pyobjc) not available")

    def scan(self) -> ScanResult:
        from . import corewlan_scanner

        return corewlan_scanner.scan(debug=self._debug)


class AirportBackend(BaseBackend):
    name = "airport"

    def open(self) -> None:
        from . import airport_scanner

        if sys.platform != "darwin" or airport_scanner._find_airport() is None:
            raise BackendUnavailable(
                "airport CLI not found (Apple removed it in macOS 14.4)")

    def scan(self) -> ScanResult:
        from . import airport_scanner

        return airport_scanner.scan()


# ---------------------------------------------------------------------------
# Picker
# ---------------------------------------------------------------------------

BackendFactory = Callable[[Optional[DebugFn]], BaseBackend]


def _is_arm(machine: str) -> bool:
    m = machine.lower()
    return "arm" in m or "aarch" in m


def candidate_backends(
    platform: Optional[str] = None,
    machine: Optional[str] = None,
    replay: Optional[str] = None,
) -> list[BackendFactory]:
    """Ordered backend factories to try on this platform."""
    if replay:
        from .recording import ReplayBackend

        return [lambda dbg: ReplayBackend(replay, debug=dbg)]

    platform = platform or sys.platform
    machine = machine if machine is not None else _platform.machine()

    if platform == "win32":
        if _is_arm(machine):
            return [PwshBackend, NetshBackend]
        return [WlanApiBackend, PwshBackend, NetshBackend]
    if platform == "darwin":
        return [CoreWLANBackend, AirportBackend]
    return []


def open_first(
    factories: list[BackendFactory], debug: Optional[DebugFn] = None
) -> tuple[Optional[BaseBackend], list[BackendFactory]]:
    """Open the first backend that works.

    Returns ``(backend, remaining_factories)``; backend is None when none
    could be opened. ``remaining`` are the fallbacks after the chosen one.
    """
    for i, factory in enumerate(factories):
        backend = factory(debug)
        try:
            backend.open()
        except Exception as e:
            if debug:
                debug(f"Backend {backend.name} unavailable: {e}")
            try:
                backend.close()
            except Exception:
                pass
            continue
        if debug:
            debug(f"Using scanner backend: {backend.name}")
        return backend, factories[i + 1:]
    return None, []
