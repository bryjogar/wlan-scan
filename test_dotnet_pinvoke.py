"""Test: Call wlanapi via PowerShell .NET P/Invoke (not ctypes).

.NET's CLR marshaling is fundamentally different from ctypes'
libffi-based calling. If this works, we can use a small
PowerShell bridge for wlanapi calls on ARM64.
"""

import subprocess
import sys

PS_SCRIPT = r"""
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;

[StructLayout(LayoutKind.Sequential)]
public struct GUID_WLAN {
    public uint Data1;
    public ushort Data2;
    public ushort Data3;
    [MarshalAs(UnmanagedType.ByValArray, SizeConst=8)]
    public byte[] Data4;
}

[StructLayout(LayoutKind.Sequential)]
public struct DOT11_SSID {
    public uint uSSIDLength;
    [MarshalAs(UnmanagedType.ByValArray, SizeConst=32)]
    public byte[] ucSSID;
}

[StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
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

[StructLayout(LayoutKind.Sequential)]
public struct WLAN_AVAILABLE_NETWORK_LIST {
    public uint dwNumberOfItems;
    public uint dwIndex;
    public WLAN_AVAILABLE_NETWORK Network;
}

public static class WlanApi {
    [DllImport("wlanapi.dll", SetLastError=true)]
    public static extern uint WlanOpenHandle(
        uint dwClientVersion,
        IntPtr pReserved,
        out uint pdwClientVersion,
        out IntPtr phClientHandle);

    [DllImport("wlanapi.dll", SetLastError=true)]
    public static extern uint WlanEnumInterfaces(
        IntPtr hClientHandle,
        IntPtr pReserved,
        out IntPtr ppInterfaceList);

    [DllImport("wlanapi.dll", SetLastError=true)]
    public static extern uint WlanGetAvailableNetworkList(
        IntPtr hClientHandle,
        ref GUID_WLAN pInterfaceGuid,
        uint dwFlags,
        IntPtr pReserved,
        out IntPtr ppAvailableNetworkList);

    [DllImport("wlanapi.dll", SetLastError=true)]
    public static extern uint WlanScan(
        IntPtr hClientHandle,
        ref GUID_WLAN pInterfaceGuid,
        IntPtr pDot11Ssid,
        IntPtr pIeData,
        IntPtr pReserved);

    [DllImport("wlanapi.dll", SetLastError=true)]
    public static extern void WlanFreeMemory(IntPtr pMemory);

    [DllImport("wlanapi.dll", SetLastError=true)]
    public static extern uint WlanCloseHandle(
        IntPtr hClientHandle,
        IntPtr pReserved);
}
"@

# --- Test ---
$clientVersion = 2
$negVersion = 0
$handle = [IntPtr]::Zero

$r = [WlanApi]::WlanOpenHandle($clientVersion, [IntPtr]::Zero, [ref]$negVersion, [ref]$handle)
Write-Output "OPEN:$r neg=$negVersion"

if ($r -ne 0) { exit }

# Enum interfaces
$pList = [IntPtr]::Zero
$r = [WlanApi]::WlanEnumInterfaces($handle, [IntPtr]::Zero, [ref]$pList)
Write-Output "ENUM:$r"

if ($r -ne 0) {
    [WlanApi]::WlanCloseHandle($handle, [IntPtr]::Zero)
    exit
}

$ifaceList = [Runtime.InteropServices.Marshal]::PtrToStructure($pList, [Type][WLAN_INTERFACE_INFO_LIST])
$guid = $ifaceList.InterfaceInfo.InterfaceGuid
$desc = $ifaceList.InterfaceInfo.strInterfaceDescription
$state = $ifaceList.InterfaceInfo.isState
Write-Output "IFACE:$desc (state=$state)"

# Test WlanGetAvailableNetworkList
$pNetList = [IntPtr]::Zero
$r = [WlanApi]::WlanGetAvailableNetworkList($handle, [ref]$guid, 0, [IntPtr]::Zero, [ref]$pNetList)
Write-Output "GAL:$r"

if ($r -eq 0) {
    $netList = [Runtime.InteropServices.Marshal]::PtrToStructure($pNetList, [Type][WLAN_AVAILABLE_NETWORK_LIST])
    $count = $netList.dwNumberOfItems
    Write-Output "NETWORKS:$count"
    
    if ($count -gt 0) {
        $offset = [Runtime.InteropServices.Marshal]::OffsetOf([Type][WLAN_AVAILABLE_NETWORK_LIST], "Network")
        $netSize = [Runtime.InteropServices.Marshal]::SizeOf([Type][WLAN_AVAILABLE_NETWORK])
        for ($i = 0; $i -lt [Math]::Min($count, 3); $i++) {
            $netPtr = [IntPtr]::Add($pNetList, [int]$offset + ($i * $netSize))
            $net = [Runtime.InteropServices.Marshal]::PtrToStructure($netPtr, [Type][WLAN_AVAILABLE_NETWORK])
            # Get SSID
            $ssidBytes = $net.dot11Ssid.ucSSID[0..([int]$net.dot11Ssid.uSSIDLength - 1)]
            $ssid = [System.Text.Encoding]::UTF8.GetString($ssidBytes)
            Write-Output "  NET$i SSID=$ssid BSS=$($net.uNumberOfBssids) sig=$($net.wlanSignalQuality) sec=$($net.bSecurityEnabled)"
        }
    }
    [WlanApi]::WlanFreeMemory($pNetList)
}

# Test WlanScan
$r = [WlanApi]::WlanScan($handle, [ref]$guid, [IntPtr]::Zero, [IntPtr]::Zero, [IntPtr]::Zero)
Write-Output "SCAN:$r"

[WlanApi]::WlanFreeMemory($pList)
[WlanApi]::WlanCloseHandle($handle, [IntPtr]::Zero)
"""

print("Testing wlanapi via .NET P/Invoke (PowerShell Add-Type)...")
print()

result = subprocess.run(
    ["powershell.exe", "-NoProfile", "-Command", PS_SCRIPT],
    capture_output=True, text=True, timeout=30)

print(result.stdout)
if result.stderr:
    stderr = result.stderr.strip()
    if stderr:
        print("STDERR:", stderr[:1000])
print(f"Exit code: {result.returncode}")
