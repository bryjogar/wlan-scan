"""ARM64 wlanapi diagnostic — tests multiple API call variants.

Run on Bryan's Windows ARM64 laptop to determine which wlanapi
calling conventions work with the Qualcomm FastConnect 7800.

Usage: python diagnose_arm64.py
"""

import ctypes
from ctypes import wintypes
import sys
import textwrap

ERROR_SUCCESS = 0


# ── GUID (not in all wintypes builds, define explicitly) ──
class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", wintypes.BYTE * 8),
    ]


# ── Structs (minimal, same as scanner.py) ──

class DOT11_SSID(ctypes.Structure):
    _fields_ = [
        ("uSSIDLength", wintypes.ULONG),
        ("ucSSID", wintypes.BYTE * 32),
    ]


class WLAN_INTERFACE_INFO(ctypes.Structure):
    _fields_ = [
        ("InterfaceGuid", GUID),
        ("strInterfaceDescription", wintypes.WCHAR * 256),
        ("isState", wintypes.DWORD),
    ]


class WLAN_INTERFACE_INFO_LIST(ctypes.Structure):
    _fields_ = [
        ("dwNumberOfItems", wintypes.DWORD),
        ("dwIndex", wintypes.DWORD),
        ("InterfaceInfo", WLAN_INTERFACE_INFO * 1),
    ]


# AvailableNetworkList structs (minimal for diag)
class WLAN_AVAILABLE_NETWORK(ctypes.Structure):
    _fields_ = [
        ("strProfileName", wintypes.WCHAR * 256),
        ("dot11Ssid", DOT11_SSID),
        ("dot11BssType", wintypes.DWORD),
        ("uNumberOfBssids", wintypes.DWORD),
        ("bNetworkConnectable", wintypes.BOOL),
        ("wlanNotConnectableReason", wintypes.DWORD),
        ("uNumberOfPhyTypes", wintypes.DWORD),
        ("dot11PhyTypes", wintypes.DWORD * 8),
        ("bMorePhyTypes", wintypes.BOOL),
        ("wlanSignalQuality", wintypes.DWORD),
        ("bSecurityEnabled", wintypes.BOOL),
        ("dot11DefaultAuthAlgorithm", wintypes.DWORD),
        ("dot11DefaultCipherAlgorithm", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("dwReserved", wintypes.DWORD),
    ]


class WLAN_AVAILABLE_NETWORK_LIST(ctypes.Structure):
    _fields_ = [
        ("dwNumberOfItems", wintypes.DWORD),
        ("dwIndex", wintypes.DWORD),
        ("Network", WLAN_AVAILABLE_NETWORK * 1),
    ]


# ── BSS list structs ──
class WLAN_BSS_ENTRY(ctypes.Structure):
    _fields_ = [
        ("dot11Ssid", DOT11_SSID),
        ("uPhyId", wintypes.ULONG),
        ("dot11Bssid", wintypes.BYTE * 6),
        ("dot11BssType", wintypes.DWORD),
        ("dot11BssPhyType", wintypes.DWORD),
        ("lRssi", wintypes.LONG),
        ("uLinkQuality", wintypes.ULONG),
        ("bInRegDomain", wintypes.BOOL),
        ("usBeaconPeriod", wintypes.USHORT),
        ("ullTimestamp", ctypes.c_ulonglong),
        ("ullHostTimestamp", ctypes.c_ulonglong),
        ("usCapabilityInformation", wintypes.USHORT),
        ("ulChCenterFrequency", wintypes.ULONG),
        ("wlanRateSet", ctypes.c_ushort * 126),
        ("ulIeOffset", wintypes.ULONG),
        ("ulIeSize", wintypes.ULONG),
    ]


class WLAN_BSS_LIST(ctypes.Structure):
    _fields_ = [
        ("dwTotalSize", wintypes.DWORD),
        ("dwNumberOfItems", wintypes.DWORD),
        ("wlanBssEntries", WLAN_BSS_ENTRY * 1),
    ]


# ── Load wlanapi ──
_wlanapi = ctypes.WinDLL("wlanapi.dll")

# Basic function bindings
_wlanapi.WlanOpenHandle.argtypes = [
    wintypes.DWORD, ctypes.c_void_p,
    ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.HANDLE),
]
_wlanapi.WlanOpenHandle.restype = wintypes.DWORD

_wlanapi.WlanEnumInterfaces.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p,
    ctypes.POINTER(ctypes.POINTER(WLAN_INTERFACE_INFO_LIST)),
]
_wlanapi.WlanEnumInterfaces.restype = wintypes.DWORD

_wlanapi.WlanCloseHandle.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
_wlanapi.WlanCloseHandle.restype = wintypes.DWORD

_wlanapi.WlanFreeMemory.argtypes = [ctypes.c_void_p]
_wlanapi.WlanFreeMemory.restype = None


def error_string(code):
    """Resolve a Win32 error code."""
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.kernel32.FormatMessageW(
        0x00001000, None, code, 0, buf, 256, None)
    return buf.value.strip() or f"0x{code:08X}"


def open_handle(version=2):
    """Open a WLAN handle with given API version. Returns (handle, negotiated)."""
    neg = wintypes.DWORD()
    handle = wintypes.HANDLE()
    result = _wlanapi.WlanOpenHandle(
        wintypes.DWORD(version), None,
        ctypes.byref(neg), ctypes.byref(handle),
    )
    if result != ERROR_SUCCESS:
        raise RuntimeError(f"WlanOpenHandle v{version} failed: {error_string(result)}")
    return handle, neg.value


def enum_interfaces(handle):
    """Enumerate interfaces, return (guid, name, state)."""
    p_list = ctypes.POINTER(WLAN_INTERFACE_INFO_LIST)()
    result = _wlanapi.WlanEnumInterfaces(
        handle, None, ctypes.byref(p_list))
    if result != ERROR_SUCCESS:
        raise RuntimeError(f"WlanEnumInterfaces failed: {error_string(result)}")
    try:
        item = p_list.contents.InterfaceInfo[0]
        return item.InterfaceGuid, item.strInterfaceDescription, item.isState
    finally:
        _wlanapi.WlanFreeMemory(p_list)


def test_call(label, fn, *args):
    """Run a test and return (ok, result_or_error)."""
    result = fn(*args)
    if result == ERROR_SUCCESS:
        return True, result
    return False, error_string(result)


def run():
    print("=" * 60)
    print("ARM64 wlanapi Diagnostic")
    print("=" * 60)
    print()

    # Verify platform
    if sys.platform != "win32":
        print("This script must run on Windows.")
        return
    is_arm = "arm" in sys.version.lower() or "arm" in sys.platform.lower()
    print(f"Python: {sys.version}")
    print(f"ARM64 native: {'Yes' if is_arm else 'Unknown (check manually)'}")
    print()

    # Step 1: Open handle with v2
    print("--- Step 1: Handle & Interface ---")
    try:
        handle, neg = open_handle(2)
        print(f"  WlanOpenHandle v2: OK (negotiated={neg})")
    except Exception as e:
        print(f"  WlanOpenHandle v2: FAILED — {e}")
        print("  Trying v1...")
        try:
            handle, neg = open_handle(1)
            print(f"  WlanOpenHandle v1: OK (negotiated={neg})")
        except Exception as e2:
            print(f"  WlanOpenHandle v1: FAILED — {e2}")
            return

    iface_guid, iface_name, iface_state = enum_interfaces(handle)
    state_names = {
        0: "not_ready", 1: "connected", 2: "ad_hoc",
        3: "disconnecting", 4: "disconnected",
        5: "associating", 6: "discovering", 7: "authenticating",
    }
    state_str = state_names.get(iface_state, f"unknown({iface_state})")
    print(f"  Interface: {iface_name} (state={state_str})")
    print()

    # ── Define wlanapi function variants ──

    # We'll test each API call with different parameter approaches

    print("--- Step 2: WlanGetAvailableNetworkList variants ---")
    print()

    # Variant A: argtypes + restype (our current approach)
    _wlanapi.WlanGetAvailableNetworkList.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(GUID),
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.POINTER(WLAN_AVAILABLE_NETWORK_LIST)),
    ]
    _wlanapi.WlanGetAvailableNetworkList.restype = wintypes.DWORD

    p_list = ctypes.POINTER(WLAN_AVAILABLE_NETWORK_LIST)()
    result = _wlanapi.WlanGetAvailableNetworkList(
        handle, ctypes.byref(iface_guid), 0, None, ctypes.byref(p_list))
    if result == ERROR_SUCCESS:
        n = p_list.contents.dwNumberOfItems
        print(f"  ✓ Variant A (argtypes, flags=0): {n} networks")
        _wlanapi.WlanFreeMemory(p_list)
    else:
        print(f"  ✗ Variant A (argtypes, flags=0): {error_string(result)}")

    # Variant B: flags=3
    p_list2 = ctypes.POINTER(WLAN_AVAILABLE_NETWORK_LIST)()
    result2 = _wlanapi.WlanGetAvailableNetworkList(
        handle, ctypes.byref(iface_guid), 3, None, ctypes.byref(p_list2))
    if result2 == ERROR_SUCCESS:
        n = p_list2.contents.dwNumberOfItems
        print(f"  ✓ Variant B (argtypes, flags=3): {n} networks")
        _wlanapi.WlanFreeMemory(p_list2)
    else:
        print(f"  ✗ Variant B (argtypes, flags=3): {error_string(result2)}")
    print()

    # ── WlanScan ──
    print("--- Step 3: WlanScan variants ---")
    print()

    _wlanapi.WlanScan.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(GUID),
        ctypes.POINTER(DOT11_SSID),
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    _wlanapi.WlanScan.restype = wintypes.DWORD

    result = _wlanapi.WlanScan(
        handle, ctypes.byref(iface_guid), None, None, None)
    if result == ERROR_SUCCESS:
        print(f"  ✓ WlanScan: OK")
    else:
        print(f"  ✗ WlanScan: {error_string(result)}")
    print()

    # ── WlanGetNetworkBssList ──
    print("--- Step 4: WlanGetNetworkBssList variants ---")
    print()

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

    DOT11_BSS_TYPE_INFRA = 1
    DOT11_BSS_TYPE_ANY = 3

    for bss_type, name in [(DOT11_BSS_TYPE_ANY, "ANY"), (DOT11_BSS_TYPE_INFRA, "INFRA")]:
        p_bss = ctypes.POINTER(WLAN_BSS_LIST)()
        result = _wlanapi.WlanGetNetworkBssList(
            handle, ctypes.byref(iface_guid),
            None, bss_type, False, None,
            ctypes.byref(p_bss),
        )
        if result == ERROR_SUCCESS:
            n = p_bss.contents.dwNumberOfItems
            print(f"  ✓ WlanGetNetworkBssList({name}): {n} BSS entries")
            if n > 0:
                entries = ctypes.cast(
                    p_bss.contents.wlanBssEntries,
                    ctypes.POINTER(WLAN_BSS_ENTRY * n),
                ).contents
                for i in range(min(n, 3)):
                    bss = entries[i]
                    ssid_bytes = bytes(bss.dot11Ssid.ucSSID[:bss.dot11Ssid.uSSIDLength])
                    ssid = ssid_bytes.decode("utf-8", errors="replace")
                    bssid = ":".join(f"{b:02x}" for b in bss.dot11Bssid)
                    print(f"    [{i+1}] {ssid} ({bssid}) "
                          f"ch={bss.ulChCenterFrequency/1000:.0f}MHz "
                          f"rssi={bss.lRssi} "
                          f"ie_offset={bss.ulIeOffset} ie_size={bss.ulIeSize}")
            _wlanapi.WlanFreeMemory(p_bss)
            break  # Success — stop here
        else:
            print(f"  ✗ WlanGetNetworkBssList({name}): {error_string(result)}")
    print()

    # ── Step 5: Test with v1 handle ──
    print("--- Step 5: Retry with v1 handle ---")
    print()

    _wlanapi.WlanCloseHandle(handle, None)

    try:
        handle_v1, neg_v1 = open_handle(1)
        print(f"  WlanOpenHandle v1: OK (negotiated={neg_v1})")

        iface_guid_v1, name_v1, state_v1 = enum_interfaces(handle_v1)
        print(f"  Interface: {name_v1}")

        # Try WlanGetAvailableNetworkList with v1 handle
        p_list_v1 = ctypes.POINTER(WLAN_AVAILABLE_NETWORK_LIST)()
        result_v1 = _wlanapi.WlanGetAvailableNetworkList(
            handle_v1, ctypes.byref(iface_guid_v1), 0, None, ctypes.byref(p_list_v1))
        if result_v1 == ERROR_SUCCESS:
            n = p_list_v1.contents.dwNumberOfItems
            print(f"  ✓ WlanGetAvailableNetworkList (v1 handle): {n} networks")
            _wlanapi.WlanFreeMemory(p_list_v1)
        else:
            print(f"  ✗ WlanGetAvailableNetworkList (v1 handle): {error_string(result_v1)}")

        _wlanapi.WlanCloseHandle(handle_v1, None)
    except Exception as e:
        print(f"  v1 test failed: {e}")
    print()

    # ── Step 6: Raw calling (no argtypes, no restype) ──
    print("--- Step 6: Raw calling (no argtypes, no restype) ---")
    print()

    # Re-open fresh handle
    _wlanapi.WlanCloseHandle(handle, None)
    handle2, neg2 = open_handle(2)
    iface_guid2, _, _ = enum_interfaces(handle2)

    # Load wlanapi WITHOUT setting any argtypes/restype
    raw = ctypes.WinDLL("wlanapi.dll")

    # Try WlanGetAvailableNetworkList with raw calling
    p = ctypes.POINTER(WLAN_AVAILABLE_NETWORK_LIST)()
    r = raw.WlanGetAvailableNetworkList(
        ctypes.c_void_p(handle2.value),
        ctypes.byref(iface_guid2),
        ctypes.c_ulong(0),
        ctypes.c_void_p(0),
        ctypes.byref(p))
    print(f"  Raw WlanGetAvailableNetworkList: {error_string(r)}")

    # Try with ctypes.pointer instead of byref
    p2 = ctypes.POINTER(WLAN_AVAILABLE_NETWORK_LIST)()
    guid_p = ctypes.pointer(iface_guid2)
    r2 = raw.WlanGetAvailableNetworkList(
        ctypes.c_void_p(handle2.value),
        guid_p,
        ctypes.c_ulong(0),
        ctypes.c_void_p(0),
        ctypes.byref(p2))
    print(f"  Raw (ctypes.pointer for GUID): {error_string(r2)}")

    # Try WlanScan - pass DOT11_SSID pointer explicitly
    ssid = DOT11_SSID()
    ssid.uSSIDLength = 0
    r3 = raw.WlanScan(
        ctypes.c_void_p(handle2.value),
        ctypes.pointer(iface_guid2),
        ctypes.pointer(ssid),
        ctypes.c_void_p(0),
        ctypes.c_void_p(0))
    print(f"  Raw WlanScan (explicit SSID): {error_string(r3)}")

    # Try WlanGetNetworkBssList with raw calling
    p4 = ctypes.POINTER(WLAN_BSS_LIST)()
    r4 = raw.WlanGetNetworkBssList(
        ctypes.c_void_p(handle2.value),
        ctypes.pointer(iface_guid2),
        ctypes.pointer(ssid),
        ctypes.c_ulong(3),
        ctypes.c_bool(False),
        ctypes.c_void_p(0),
        ctypes.byref(p4))
    print(f"  Raw WlanGetNetworkBssList: {error_string(r4)}")

    # Try CDLL instead of WinDLL
    print()
    print("  --- CDLL variant ---")
    cdll = ctypes.CDLL("wlanapi.dll")
    p5 = ctypes.POINTER(WLAN_AVAILABLE_NETWORK_LIST)()
    r5 = cdll.WlanGetAvailableNetworkList(
        ctypes.c_void_p(handle2.value),
        ctypes.pointer(iface_guid2),
        ctypes.c_ulong(0),
        ctypes.c_void_p(0),
        ctypes.byref(p5))
    print(f"  CDLL WlanGetAvailableNetworkList: {error_string(r5)}")

    _wlanapi.WlanCloseHandle(handle2, None)
    print()

    # ── Step 7: Bypass ctypes pointer → raw address ──
    print("--- Step 7: Raw address (bypass ctypes pointer) ---")
    print()

    handle3, _ = open_handle(2)
    iface_guid3, iface_name3, _ = enum_interfaces(handle3)

    # Dump GUID details
    guid = iface_guid3
    guid_str = f"{guid.Data1:08x}-{guid.Data2:04x}-{guid.Data3:04x}-" \
               f"{guid.Data4[0]:02x}{guid.Data4[1]:02x}-" \
               f"{guid.Data4[2]:02x}{guid.Data4[3]:02x}{guid.Data4[4]:02x}" \
               f"{guid.Data4[5]:02x}{guid.Data4[6]:02x}{guid.Data4[7]:02x}"
    print(f"  Interface GUID: {guid_str}")
    print(f"  GUID address: {ctypes.addressof(guid)}")
    print(f"  sizeof(GUID): {ctypes.sizeof(guid)}")

    # Try: pass GUID as raw c_void_p address (bypass ctypes pointer)
    p = ctypes.POINTER(WLAN_AVAILABLE_NETWORK_LIST)()
    raw_addr = ctypes.c_void_p(ctypes.addressof(guid))
    r = raw.WlanGetAvailableNetworkList(
        ctypes.c_void_p(handle3.value),
        raw_addr,
        ctypes.c_ulong(0),
        ctypes.c_void_p(0),
        ctypes.byref(p))
    print(f"  Raw address (c_void_p): {error_string(r)}")

    # Try: pass GUID pointer value extracted from handle3 struct
    # (Use handle value directly, not wrapped)
    p2 = ctypes.POINTER(WLAN_AVAILABLE_NETWORK_LIST)()
    r2 = raw.WlanGetAvailableNetworkList(
        handle3,  # raw handle, no c_void_p wrapping
        raw_addr,
        ctypes.c_ulong(0),
        ctypes.c_void_p(0),
        ctypes.byref(p2))
    print(f"  Raw address + raw handle: {error_string(r2)}")

    _wlanapi.WlanCloseHandle(handle3, None)
    print()

    print("=" * 60)
    print("Diagnostic complete. Share this output.")
    print("=" * 60)


if __name__ == "__main__":
    run()
