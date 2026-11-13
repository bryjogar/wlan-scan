"""One scan cycle: trigger, wait for completion, read the cached BSS list.

Kept apart from the wlanapi ctypes code so the timing can be tested
without Windows. ``WiFiScanner`` plugs in its real trigger/read functions
and a ``threading.Event`` that its ``WlanRegisterNotification`` callback
sets on ``wlan_notification_acm_scan_complete`` / ``_scan_fail``.

v1 slept a fixed 2 s after every WlanScan plus a 1.5 s cooldown, so the
0.25-1 s intervals in the GUI could never happen. Now a cycle ends as
soon as the driver says the scan finished (typically 1-4 s with an
active scan), or after ``timeout`` if no notification arrives.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Generic, Optional, TypeVar

T = TypeVar("T")


@dataclass
class CycleStats:
    triggered: bool = False
    completed_by: str = ""  # "notification", "failed", "timeout", "cached", "stopped"
    duration: float = 0.0


class ScanCycle(Generic[T]):
    def __init__(
        self,
        trigger: Callable[[], bool],
        read: Callable[[], T],
        done: Optional[threading.Event] = None,
        timeout: float = 4.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.trigger = trigger
        self.read = read
        self.done = done or threading.Event()
        self.timeout = timeout
        self.clock = clock
        self.failed = False  # set by the notifier on scan_fail
        self._stop = False
        self.last = CycleStats()

    # called from the notification thread
    def notify_complete(self, failed: bool = False) -> None:
        self.failed = failed
        self.done.set()

    def request_stop(self) -> None:
        self._stop = True
        self.done.set()

    def run(self) -> T:
        start = self.clock()
        self._stop = False
        self.failed = False
        self.done.clear()
        stats = CycleStats()
        stats.triggered = bool(self.trigger())
        if stats.triggered:
            if self.done.wait(self.timeout):
                if self._stop:
                    stats.completed_by = "stopped"
                else:
                    stats.completed_by = "failed" if self.failed else "notification"
            else:
                stats.completed_by = "timeout"
        else:
            stats.completed_by = "cached"  # scan rejected: read the background list
        result = self.read()
        stats.duration = self.clock() - start
        self.last = stats
        return result


class RateMeter:
    """Smoothed time between scan completions, for the status bar."""

    def __init__(self, alpha: float = 0.3, clock: Callable[[], float] = time.monotonic):
        self.alpha = alpha
        self.clock = clock
        self._last: Optional[float] = None
        self.period: Optional[float] = None

    def tick(self) -> Optional[float]:
        now = self.clock()
        if self._last is not None:
            dt = now - self._last
            self.period = dt if self.period is None else (
                self.alpha * dt + (1 - self.alpha) * self.period)
        self._last = now
        return self.period

    def reset(self) -> None:
        self._last = None
        self.period = None
