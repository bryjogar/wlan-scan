"""Minimal test: CFUNCTYPE vs WinDLL attribute on ARM64 wlanapi.

Tests whether manually creating properly typed function pointers
(bypassing ctypes' WinDLL.__getattr__ wrapper) fixes the call.
"""

import ctypes
from ctypes import wintypes
import sys

if sys.platform != "win32":
    print("Windows only.")
    sys.exit(1)

ERROR_SUCCESS = 0


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", wintypes.BYTE * 8),
    ]


class DOT11_SSID(ctypes.Structure):
    _fields_ = [
        ("uSSIDLength", wintypes.ULONG),
        ("ucSSID", wintypes.BYTE * 32),
    ]


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


def err(code):
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.kernel32.FormatMessageW(
        0x00001000, None, code, 0, buf, 256, None)
    return buf.value.strip()


# ── Setup: open handle, get GUID ──
wlan = ctypes.WinDLL("wlanapi.dll")

wlan.WlanOpenHandle.argtypes = [
    wintypes.DWORD, ctypes.c_void_p,
    ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.HANDLE)]
wlan.WlanOpenHandle.restype = wintypes.DWORD

neg = wintypes.DWORD()
handle = wintypes.HANDLE()
r = wlan.WlanOpenHandle(wintypes.DWORD(2), None, ctypes.byref(neg), ctypes.byref(handle))
assert r == 0, f"WlanOpenHandle: {err(r)}"
print(f"Handle OK, version={neg.value}")

wlan.WlanEnumInterfaces.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p,
    ctypes.POINTER(ctypes.POINTER(WLAN_INTERFACE_INFO_LIST))]
wlan.WlanEnumInterfaces.restype = wintypes.DWORD

p = ctypes.POINTER(WLAN_INTERFACE_INFO_LIST)()
r = wlan.WlanEnumInterfaces(handle, None, ctypes.byref(p))
assert r == 0, f"WlanEnumInterfaces: {err(r)}"
iface = p.contents.InterfaceInfo[0]
guid = iface.InterfaceGuid
print(f"Iface: {iface.strInterfaceDescription}")
wlan.WlanFreeMemory(p)


# ── Test: manually resolve function address, create CFUNCTYPE pointer ──
kernel32 = ctypes.WinDLL("kernel32.dll")
kernel32.GetProcAddress.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
kernel32.GetProcAddress.restype = ctypes.c_void_p

GAL_t = ctypes.WINFUNCTYPE(
    wintypes.DWORD,                    # return
    wintypes.HANDLE,                   # hClientHandle
    ctypes.POINTER(GUID),             # pInterfaceGuid
    wintypes.DWORD,                   # dwFlags
    ctypes.c_void_p,                  # pReserved
    ctypes.POINTER(ctypes.POINTER(WLAN_AVAILABLE_NETWORK_LIST)),  # ppNetworkList
)

# Resolve raw address
func_addr = kernel32.GetProcAddress(wlan._handle, b"WlanGetAvailableNetworkList")
if not func_addr:
    print("FATAL: could not resolve WlanGetAvailableNetworkList address")
    sys.exit(1)
print(f"WlanGetAvailableNetworkList @ 0x{func_addr:x}")

# Create properly typed function pointer
GAL = ctypes.cast(func_addr, GAL_t)

# Test 1: CFUNCTYPE with byref GUID
p1 = ctypes.POINTER(WLAN_AVAILABLE_NETWORK_LIST)()
r1 = GAL(handle, ctypes.byref(guid), 0, None, ctypes.byref(p1))
print(f"CFUNCTYPE + byref:   {err(r1)}")

# Test 2: CFUNCTYPE with pointer GUID
p2 = ctypes.POINTER(WLAN_AVAILABLE_NETWORK_LIST)()
guid_p = ctypes.pointer(guid)
r2 = GAL(handle, guid_p, 0, None, ctypes.byref(p2))
print(f"CFUNCTYPE + pointer: {err(r2)}")

# Test 3: CFUNCTYPE with raw address
p3 = ctypes.POINTER(WLAN_AVAILABLE_NETWORK_LIST)()
r3 = GAL(handle, ctypes.c_void_p(ctypes.addressof(guid)), 0, None, ctypes.byref(p3))
print(f"CFUNCTYPE + address: {err(r3)}")

# Test 4: WinDLL attribute (control — should fail same as before)
wlan.WlanGetAvailableNetworkList.argtypes = GAL_t.argtypes
wlan.WlanGetAvailableNetworkList.restype = wintypes.DWORD
p4 = ctypes.POINTER(WLAN_AVAILABLE_NETWORK_LIST)()
r4 = wlan.WlanGetAvailableNetworkList(handle, ctypes.byref(guid), 0, None, ctypes.byref(p4))
print(f"WinDLL  + byref:     {err(r4)}")

wlan.WlanCloseHandle(handle, None)
print("\nDone.")
