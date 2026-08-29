"""macOS scanner adapter — drop-in for the Windows WiFiScanner interface.

Exposes MacScanner with the same surface the GUI expects:
    set_debug_callback(cb) / open() / close() / scan() / get_scan_results()
    / run_diagnostics() / detect_capability()
"""

from __future__ import annotations

import sys
from typing import Callable, Optional

from .models import ScanResult
from . import airport_scanner
from . import corewlan_scanner


class MacScanner:
    """Scan Wi-Fi networks on macOS — CoreWLAN first, airport CLI fallback."""

    def __init__(self):
        self._debug: Optional[Callable[[str], None]] = None

    def set_debug_callback(self, cb: Callable[[str], None]) -> None:
        self._debug = cb

    def open(self) -> None:
        pass  # no persistent handle needed

    def close(self) -> None:
        pass

    def scan(self) -> ScanResult:
        if corewlan_scanner.corewlan_available():
            try:
                return corewlan_scanner.scan(debug=self._debug)
            except Exception as e:
                if self._debug:
                    self._debug(f"CoreWLAN failed ({e}) — falling back to airport CLI")
        return airport_scanner.scan()

    def get_scan_results(self) -> ScanResult:
        return self.scan()

    def run_diagnostics(self) -> list[str]:
        lines = []
        if corewlan_scanner.corewlan_available():
            try:
                corewlan_scanner.scan(debug=self._debug)
                lines.append("✓ CoreWLAN scan succeeded")
            except Exception as e:
                lines.append(f"✗ CoreWLAN failed: {e}")
        try:
            airport_scanner.scan()
            lines.append("✓ airport -s scan succeeded")
        except Exception as e:
            lines.append(f"✗ airport -s failed: {e}")
        return lines

    def detect_capability(self):
        return None


def is_available() -> bool:
    """True when running on macOS (either scanner backend may work)."""
    if sys.platform != "darwin":
        return False
    return corewlan_scanner.corewlan_available() or airport_scanner._find_airport() is not None
