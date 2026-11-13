"""Core Wi-Fi scanning engine via Windows WLAN API (wlanapi.dll).

This module wraps the Native Wifi API using ctypes to scan for wireless
networks, retrieve BSS details, and parse raw 802.11 information elements.

Reference: https://learn.microsoft.com/en-us/windows/win32/api/wlanapi/
"""

import ctypes
import time
import sys
from ctypes import wintypes
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from .models import BSSEntry, ScanResult, group_networks
from .scan_cycle import ScanCycle
from .bss import build_bss_entry
from .channels import freq_to_channel as _freq_to_channel_mhz
from .vendors import lookup_vendor

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WLAN_API_VERSION_2_0 = 2
ERROR_SUCCESS = 0
DOT11_SSID_MAX_LENGTH = 32
WLAN_MAX_PHY_TYPE_NUMBER = 8

# DOT11_BSS_TYPE
DOT11_BSS_TYPE_INFRASTRUCTURE = 1
DOT11_BSS_TYPE_INDEPENDENT = 2
DOT11_BSS_TYPE_ANY = 3

# Common error codes
_WLAN_ERRORS = {
    0x00000000: "SUCCESS",
    0x00000002: "FILE_NOT_FOUND",
    0x00000005: "ACCESS_DENIED",
    0x00000057: "INVALID_PARAMETER",
    0x0000007A: "BUFFER_TOO_SMALL",
    0x0000045A: "RPC_SERVER_UNAVAILABLE",
    0x00000490: "ERROR_NOT_FOUND — WiFi interface not ready, Location Services disabled, or scan in progress",
    0x0000139F: "INVALID_STATE",
    0x00138000: "WLAN_NOTIFICATION",
    0x00210002: "WLAN_SERVICE_NOT_RUNNING",
    0x00210004: "WLAN_AUTO_CONFIG_SERVICE_NOT_RUNNING",
    0x00210005: "INVALID_HANDLE_STATE",
    0x00210008: "INTERFACE_NOT_READY",
    0x00210020: "NOT_SUPPORTED",
    0x00210026: "WLAN_NO_SCAN_RESULTS",
}


def _wlan_error_string(code: int) -> str:
    """Translate a WLAN API error code to a human-readable string."""
    return _WLAN_ERRORS.get(code, f"0x{code:08X}")


# ---------------------------------------------------------------------------
# Structures
# ---------------------------------------------------------------------------


# Fixed-width field types: wintypes.DWORD/ULONG are c_ulong, which is 8 bytes
# off Windows, so these keep the layout identical (and testable) everywhere.
_DWORD = ctypes.c_uint32
_ULONG = ctypes.c_uint32
_LONG = ctypes.c_int32
_WORD = ctypes.c_uint16
_USHORT = ctypes.c_uint16
_BOOL = ctypes.c_int32
_WCHAR = ctypes.c_wchar if ctypes.sizeof(ctypes.c_wchar) == 2 else ctypes.c_uint16


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", _DWORD),
        ("Data2", _WORD),
        ("Data3", _WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    def __str__(self):
        return (
            f"{self.Data1:08X}-{self.Data2:04X}-{self.Data3:04X}-"
            f"{self.Data4[0]:02X}{self.Data4[1]:02X}-"
            f"{''.join(f'{b:02X}' for b in self.Data4[2:])}"
        )


class DOT11_SSID(ctypes.Structure):
    _fields_ = [
        ("uSSIDLength", _ULONG),
        ("ucSSID", ctypes.c_ubyte * DOT11_SSID_MAX_LENGTH),
    ]


class WLAN_INTERFACE_INFO(ctypes.Structure):
    _fields_ = [
        ("InterfaceGuid", GUID),
        ("strInterfaceDescription", _WCHAR * 256),
        ("isState", _DWORD),
    ]


class WLAN_INTERFACE_INFO_LIST(ctypes.Structure):
    _fields_ = [
        ("dwNumberOfItems", _DWORD),
        ("dwIndex", _DWORD),
        ("InterfaceInfo", WLAN_INTERFACE_INFO * 1),
    ]


class WLAN_AVAILABLE_NETWORK(ctypes.Structure):
    """wlanapi.h WLAN_AVAILABLE_NETWORK (628 bytes)."""
    _fields_ = [
        ("strProfileName", _WCHAR * 256),
        ("dot11Ssid", DOT11_SSID),
        ("dot11BssType", _DWORD),
        ("uNumberOfBssids", _ULONG),
        ("bNetworkConnectable", _BOOL),
        ("wlanNotConnectableReason", _DWORD),
        ("uNumberOfPhyTypes", _ULONG),
        ("dot11PhyTypes", _DWORD * WLAN_MAX_PHY_TYPE_NUMBER),
        ("bMorePhyTypes", _BOOL),
        ("wlanSignalQuality", _ULONG),
        ("bSecurityEnabled", _BOOL),
        ("dot11DefaultAuthAlgorithm", _DWORD),
        ("dot11DefaultCipherAlgorithm", _DWORD),
        ("dwFlags", _DWORD),
        ("dwReserved", _DWORD),
    ]


class WLAN_AVAILABLE_NETWORK_LIST(ctypes.Structure):
    _fields_ = [
        ("dwNumberOfItems", _DWORD),
        ("dwIndex", _DWORD),
        ("Network", WLAN_AVAILABLE_NETWORK * 1),
    ]


DOT11_RATE_SET_MAX_LENGTH = 126


class WLAN_RATE_SET(ctypes.Structure):
    """uRateSetLength is in *bytes*; each rate is in 500 kbps units, bit 15 = basic."""
    _fields_ = [
        ("uRateSetLength", _ULONG),
        ("usRateSet", _USHORT * DOT11_RATE_SET_MAX_LENGTH),
    ]

    def max_mbps(self) -> float:
        n = min(self.uRateSetLength // 2, DOT11_RATE_SET_MAX_LENGTH)
        rates = [(self.usRateSet[i] & 0x7FFF) * 0.5 for i in range(n)]
        return max(rates, default=0.0)


class WLAN_BSS_ENTRY(ctypes.Structure):
    """wlanapi.h WLAN_BSS_ENTRY (360 bytes).

    v1 declared wlanRateSet as 126 bytes and bInRegDomain as a 4-byte BOOL,
    making the struct 232 bytes: every entry after the first was read at the
    wrong stride. Layout is pinned by tests/test_wlanapi_layout.py.
    """
    _fields_ = [
        ("dot11Ssid", DOT11_SSID),
        ("uPhyId", _ULONG),
        ("dot11Bssid", ctypes.c_ubyte * 6),
        ("dot11BssType", _DWORD),
        ("dot11BssPhyType", _DWORD),
        ("lRssi", _LONG),
        ("uLinkQuality", _ULONG),
        ("bInRegDomain", ctypes.c_ubyte),  # BOOLEAN (1 byte)
        ("usBeaconPeriod", _USHORT),
        ("ullTimestamp", ctypes.c_ulonglong),
        ("ullHostTimestamp", ctypes.c_ulonglong),
        ("usCapabilityInformation", _USHORT),
        ("ulChCenterFrequency", _ULONG),
        ("wlanRateSet", WLAN_RATE_SET),
        ("ulIeOffset", _ULONG),  # from the start of *this entry*
        ("ulIeSize", _ULONG),
    ]


class WLAN_BSS_LIST(ctypes.Structure):
    _fields_ = [
        ("dwTotalSize", _DWORD),
        ("dwNumberOfItems", _DWORD),
        ("wlanBssEntries", WLAN_BSS_ENTRY * 1),
    ]


# ---------------------------------------------------------------------------
# PHY type → human-readable
# ---------------------------------------------------------------------------

class WLAN_NOTIFICATION_DATA(ctypes.Structure):
    _fields_ = [
        ("NotificationSource", _DWORD),
        ("NotificationCode", _DWORD),
        ("InterfaceGuid", GUID),
        ("dwDataSize", _DWORD),
        ("pData", ctypes.c_void_p),
    ]


WLAN_NOTIFICATION_SOURCE_NONE = 0x00000000
WLAN_NOTIFICATION_SOURCE_ACM = 0x00000008
WLAN_NOTIFICATION_ACM_SCAN_COMPLETE = 7
WLAN_NOTIFICATION_ACM_SCAN_FAIL = 8

_CALLBACK_FACTORY = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
WLAN_NOTIFICATION_CALLBACK = _CALLBACK_FACTORY(
    None, ctypes.POINTER(WLAN_NOTIFICATION_DATA), ctypes.c_void_p)


def is_scan_done_notification(data: WLAN_NOTIFICATION_DATA,
                              guid: Optional["GUID"]) -> Optional[bool]:
    """None if unrelated; False = scan complete; True = scan failed."""
    if data.NotificationSource != WLAN_NOTIFICATION_SOURCE_ACM:
        return None
    if guid is not None and bytes(data.InterfaceGuid) != bytes(guid):
        return None
    if data.NotificationCode == WLAN_NOTIFICATION_ACM_SCAN_COMPLETE:
        return False
    if data.NotificationCode == WLAN_NOTIFICATION_ACM_SCAN_FAIL:
        return True
    return None


# DOT11_PHY_TYPE values: see bss.PHY_TYPE_NAMES (v1 had 6..9 off by one).


# ---------------------------------------------------------------------------
# Channel conversion
# ---------------------------------------------------------------------------


def freq_to_channel(freq_khz: int) -> tuple[int, str]:
    """Convert centre frequency (kHz, as wlanapi reports it) to (channel, band)."""
    return _freq_to_channel_mhz(freq_khz / 1000.0)


# ---------------------------------------------------------------------------
# wlanapi.dll loader (Windows only)
# ---------------------------------------------------------------------------

_wlanapi = None


def _load_wlanapi():
    """Load wlanapi.dll and set up function signatures."""
    global _wlanapi
    if _wlanapi is not None:
        return _wlanapi

    if sys.platform != "win32":
        raise OSError("Native WiFi scanning is only available on Windows.")

    _wlanapi = ctypes.windll.wlanapi

    # WlanOpenHandle
    _wlanapi.WlanOpenHandle.argtypes = [
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.HANDLE),
    ]
    _wlanapi.WlanOpenHandle.restype = wintypes.DWORD

    # WlanCloseHandle
    _wlanapi.WlanCloseHandle.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    _wlanapi.WlanCloseHandle.restype = wintypes.DWORD

    # WlanEnumInterfaces
    _wlanapi.WlanEnumInterfaces.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.POINTER(WLAN_INTERFACE_INFO_LIST)),
    ]
    _wlanapi.WlanEnumInterfaces.restype = wintypes.DWORD

    # WlanScan
    _wlanapi.WlanScan.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(GUID),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    _wlanapi.WlanScan.restype = wintypes.DWORD

    # WlanGetNetworkBssList
    _wlanapi.WlanGetNetworkBssList.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(GUID),
        ctypes.POINTER(DOT11_SSID),
        wintypes.DWORD,
        wintypes.BOOL,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.POINTER(WLAN_BSS_LIST)),
    ]
    _wlanapi.WlanGetNetworkBssList.restype = wintypes.DWORD

    # WlanGetAvailableNetworkList (simpler — no BSS details, but reliable)
    _wlanapi.WlanGetAvailableNetworkList.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(GUID),
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.POINTER(WLAN_AVAILABLE_NETWORK_LIST)),
    ]
    _wlanapi.WlanGetAvailableNetworkList.restype = wintypes.DWORD

    # WlanRegisterNotification
    _wlanapi.WlanRegisterNotification.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.BOOL,
        WLAN_NOTIFICATION_CALLBACK,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _wlanapi.WlanRegisterNotification.restype = wintypes.DWORD

    # WlanFreeMemory
    _wlanapi.WlanFreeMemory.argtypes = [ctypes.c_void_p]
    _wlanapi.WlanFreeMemory.restype = None

    # WlanGetInterfaceCapability
    _wlanapi.WlanGetInterfaceCapability.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(GUID),
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(ctypes.POINTER(WLAN_INTERFACE_CAPABILITY)),
    ]
    _wlanapi.WlanGetInterfaceCapability.restype = wintypes.DWORD

    return _wlanapi


# ---------------------------------------------------------------------------
# Scanner class
# ---------------------------------------------------------------------------


class WLAN_INTERFACE_CAPABILITY(ctypes.Structure):
    """WLAN_INTERFACE_CAPABILITY structure from wlanapi.h."""
    _fields_ = [
        ("interfaceType", _DWORD),
        ("bDot11DSupported", _BOOL),
        ("dwMaxDesiredSsidListSize", _DWORD),
        ("dwMaxDesiredBssidListSize", _DWORD),
        ("dwNumberOfSupportedPhys", _DWORD),
        ("dot11PhyTypes", _DWORD * 64),
    ]


@dataclass
class InterfaceCapability:
    """Human-readable interface capability summary."""
    supported_bands: list[str] = field(default_factory=list)
    supported_generations: list[str] = field(default_factory=list)  # e.g. "Wi-Fi 6 (802.11ax)"
    phy_type_ids: list[int] = field(default_factory=list)
    max_bssid_list_size: int = 0
    missing_6ghz: bool = False
    missing_5ghz: bool = False

    def summary(self) -> str:
        bands = " + ".join(self.supported_bands) if self.supported_bands else "Unknown"
        gens = ", ".join(self.supported_generations) if self.supported_generations else "Unknown"
        notes = []
        if self.missing_6ghz:
            notes.append("⚠ no 6 GHz")
        if self.missing_5ghz:
            notes.append("⚠ no 5 GHz")
        note_str = " | ".join(notes) if notes else ""
        return f"{bands} · {gens}  {note_str}".strip()


# Keyed by DOT11_PHY_TYPE: 4 ofdm, 5 hrdsss, 6 erp, 7 ht, 8 vht, 9 dmg, 10 he, 11 eht
PHY_BAND_MAP: dict[int, list[str]] = {
    4: ["5 GHz"],     # 802.11a
    5: ["2.4 GHz"],   # 802.11b
    6: ["2.4 GHz"],   # 802.11g (ERP)
    7: ["2.4 GHz", "5 GHz"],  # 802.11n (HT) — dual-band possible
    8: ["5 GHz"],     # 802.11ac (VHT)
    10: ["2.4 GHz", "5 GHz", "6 GHz"],  # 802.11ax (HE) — tri-band possible
    11: ["2.4 GHz", "5 GHz", "6 GHz"],  # 802.11be (EHT) — tri-band possible
}

PHY_GENERATION_MAP: dict[int, str] = {
    4: "Wi-Fi 2 (802.11a)",
    5: "Wi-Fi 1 (802.11b)",
    6: "Wi-Fi 3 (802.11g)",
    7: "Wi-Fi 4 (802.11n)",
    8: "Wi-Fi 5 (802.11ac)",
    10: "Wi-Fi 6 (802.11ax)",
    11: "Wi-Fi 7 (802.11be)",
}


def _band_sort_key(band: str) -> int:
    """Sort bands in logical order: 2.4 → 5 → 6."""
    if "2.4" in band:
        return 0
    if "5" in band:
        return 1
    if "6" in band:
        return 2
    return 99


def _gen_rank(gen_label: str) -> int:
    """Extract Wi-Fi generation number from label for comparison."""
    import re
    m = re.search(r'Wi-Fi (\d+)', gen_label)
    return int(m.group(1)) if m else 0


class WiFiScanner:
    """Windows Native WiFi scanner using wlanapi.dll."""

    def __init__(self):
        self._api = _load_wlanapi()
        self._handle: Optional[wintypes.HANDLE] = None
        self._interface_guid: Optional[GUID] = None
        self._interface_name: str = ""
        self._cycle = ScanCycle(self.trigger_scan, self._read_cached, timeout=4.0)
        self._notify_cb = None  # keep the ctypes callback alive while registered
        self._on_debug: Optional[Callable[[str], None]] = None
        self._capability: Optional[InterfaceCapability] = None

    # ----- handle management -----

    def open(self):
        """Open WLAN API handle and find the first wireless interface."""
        if self._handle is not None:
            return

        handle = wintypes.HANDLE()
        negotiated = wintypes.DWORD()

        result = self._api.WlanOpenHandle(
            WLAN_API_VERSION_2_0,
            None,
            ctypes.byref(negotiated),
            ctypes.byref(handle),
        )
        if result != ERROR_SUCCESS:
            raise RuntimeError(
                f"WlanOpenHandle failed: {_wlan_error_string(result)}")
        self._dbg(f"WlanOpenHandle OK — requested v2, negotiated v{negotiated.value}")

        self._handle = handle

        # Enumerate interfaces
        p_iface_list = ctypes.POINTER(WLAN_INTERFACE_INFO_LIST)()
        result = self._api.WlanEnumInterfaces(
            self._handle, None, ctypes.byref(p_iface_list)
        )
        if result != ERROR_SUCCESS:
            self.close()
            raise RuntimeError(
                f"WlanEnumInterfaces failed: {_wlan_error_string(result)}")

        n_ifaces = p_iface_list.contents.dwNumberOfItems
        self._dbg(f"WlanEnumInterfaces: {n_ifaces} interface(s)")

        if n_ifaces == 0:
            self._api.WlanFreeMemory(p_iface_list)
            self.close()
            raise RuntimeError("No wireless interfaces found.")

        # Log interface details
        for idx in range(n_ifaces):
            iface_array = (WLAN_INTERFACE_INFO * n_ifaces).from_address(
                ctypes.addressof(p_iface_list.contents.InterfaceInfo)
            )
            info = iface_array[idx]
            state_names = {
                0: "not_ready", 1: "connected", 2: "ad_hoc",
                3: "disconnecting", 4: "disconnected",
                5: "associating", 6: "discovering", 7: "authenticating",
            }
            state = state_names.get(info.isState, f"unknown({info.isState})")
            self._dbg(f"  Interface[{idx}]: state={state} "
                      f"desc={info.strInterfaceDescription}")

        iface = p_iface_list.contents.InterfaceInfo[0]
        self._interface_guid = iface.InterfaceGuid
        self._interface_name = iface.strInterfaceDescription
        self._dbg(f"Selected: {self._interface_name} (state={state})")

        self._api.WlanFreeMemory(p_iface_list)

        # If negotiated version < 2, try re-opening with v1
        if negotiated.value < 2:
            self._dbg(f"Negotiated v{negotiated.value} < 2 — "
                      f"limited functionality")
        if negotiated.value == 0:
            self._dbg("WARNING: Negotiated version 0 — API may not work!")

        self._register_notifications()

    def close(self):
        """Close the WLAN API handle."""
        if self._handle is not None:
            try:
                self._unregister_notifications()
            except Exception:
                pass
            self._api.WlanCloseHandle(self._handle, None)
            self._handle = None

    @property
    def interface_name(self) -> str:
        return self._interface_name

    def set_debug_callback(self, callback: Callable[[str], None]):
        """Set a callback for debug messages."""
        self._on_debug = callback

    def _dbg(self, msg: str):
        if self._on_debug:
            self._on_debug(msg)

    @property
    def last_cycle(self):
        """Timing of the last scan: triggered, completed_by, duration."""
        return self._cycle.last

    def request_stop(self) -> None:
        """Make a scan waiting for its completion notification return now."""
        self._cycle.request_stop()

    # ----- scan-complete notifications -----

    def _register_notifications(self) -> None:
        def on_notify(p_data, _ctx):
            try:
                failed = is_scan_done_notification(p_data.contents, self._interface_guid)
                if failed is not None:
                    self._cycle.notify_complete(failed)
            except Exception:
                pass  # never raise into wlanapi's thread

        cb = WLAN_NOTIFICATION_CALLBACK(on_notify)
        prev = wintypes.DWORD()
        r = self._api.WlanRegisterNotification(
            self._handle, WLAN_NOTIFICATION_SOURCE_ACM, True, cb, None, None,
            ctypes.byref(prev))
        if r == ERROR_SUCCESS:
            self._notify_cb = cb
            self._dbg("Scan-complete notifications registered")
        else:
            self._dbg(f"WlanRegisterNotification failed: {_wlan_error_string(r)} "
                      f"— scans will wait up to {self._cycle.timeout:.0f} s")

    def _unregister_notifications(self) -> None:
        if self._notify_cb is None or self._handle is None:
            return
        prev = wintypes.DWORD()
        self._api.WlanRegisterNotification(
            self._handle, WLAN_NOTIFICATION_SOURCE_NONE, True,
            WLAN_NOTIFICATION_CALLBACK(), None, None, ctypes.byref(prev))
        self._notify_cb = None

    @property
    def capability(self) -> Optional[InterfaceCapability]:
        return self._capability

    def detect_capability(self) -> InterfaceCapability:
        """Query WlanGetInterfaceCapability and return a human-readable summary.

        Returns InterfaceCapability even on failure (with empty fields),
        so callers can always call .summary().
        """
        cap = InterfaceCapability()
        if self._handle is None or self._interface_guid is None:
            self._dbg("Cannot detect capability: no active handle/interface")
            return cap

        p_cap = ctypes.POINTER(WLAN_INTERFACE_CAPABILITY)()
        negotiated = wintypes.DWORD()
        result = self._api.WlanGetInterfaceCapability(
            self._handle,
            ctypes.byref(self._interface_guid),
            None,
            ctypes.byref(negotiated),
            ctypes.byref(p_cap),
        )
        if result != ERROR_SUCCESS:
            self._dbg(f"WlanGetInterfaceCapability failed: {_wlan_error_string(result)}")
            return cap

        try:
            iface_cap = p_cap.contents
            cap.max_bssid_list_size = iface_cap.dwMaxDesiredBssidListSize
            cap.phy_type_ids = [
                iface_cap.dot11PhyTypes[i]
                for i in range(iface_cap.dwNumberOfSupportedPhys)
            ]

            # Determine supported bands
            bands: set[str] = set()
            for phy_id in cap.phy_type_ids:
                band_list = PHY_BAND_MAP.get(phy_id, [])
                for b in band_list:
                    bands.add(b)
            cap.supported_bands = sorted(bands, key=_band_sort_key)

            # Determine supported Wi-Fi generations (highest per band)
            gens: dict[str, str] = {}
            for phy_id in cap.phy_type_ids:
                gen_label = PHY_GENERATION_MAP.get(phy_id)
                if not gen_label:
                    continue
                for band in PHY_BAND_MAP.get(phy_id, []):
                    existing = gens.get(band, "")
                    if _gen_rank(gen_label) > _gen_rank(existing):
                        gens[band] = gen_label
            cap.supported_generations = [
                f"{band}: {gen}"
                for band in sorted(gens.keys(), key=_band_sort_key)
                for gen in [gens[band]]
            ]

            # Detect missing bands that modern adapters should have
            # Only flag 5 GHz missing if we see Wi-Fi 4+ PHY types (modern adapter)
            has_modern = any(p >= 7 for p in cap.phy_type_ids)
            if has_modern:
                cap.missing_5ghz = "5 GHz" not in bands
                cap.missing_6ghz = "6 GHz" not in bands

            self._dbg(f"Capability: {cap.summary()}")
            self._capability = cap
        finally:
            self._api.WlanFreeMemory(p_cap)

        return cap

    def run_diagnostics(self) -> list[str]:
        """Run diagnostic checks and return log lines.

        On Qualcomm FastConnect 7800 (ARM64 Snapdragon), wlanapi calls
        often fail on first attempt and succeed on retry.
        """
        lines = []
        max_retries = 4  # try up to 4 times with increasing delays

        # 1. Try WlanGetAvailableNetworkList (lightweight, no scan needed)
        for attempt in range(max_retries):
            if attempt > 0:
                time.sleep(0.5 * attempt)
            p_list = ctypes.POINTER(WLAN_AVAILABLE_NETWORK_LIST)()
            flags = 0x00000003  # ALL_ADHOC | ALL_MANUAL_HIDDEN
            result = self._api.WlanGetAvailableNetworkList(
                self._handle,
                ctypes.byref(self._interface_guid),
                flags,
                None,
                ctypes.byref(p_list),
            )
            if result == ERROR_SUCCESS:
                count = p_list.contents.dwNumberOfItems
                label = "" if attempt == 0 else f" (retry {attempt})"
                lines.append(f"✓ WlanGetAvailableNetworkList{label}: {count} networks")
                if count > 0:
                    net_array = (WLAN_AVAILABLE_NETWORK * count).from_address(
                        ctypes.addressof(p_list.contents.Network))
                    for i in range(min(count, 3)):
                        net = net_array[i]
                        ssid = bytes(
                            net.dot11Ssid.ucSSID[: net.dot11Ssid.uSSIDLength]
                        ).decode("utf-8", errors="replace")
                        lines.append(f"  [{i+1}] {ssid} ({net.wlanSignalQuality}%)")
                self._api.WlanFreeMemory(p_list)
                break
            else:
                if attempt < max_retries - 1:
                    continue
                lines.append(f"✗ WlanGetAvailableNetworkList: {_wlan_error_string(result)}")

        # 2. Try WlanScan first (this may enable BSS list queries)
        for attempt in range(max_retries):
            if attempt > 0:
                time.sleep(0.5)
            result = self._api.WlanScan(
                self._handle,
                ctypes.byref(self._interface_guid),
                None, None, None,
            )
            if result == ERROR_SUCCESS:
                label = "" if attempt == 0 else f" (retry {attempt})"
                lines.append(f"✓ WlanScan{label}: OK")
                break
            else:
                if attempt < max_retries - 1:
                    continue
                lines.append(f"✗ WlanScan: {_wlan_error_string(result)}")

        # 3. Try WlanGetNetworkBssList with retries
        for bss_type, name in [
            (DOT11_BSS_TYPE_ANY, "ANY"),
            (DOT11_BSS_TYPE_INFRASTRUCTURE, "INFRA"),
        ]:
            for attempt in range(max_retries):
                if attempt > 0:
                    time.sleep(0.5 * attempt)
                p_bss = ctypes.POINTER(WLAN_BSS_LIST)()
                result = self._api.WlanGetNetworkBssList(
                    self._handle,
                    ctypes.byref(self._interface_guid),
                    None, bss_type, False, None,
                    ctypes.byref(p_bss),
                )
                if result == ERROR_SUCCESS:
                    count = p_bss.contents.dwNumberOfItems
                    label = "" if attempt == 0 else f" (retry {attempt})"
                    lines.append(f"✓ WlanGetNetworkBssList({name}){label}: {count} BSS entries")
                    self._api.WlanFreeMemory(p_bss)
                    break
                else:
                    if attempt < max_retries - 1:
                        continue
                    lines.append(f"✗ WlanGetNetworkBssList({name}): {_wlan_error_string(result)}")
                if result == ERROR_SUCCESS:
                    break
            else:
                continue
            break  # found BSS list, done

        return lines

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()

    # ----- scanning -----

    def trigger_scan(self) -> bool:
        """Ask the driver for an active scan. Returns True if it was accepted.

        Windows may reject WlanScan (scan already running, driver policy);
        that is non-fatal: the cached list from background scans is read.
        """
        result = self._api.WlanScan(
            self._handle,
            ctypes.byref(self._interface_guid),
            None, None, None,
        )
        if result != ERROR_SUCCESS:
            self._dbg(f"WlanScan: {_wlan_error_string(result)} (using cached scan data)")
            return False
        return True

    def _read_cached(self) -> list[BSSEntry]:
        return self._get_bss_list()

    def read_cached_results(self) -> ScanResult:
        """The BSS list Windows already has, without triggering a scan."""
        return self._to_result(self._get_bss_list())

    def get_scan_results(self) -> ScanResult:
        """Trigger a scan, wait for its completion notification (or the
        timeout), and return the BSS list."""
        bss_list = self._cycle.run()
        st = self._cycle.last
        self._dbg(f"Scan {st.completed_by} in {st.duration:.2f} s: "
                  f"{len(bss_list)} BSS entries")
        return self._to_result(bss_list)

    def _to_result(self, bss_list: list[BSSEntry]) -> ScanResult:
        return ScanResult(
            timestamp=datetime.now(),
            networks=group_networks(bss_list),
            interface_name=self._interface_name,
            interface_guid=str(self._interface_guid),
        )

    def _get_bss_list(self) -> list[BSSEntry]:
        """Retrieve the BSS list from wlanapi and parse into BSSEntry objects.

        Tries DOT11_BSS_TYPE_ANY first; falls back to
        DOT11_BSS_TYPE_INFRASTRUCTURE if ANY returns empty/error.
        """
        for bss_type in (DOT11_BSS_TYPE_ANY, DOT11_BSS_TYPE_INFRASTRUCTURE):
            entries = self._try_get_bss_list(bss_type)
            if entries:
                return entries
        return []

    def _try_get_bss_list(self, bss_type: int) -> list[BSSEntry]:
        p_bss_list = ctypes.POINTER(WLAN_BSS_LIST)()
        result = self._api.WlanGetNetworkBssList(
            self._handle,
            ctypes.byref(self._interface_guid),
            None,  # all SSIDs
            bss_type,
            False,  # bSecurityEnabled = False (get all)
            None,   # reserved
            ctypes.byref(p_bss_list),
        )
        if result != ERROR_SUCCESS:
            if bss_type != DOT11_BSS_TYPE_INFRASTRUCTURE:
                self._dbg(
                    f"WlanGetNetworkBssList(ANY): "
                    f"{_wlan_error_string(result)} — trying INFRASTRUCTURE")
            else:
                self._dbg(
                    f"WlanGetNetworkBssList(INFRA): "
                    f"{_wlan_error_string(result)}")
                self._dbg(
                    "Possible causes: Location Services disabled, "
                    "WiFi adapter in low-power state, or driver issue. "
                    "Try: Settings > Privacy > Location > On")
            return []

        entries: list[BSSEntry] = []
        count = p_bss_list.contents.dwNumberOfItems
        self._dbg(f"BSS list: {count} entries, "
                  f"totalSize={p_bss_list.contents.dwTotalSize}")

        if count == 0:
            self._api.WlanFreeMemory(p_bss_list)
            return []

        try:
            # Cast the inline 1-element array to correct size array
            EntryArray = WLAN_BSS_ENTRY * count
            entries_ptr = ctypes.cast(
                p_bss_list.contents.wlanBssEntries,
                ctypes.POINTER(EntryArray),
            )

            base = ctypes.addressof(p_bss_list.contents.wlanBssEntries)
            stride = ctypes.sizeof(WLAN_BSS_ENTRY)
            for i in range(count):
                b = entries_ptr.contents[i]
                entry_addr = base + i * stride
                ie_data = b""
                if b.ulIeSize > 0 and b.ulIeOffset > 0:
                    ie_data = ctypes.string_at(entry_addr + b.ulIeOffset, b.ulIeSize)
                entries.append(build_bss_entry(
                    ssid=bytes(b.dot11Ssid.ucSSID[: min(b.dot11Ssid.uSSIDLength, 32)]),
                    bssid=bytes(b.dot11Bssid),
                    rssi=b.lRssi,
                    freq_khz=b.ulChCenterFrequency,
                    phy_id=b.dot11BssPhyType,
                    beacon_period=b.usBeaconPeriod,
                    capability=b.usCapabilityInformation,
                    ies=ie_data,
                    legacy_max_rate=b.wlanRateSet.max_mbps(),
                    vendor_lookup=lookup_vendor,
                ))

        finally:
            self._api.WlanFreeMemory(p_bss_list)

        return entries


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------


def scan() -> ScanResult:
    """Perform a single scan and return results. One-shot convenience."""
    with WiFiScanner() as scanner:
        return scanner.get_scan_results()
