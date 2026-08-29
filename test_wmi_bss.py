"""Test: WMI MSNdis_80211_BSSIList availability on Windows 11 ARM64.

Run this on Bryan's laptop to check if the NDIS WMI class
exists and returns IEEE 802.11 Information Elements.
"""

import ctypes
from ctypes import wintypes
import sys

if sys.platform != "win32":
    print("Windows only.")
    sys.exit(1)

# We'll use PowerShell to query WMI since it handles the complex types

import subprocess
import json

PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'

# First: does the class exist?
try {
    $class = Get-CimClass -Namespace root/WMI -ClassName MSNdis_80211_BSSIList -ErrorAction Stop
    Write-Output "CLASS_EXISTS:yes"
} catch {
    Write-Output "CLASS_EXISTS:no"
    Write-Output "ERROR:$($_.Exception.Message)"
    exit
}

# Try to enumerate instances
try {
    $instances = Get-CimInstance -Namespace root/WMI -ClassName MSNdis_80211_BSSIList -ErrorAction Stop
    Write-Output "INSTANCE_COUNT:$($instances.Count)"
    
    if ($instances.Count -gt 0) {
        $inst = $instances[0]
        $bssiList = $inst.Ndis80211BSSIList
        Write-Output "BSSI_LIST_COUNT:$($bssiList.Count)"
        
        if ($bssiList.Count -gt 0) {
            $first = $bssiList[0]
            Write-Output "FIRST_BSS_SSID_LEN:$($first.Ndis80211Ssid.SsidLength)"
            Write-Output "FIRST_BSS_RSSI:$($first.Ndis80211Rssi)"
            Write-Output "FIRST_BSS_FREQ_KHZ:$($first.chCenterFrequency)"
            Write-Output "FIRST_BSS_IE_OFFSET:$($first.IeOffset)"
            Write-Output "FIRST_BSS_IE_SIZE:$($first.IeSize)"
        }
    }
} catch {
    Write-Output "QUERY_ERROR:$($_.Exception.Message)"
}

# Also check related classes
Write-Output "---"
$related = @('MSNdis_80211_ServiceSetIdentifier', 'MSNdis_80211_ReceivedSignalStrength',
             'MSNdis_80211_Configuration', 'MSNdis_80211_BSSIListScan')
foreach ($cls in $related) {
    try {
        $c = Get-CimClass -Namespace root/WMI -ClassName $cls -ErrorAction Stop
        Write-Output "RELATED:$cls=EXISTS"
    } catch {
        Write-Output "RELATED:$cls=NOT_FOUND"
    }
}
"""

print("Checking WMI MSNdis_80211_BSSIList availability...")
print()

result = subprocess.run(
    ["powershell.exe", "-NoProfile", "-Command", PS_SCRIPT],
    capture_output=True, text=True, timeout=30)

print(result.stdout)
if result.stderr:
    print("STDERR:", result.stderr[:500])
print(f"Exit code: {result.returncode}")
