"""Full PowerShell wlanapi bridge: scan + BSS list + IE extraction.

Uses .NET P/Invoke (which works on ARM64) to call all wlanapi
functions, then returns structured JSON that Python can parse
for channel width and other IE-based data.
"""

import subprocess
import json
import sys

PS_SCRIPT = r"""
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
public struct WLAN_BSS_ENTRY {
    public DOT11_SSID dot11Ssid;
    public uint uPhyId;
    [MarshalAs(UnmanagedType.ByValArray, SizeConst=6)]
    public byte[] dot11Bssid;
    public uint dot11BssType;
    public uint dot11BssPhyType;
    public int lRssi;
    public uint uLinkQuality;
    public bool bInRegDomain;
    public ushort usBeaconPeriod;
    public ulong ullTimestamp;
    public ulong ullHostTimestamp;
    public ushort usCapabilityInformation;
    public uint ulChCenterFrequency;
    [MarshalAs(UnmanagedType.ByValArray, SizeConst=126)]
    public ushort[] wlanRateSet;
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

    # Extract SSID
    $ssidBytes = @()
    for ($j = 0; $j -lt [int]$bss.dot11Ssid.uSSIDLength; $j++) {
        $ssidBytes += $bss.dot11Ssid.ucSSID[$j]
    }
    $ssid = ""
    if ($ssidBytes.Count -gt 0) {
        $ssid = [System.Text.Encoding]::UTF8.GetString($ssidBytes)
        # Filter non-printable characters (hidden SSIDs)
        $cleaned = ""
        foreach ($c in $ssid.ToCharArray()) {
            if ([int]$c -ge 32 -and [int]$c -le 126) { $cleaned += $c }
        }
        $ssid = $cleaned
    }
    if ($ssid -eq "") { $ssid = "HIDDEN" }

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
    foreach ($rateVal in $bss.wlanRateSet) {
        if ($rateVal -gt 0 -and $rateVal -lt 0xFFFF) {
            # Rate is in units of 0.5 Mbps
            $rate = [double]$rateVal * 0.5
            if ($rate -gt $maxRate) { $maxRate = $rate }
        }
    }

    $entries += @{
        ssid = $ssid
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

if sys.platform != "win32":
    print("Windows only.")
    sys.exit(1)

print("Running full wlanapi bridge via .NET P/Invoke...")
print()

result = subprocess.run(
    ["powershell.exe", "-NoProfile", "-Command", PS_SCRIPT],
    capture_output=True, text=True, timeout=45)

try:
    data = json.loads(result.stdout.strip())
except json.JSONDecodeError:
    print("RAW OUTPUT:")
    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr[:1000])
    sys.exit(1)

print(f"Interface: {data.get('interface', '?')}")
print(f"BSS entries: {data.get('count', 0)}")
print()

for entry in data.get("entries", []):
    print(f"  {entry['ssid']} ({entry['bssid']})")
    print(f"    RSSI: {entry['rssi']} dBm")
    print(f"    Channel: {entry['channelFrequency']/1000:.0f} MHz")
    print(f"    PHY type: {entry['phyType']}")
    print(f"    Max rate: {entry['maxRate']:.1f} Mbps")
    ie_hex = entry.get('ieHex', '')
    print(f"    IE data: {len(ie_hex)//2} bytes")
    if ie_hex:
        print(f"    IE hex: {ie_hex[:120]}...")
    print()
