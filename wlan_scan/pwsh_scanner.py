"""wlanapi scanner via PowerShell/.NET P/Invoke bridge for ARM64 Windows.

Uses .NET CLR marshaling instead of ctypes libffi because
ctypes can't pass struct parameters to wlanapi.dll on ARM64.
The CLR interop works fine — proven by test_bss_bridge.py.

Returns the same ScanResult model as scanner.py for drop-in replacement.
"""

import json
import logging
import subprocess
from typing import Optional

from .bss import build_bss_entry
from .models import BSSEntry, ScanResult, group_networks
from .vendors import lookup_vendor

_log = logging.getLogger("wlanscan.pwsh")


# ── PowerShell bridge script (known-working from test_bss_bridge.py) ──
_BRIDGE_PS1 = r"""
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

[StructLayout(LayoutKind.Sequential)]
public struct GUID_WLAN {
    public uint Data1;
    public ushort Data2;
    public ushort Data3;
    [MarshalAs(UnmanagedType.ByValArray, SizeConst=8)]
    public byte[] Data4;
    public override string ToString() {
        return string.Format("{0:X8}-{1:X4}-{2:X4}-{3:X2}{4:X2}-{5:X2}{6:X2}{7:X2}{8:X2}{9:X2}{10:X2}",
            Data1, Data2, Data3,
            Data4[0], Data4[1], Data4[2], Data4[3],
            Data4[4], Data4[5], Data4[6], Data4[7]);
    }
}

[StructLayout(LayoutKind.Sequential)]
public struct DOT11_SSID {
    public uint uSSIDLength;
    [MarshalAs(UnmanagedType.ByValArray, SizeConst=32)]
    public byte[] ucSSID;
}

[StructLayout(LayoutKind.Sequential)]
public struct WLAN_INTERFACE_INFO {
    public GUID_WLAN InterfaceGuid;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=256)]
    public string strInterfaceDescription;
    public uint isState;
}

[StructLayout(LayoutKind.Sequential)]
public struct WLAN_INTERFACE_INFO_LIST {
    public uint dwNumberOfItems;
    public uint dwIndex;
    public WLAN_INTERFACE_INFO InterfaceInfo;
}

[StructLayout(LayoutKind.Sequential)]
public struct WLAN_RATE_SET {
    public uint uRateSetLength;
    [MarshalAs(UnmanagedType.ByValArray, SizeConst=126)]
    public ushort[] usRateSet;
}

[StructLayout(LayoutKind.Sequential)]
public struct WLAN_BSS_ENTRY {
    public DOT11_SSID dot11Ssid;
    public uint uPhyId;
    [MarshalAs(UnmanagedType.ByValArray, SizeConst=6)]
    public byte[] dot11Bssid;
    public uint dot11BssType;
    public uint dot11BssPhyType;
    public int lRssi;
    public uint uLinkQuality;
    [MarshalAs(UnmanagedType.U1)]
    public bool bInRegDomain;   // BOOLEAN: 1 byte (default bool marshal is 4)
    public ushort usBeaconPeriod;
    public ulong ullTimestamp;
    public ulong ullHostTimestamp;
    public ushort usCapabilityInformation;
    public uint ulChCenterFrequency;
    public WLAN_RATE_SET wlanRateSet;
    public uint ulIeOffset;
    public uint ulIeSize;
}

[StructLayout(LayoutKind.Sequential)]
public struct WLAN_BSS_LIST {
    public uint dwTotalSize;
    public uint dwNumberOfItems;
    public WLAN_BSS_ENTRY wlanBssEntries;
}

[StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
public struct WLAN_AVAILABLE_NETWORK {
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=256)]
    public string strProfileName;
    public DOT11_SSID dot11Ssid;
    public uint dot11BssType;
    public uint uNumberOfBssids;
    public bool bNetworkConnectable;
    public uint wlanNotConnectableReason;
    public uint uNumberOfPhyTypes;
    [MarshalAs(UnmanagedType.ByValArray, SizeConst=8)]
    public uint[] dot11PhyTypes;
    public bool bMorePhyTypes;
    public uint wlanSignalQuality;
    public bool bSecurityEnabled;
    public uint dot11DefaultAuthAlgorithm;
    public uint dot11DefaultCipherAlgorithm;
    public uint dwFlags;
    public uint dwReserved;
}

public static class WlanApi {
    [DllImport("wlanapi.dll")]
    public static extern uint WlanOpenHandle(
        uint dwClientVersion, IntPtr pReserved,
        out uint pdwClientVersion, out IntPtr phClientHandle);

    [DllImport("wlanapi.dll")]
    public static extern uint WlanEnumInterfaces(
        IntPtr hClientHandle, IntPtr pReserved,
        out IntPtr ppInterfaceList);

    [DllImport("wlanapi.dll")]
    public static extern uint WlanScan(
        IntPtr hClientHandle, ref GUID_WLAN pInterfaceGuid,
        IntPtr pDot11Ssid, IntPtr pIeData, IntPtr pReserved);

    [DllImport("wlanapi.dll")]
    public static extern uint WlanGetNetworkBssList(
        IntPtr hClientHandle, ref GUID_WLAN pInterfaceGuid,
        IntPtr pDot11Ssid, uint dot11BssType,
        bool bSecurityEnabled, IntPtr pReserved,
        out IntPtr ppWlanBssList);

    [DllImport("wlanapi.dll")]
    public static extern void WlanFreeMemory(IntPtr pMemory);

    [DllImport("wlanapi.dll")]
    public static extern uint WlanCloseHandle(
        IntPtr hClientHandle, IntPtr pReserved);
}
"@

$ErrorActionPreference = "Stop"

# Open handle
$ver = 2; $neg = 0; $h = [IntPtr]::Zero
$r = [WlanApi]::WlanOpenHandle($ver, [IntPtr]::Zero, [ref]$neg, [ref]$h)
if ($r -ne 0) { Write-Output '{"error":"WlanOpenHandle failed","code":' + $r + '}'; exit 1 }

# Enum interfaces
$p = [IntPtr]::Zero
$r = [WlanApi]::WlanEnumInterfaces($h, [IntPtr]::Zero, [ref]$p)
if ($r -ne 0) { Write-Output '{"error":"WlanEnumInterfaces failed","code":' + $r + '}'; exit 1 }

$ifList = [Runtime.InteropServices.Marshal]::PtrToStructure($p, [Type][WLAN_INTERFACE_INFO_LIST])
$guid = $ifList.InterfaceInfo.InterfaceGuid
$ifaceName = $ifList.InterfaceInfo.strInterfaceDescription
$ifaceState = $ifList.InterfaceInfo.isState

# Scan
$r = [WlanApi]::WlanScan($h, [ref]$guid, [IntPtr]::Zero, [IntPtr]::Zero, [IntPtr]::Zero)
if ($r -ne 0) {
    # Scan may fail if already scanning — try BSS list anyway (cached)
}

# Small delay for scan to complete
Start-Sleep -Milliseconds 500

# Get BSS list — try DOT11_BSS_TYPE_ANY first, then INFRASTRUCTURE
$bssTypes = @(3, 1)
$bssList = $null
foreach ($bt in $bssTypes) {
    $pBss = [IntPtr]::Zero
    $r = [WlanApi]::WlanGetNetworkBssList($h, [ref]$guid, [IntPtr]::Zero, $bt, $false, [IntPtr]::Zero, [ref]$pBss)
    if ($r -eq 0 -and $pBss -ne [IntPtr]::Zero) {
        $bssList = $pBss
        break
    }
}

if ($bssList -eq $null) {
    [WlanApi]::WlanFreeMemory($p)
    [WlanApi]::WlanCloseHandle($h, [IntPtr]::Zero)
    Write-Output '{"error":"WlanGetNetworkBssList failed"}'
    exit 1
}

# Parse BSS list
$header = [Runtime.InteropServices.Marshal]::PtrToStructure($bssList, [Type][WLAN_BSS_LIST])
$count = [int]$header.dwNumberOfItems

# Build JSON output manually
$entries = @()
$entrySize = [Runtime.InteropServices.Marshal]::SizeOf([Type][WLAN_BSS_ENTRY])
$firstEntryOffset = [Runtime.InteropServices.Marshal]::OffsetOf([Type][WLAN_BSS_LIST], "wlanBssEntries")

for ($i = 0; $i -lt $count; $i++) {
    $entryPtr = [IntPtr]::Add($bssList, [int]$firstEntryOffset + ($i * $entrySize))
    $bss = [Runtime.InteropServices.Marshal]::PtrToStructure($entryPtr, [Type][WLAN_BSS_ENTRY])

    # SSID as raw hex; Python decodes it (same as the ctypes path)
    $ssidLen = [Math]::Min([int]$bss.dot11Ssid.uSSIDLength, 32)
    $ssidHex = ""
    if ($ssidLen -gt 0) {
        $ssidHex = [BitConverter]::ToString($bss.dot11Ssid.ucSSID, 0, $ssidLen) -replace '-',''
    }

    # Format BSSID
    $bssid = "{0:X2}:{1:X2}:{2:X2}:{3:X2}:{4:X2}:{5:X2}" -f `
        $bss.dot11Bssid[0], $bss.dot11Bssid[1], $bss.dot11Bssid[2],
        $bss.dot11Bssid[3], $bss.dot11Bssid[4], $bss.dot11Bssid[5]

    # Extract IE bytes at ulIeOffset
    $ieHex = ""
    $ieOffset = [int]$bss.ulIeOffset
    $ieSize = [int]$bss.ulIeSize
    if ($ieSize -gt 0) {
        $ieBytes = New-Object byte[] $ieSize
        [Runtime.InteropServices.Marshal]::Copy(
            [IntPtr]::Add($entryPtr, $ieOffset), $ieBytes, 0, $ieSize)
        $ieHex = [BitConverter]::ToString($ieBytes) -replace '-',''
    }

    # Get max rate
    $maxRate = 0
    # uRateSetLength is in bytes; each rate is a USHORT
    for ($j = 0; $j -lt [Math]::Min([int]($bss.wlanRateSet.uRateSetLength / 2), 126); $j++) {
        $rval = $bss.wlanRateSet.usRateSet[$j]
        $rval = $rval -band 0x7FFF   # bit 15 = basic rate flag
        if ($rval -gt 0) {
            # Rate is in units of 0.5 Mbps
            $rate = [double]$rval * 0.5
            if ($rate -gt $maxRate) { $maxRate = $rate }
        }
    }

    $entries += @{
        ssidHex = $ssidHex
        bssid = $bssid
        rssi = $bss.lRssi
        channelFrequency = $bss.ulChCenterFrequency
        phyType = $bss.dot11BssPhyType
        beaconPeriod = $bss.usBeaconPeriod
        capabilities = $bss.usCapabilityInformation
        maxRate = $maxRate
        ieHex = $ieHex
    }
}

# Cleanup
[WlanApi]::WlanFreeMemory($bssList)
[WlanApi]::WlanFreeMemory($p)
[WlanApi]::WlanCloseHandle($h, [IntPtr]::Zero)

# Output as single JSON line
$result = @{
    interface = $ifaceName
    state = $ifaceState
    count = $count
    entries = $entries
} | ConvertTo-Json -Depth 5 -Compress
Write-Output $result
"""


def entry_from_json(raw: dict) -> BSSEntry:
    """Map one bridge JSON entry to a BSSEntry via the shared builder."""
    ssid_hex = raw.get("ssidHex")
    if ssid_hex is not None:
        ssid = bytes.fromhex(ssid_hex) if ssid_hex else b""
    else:  # older bridge output
        ssid = (raw.get("ssid") or "").encode()
        if ssid == b"HIDDEN":
            ssid = b""
    ie_hex = raw.get("ieHex") or ""
    try:
        ies = bytes.fromhex(ie_hex)
    except ValueError:
        ies = b""
    return build_bss_entry(
        ssid=ssid,
        bssid=raw.get("bssid", ""),
        rssi=int(raw.get("rssi", -100)),
        freq_khz=int(raw.get("channelFrequency", 0)),
        phy_id=int(raw.get("phyType", 0)),
        beacon_period=int(raw.get("beaconPeriod", 0)),
        capability=int(raw.get("capabilities", 0)),
        ies=ies,
        legacy_max_rate=float(raw.get("maxRate", 0.0) or 0.0),
        vendor_lookup=lookup_vendor,
    )


def result_from_json(data: dict) -> ScanResult:
    """Map the bridge's whole JSON document to a ScanResult."""
    raw = data.get("entries") or []
    if isinstance(raw, dict):  # ConvertTo-Json may unroll a 1-element array
        raw = [raw]
    entries = [entry_from_json(e) for e in raw]
    return ScanResult(
        networks=group_networks(entries),
        interface_name=data.get("interface", "") or "",
    )


class PwshScanner:
    """Wi-Fi scanner using PowerShell/.NET P/Invoke bridge.

    Works on ARM64 Windows where ctypes-based wlanapi calls fail
    due to libffi struct parameter handling bugs.
    """

    def __init__(self):
        self._interface_name: Optional[str] = None
        self._last_error: Optional[str] = None
        self._debug_callback: Optional[callable] = None
        self._proc: Optional[subprocess.Popen] = None
        self._stop_requested = False

    def request_stop(self) -> None:
        """Kill a bridge process that is still running (Stop / window close)."""
        proc = self._proc
        if proc is not None:
            self._stop_requested = True
            try:
                proc.kill()
            except Exception:
                pass

    def set_debug_callback(self, callback: callable) -> None:
        self._debug_callback = callback

    def _debug(self, msg: str) -> None:
        if self._debug_callback:
            self._debug_callback(msg)
        else:
            _log.debug(msg)

    @property
    def interface_name(self) -> Optional[str]:
        return self._interface_name

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def _empty_result(self) -> ScanResult:
        return ScanResult(
            networks=[],
            interface_name="",
        )

    def scan(self) -> ScanResult:
        """Run a full scan via PowerShell bridge.

        Returns ScanResult with networks, each containing BSSEntry
        objects with real channel widths from 802.11 IEs.
        """
        self._last_error = None

        try:
            self._proc = subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-Command", _BRIDGE_PS1],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
            )
        except FileNotFoundError:
            self._last_error = "PowerShell not found"
            return self._empty_result()
        try:
            stdout, stderr = self._proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.communicate()
            self._last_error = "PowerShell bridge timed out"
            return self._empty_result()
        finally:
            returncode = self._proc.returncode
            self._proc = None
        if self._stop_requested:
            self._stop_requested = False
            self._last_error = "stopped"
            return self._empty_result()
        proc = subprocess.CompletedProcess([], returncode, stdout or "", stderr or "")

        self._debug(f"pwsh exit={proc.returncode} stdout={len(proc.stdout)}B stderr={len(proc.stderr)}B")

        if proc.returncode != 0 or not proc.stdout.strip():
            self._last_error = proc.stderr.strip() or "no output"
            if proc.stderr:
                self._debug(f"pwsh stderr: {proc.stderr[:300]}")
            return self._empty_result()

        try:
            raw_stdout = proc.stdout.strip()
            # PowerShell may leak .NET return values to stdout before the JSON.
            # The JSON is always the last non-empty line. Take only that line.
            lines = raw_stdout.split('\n')
            # Find the last line that starts with { and ends with }
            for line in reversed(lines):
                stripped = line.strip()
                if stripped.startswith('{') and stripped.endswith('}'):
                    raw_stdout = stripped
                    break
            data = json.loads(raw_stdout)
        except json.JSONDecodeError as e:
            _log.warning(f"PowerShell bridge JSON parse failed: {e}; "
                         f"first 500 chars: {proc.stdout[:500]!r}")
            self._last_error = f"JSON parse error: {proc.stdout[:200]}"
            if proc.stderr:
                self._last_error += f" | stderr: {proc.stderr.strip()[:200]}"
            return self._empty_result()

        if data.get("error"):
            self._last_error = data["error"]
            self._debug(f"pwsh bridge error: {data['error']}")
            return self._empty_result()

        self._interface_name = data.get("interface", "")
        result = result_from_json(data)
        self._debug(f"pwsh result: {len(result.networks)} networks, "
                    f"{sum(len(n.bss_list) for n in result.networks)} BSS total")
        return result
