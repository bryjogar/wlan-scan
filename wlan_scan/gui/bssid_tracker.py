"""BSSID tracker with RSSI-based beeping for site surveys.

Maps RSSI to beep interval — stronger signal = faster beeps.
Useful for walking a site and finding APs by ear.
"""

import sys
import time
from PySide6.QtCore import Qt, QObject, QTimer, Signal, Slot


class BSSIDTracker(QObject):
    """Track a specific BSSID across scans and beep at RSSI-derived intervals."""

    # (rssi_threshold, beep_interval_ms, beep_freq_hz)
    # Closer = faster beeps at higher pitch (like a proximity sonar)
    RSSI_BEEP_MAP = [
        (-30,  120, 1200),  # Excellent: rapid high chirps
        (-40,  200, 1050),  # Very strong
        (-50,  350,  900),  # Strong
        (-57,  500,  780),  # Good
        (-63,  700,  660),  # Fair
        (-70, 1000,  550),  # Weak
        (-78, 1600,  440),  # Poor
        (-86, 2400,  350),  # Very poor
        (-94, 3500,  300),  # Barely there
    ]
    CUTOFF = -95
    SMOOTH_ALPHA = 0.75  # Fast response — reach 94% of target in 2 scans

    # Signal: bssid, rssi, found (bool)
    status_changed = Signal(str, int, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bssid: str | None = None
        self._raw_rssi: int = -100
        self._smooth_rssi: float = -100.0
        self._found: bool = False
        self._miss_count: int = 0
        self._beep_timer = QTimer(self)
        self._beep_timer.setTimerType(Qt.PreciseTimer)
        self._beep_timer.timeout.connect(self._do_beep)
        self._last_beep = 0.0

    # ── public API ──

    def track(self, bssid: str):
        """Start tracking a BSSID."""
        self._bssid = bssid
        self._raw_rssi = -100
        self._smooth_rssi = -100.0
        self._found = False
        self._miss_count = 0
        self._start_beeping()
        self.status_changed.emit(bssid, -100, False)

    def untrack(self):
        """Stop tracking."""
        self._beep_timer.stop()
        self._bssid = None
        self._raw_rssi = -100
        self._smooth_rssi = -100.0
        self._found = False
        self._miss_count = 0
        self.status_changed.emit("", -100, False)

    @property
    def bssid(self) -> str | None:
        return self._bssid

    @property
    def is_tracking(self) -> bool:
        return self._bssid is not None

    @property
    def rssi(self) -> int:
        return int(self._smooth_rssi)

    @property
    def found(self) -> bool:
        return self._found

    @property
    def beep_interval_ms(self) -> int:
        """Current beep interval in ms, or 0 if not beeping."""
        return self._beep_timer.interval() if self._beep_timer.isActive() else 0

    # ── called by main window on each scan ──

    def feed_scan(self, bss_list: list):
        """Feed a list of (bssid, rssi, ssid) tuples from the latest scan."""
        if not self._bssid:
            return

        match = None
        for bssid, rssi, ssid in bss_list:
            if bssid == self._bssid:
                match = (ssid, rssi)
                break

        if match:
            ssid, rssi = match
            self._raw_rssi = rssi
            self._found = True
            self._miss_count = 0
            # Simple EMA smoothing — fast alpha, converges in 2-3 scans
            if self._smooth_rssi <= -99:
                self._smooth_rssi = float(rssi)
            else:
                self._smooth_rssi = (
                    self.SMOOTH_ALPHA * rssi +
                    (1 - self.SMOOTH_ALPHA) * self._smooth_rssi
                )
            self._update_beep_interval()
            self.status_changed.emit(self._bssid, int(self._smooth_rssi), True)
        else:
            self._miss_count += 1
            if self._miss_count >= 3:
                self._found = False
                self._beep_timer.stop()
                self.status_changed.emit(self._bssid, int(self._smooth_rssi), False)

    # ── internal ──

    def _rssi_to_params(self, rssi: float) -> tuple[int, int]:
        """Return (interval_ms, freq_hz) for the given smoothed RSSI."""
        if rssi < self.CUTOFF:
            return 0, 0
        for threshold, interval, freq in self.RSSI_BEEP_MAP:
            if rssi >= threshold:
                return interval, freq
        return self.RSSI_BEEP_MAP[-1][1], self.RSSI_BEEP_MAP[-1][2]

    def _start_beeping(self):
        interval, _ = self._rssi_to_params(self._smooth_rssi)
        if interval > 0:
            self._beep_timer.start(interval)
            self._schedule_immediate_beep()

    def _update_beep_interval(self):
        interval, _ = self._rssi_to_params(self._smooth_rssi)
        if interval <= 0:
            self._beep_timer.stop()
            return
        self._beep_timer.start(interval)
        now = time.monotonic()
        if now - self._last_beep >= interval / 1000.0:
            self._schedule_immediate_beep()

    def _schedule_immediate_beep(self):
        """Queue a single immediate beep without blocking the timer cycle."""
        QTimer.singleShot(10, self._do_beep)  # 10ms defer to avoid reentrancy

    def _do_beep(self):
        self._last_beep = time.monotonic()
        _, freq = self._rssi_to_params(self._smooth_rssi)
        if freq <= 0:
            return
        try:
            if sys.platform == "win32":
                import winsound
                winsound.Beep(freq, 50)
            else:
                print("\a", end="", flush=True)
        except Exception:
            pass

