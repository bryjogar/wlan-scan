"""wlanapi scanner via PowerShell/.NET P/Invoke bridge for ARM64 Windows.

Uses .NET CLR marshaling instead of ctypes libffi because
ctypes can't pass struct parameters to wlanapi.dll on ARM64.
The CLR interop works fine — proven by test_bss_bridge.py.

Returns the same ScanResult model as scanner.py for drop-in replacement.
"""

import subprocess
import json
import sys
import struct as _struct
from typing import Optional

from .models import BSSEntry, Network, ScanResult
from .oui_lookup import lookup_vendor


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
    public bool bInRegDomain;
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
    for ($j = 0; $j -lt [int]$bss.wlanRateSet.uRateSetLength; $j++) {
        $rval = $bss.wlanRateSet.usRateSet[$j]
        if ($rval -gt 0 -and $rval -lt 0x7FFF) {
            # Rate is in units of 0.5 Mbps
            $rate = [double]$rval * 0.5
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


# ── PHY type mapping ──
_PHY_TYPE_MAP: dict[int, str] = {
    0: "unknown",
    1: "FHSS",
    2: "DSSS",
    3: "IR",
    4: "802.11a (OFDM)",
    5: "802.11b (HRDSSS)",
    6: "802.11g (ERP)",
    7: "802.11n (HT)",
    8: "802.11ac (VHT)",
    9: "802.11ad (DMG)",
    10: "802.11ax (HE)",
    11: "802.11be (EHT)",
}

_PHY_SHORT: dict[int, str] = {
    7: "802.11n",
    8: "802.11ac",
    9: "802.11ad",
    10: "802.11ax",
    11: "802.11be",
}


def _freq_to_band(freq_khz: int) -> str:
    """Convert center frequency in KHz to band label."""
    freq_mhz = freq_khz / 1000.0
    if freq_mhz < 2500:
        return "2.4 GHz"
    elif freq_mhz < 6000:
        return "5 GHz"
    else:
        return "6 GHz"


def _freq_to_channel(freq_khz: int, band: str) -> int:
    """Approximate channel number from center frequency."""
    freq_mhz = freq_khz / 1000.0
    if "2.4" in band:
        if 2412 <= freq_mhz <= 2484:
            return int((freq_mhz - 2407) / 5)
    elif "5" in band:
        if 5180 <= freq_mhz <= 5885:
            return int((freq_mhz - 5000) / 5)
    elif "6" in band:
        if 5955 <= freq_mhz <= 7115:
            return int((freq_mhz - 5950) / 5)
    return 0


def _parse_ie_hex(ie_hex: str, band: str = "") -> dict:
    """Parse hex-encoded IEs to get channel width, security, and capabilities.

    Returns: dict with keys width, phy, security, cipher, auth, generation, modes
    """
    width = 0
    phy = None
    security = None
    cipher = None
    auth = None
    has_ht = False
    has_vht = False
    has_he = False
    has_eht = False
    ie_tags_found: list[str] = []  # for debugging

    result = {
        "width": 0, "phy": None, "security": None,
        "cipher": None, "auth": None, "generation": "", "modes": "",
        "station_count": 0, "channel_utilization": 0,
    }

    if not ie_hex:
        return result

    try:
        ie_bytes = bytes.fromhex(ie_hex)
    except (ValueError, TypeError):
        return result

    i = 0
    while i < len(ie_bytes) - 2:
        tag = ie_bytes[i]
        length = ie_bytes[i + 1]
        if i + 2 + length > len(ie_bytes):
            break
        data = ie_bytes[i + 2: i + 2 + length]

        if tag == 11 and length >= 5:
            # BSS Load IE — station count + channel utilization
            sta_count = int.from_bytes(data[0:2], "little")
            chan_util = int.from_bytes(data[2:3], "little")  # 0-255 → 0-100%
            ie_tags_found.append(f"BSS_LOAD({sta_count},{chan_util})")
            result["station_count"] = sta_count
            result["channel_utilization"] = round(chan_util / 255 * 100, 1)

        elif tag == 45 and length >= 24:
            # HT Capabilities (802.11n)
            has_ht = True
            ie_tags_found.append("HT_CAP")

        elif tag == 48 and length >= 10:
            # RSN (WPA2/WPA3)
            ie_tags_found.append(f"RSN")
            rsn_cipher, rsn_auth, rsn_sec = _parse_rsn_ie(data[:length])
            if rsn_sec:
                security = rsn_sec
            if rsn_cipher:
                cipher = rsn_cipher
            if rsn_auth:
                auth = rsn_auth

        elif tag == 61 and length >= 4:
            # HT Operation (802.11n) — byte 0=primary ch, byte 1=HT Info 1
            ie_tags_found.append(f"HT_OP({data[0]},{data[1]})")
            if not phy:
                phy = "802.11n"
            ht_info = data[1]  # Secondary Channel Offset is in byte 1, bits 0-1
            sta_chan_width = (data[1] >> 2) & 1  # STA Channel Width is bit 2 of byte 1
            secondary_offset = ht_info & 3  # bits 0-1
            if sta_chan_width and secondary_offset != 0:
                width = 40
            else:
                width = 20

        elif tag == 191 and length >= 4:
            # VHT Capabilities (802.11ac)
            has_vht = True
            ie_tags_found.append("VHT_CAP")

        elif tag == 192 and length >= 5:
            # VHT Operation (802.11ac). VHT only operates on 5 GHz.
            ie_tags_found.append(f"VHT_OP({data[0]:#x})")
            if "2.4" not in band:
                if not phy:
                    phy = "802.11ac"
                vht_info = data[0]
                chan_width = vht_info & 3
                if chan_width == 3:
                    width = 240
                elif chan_width == 2:
                    width = 160
                elif chan_width == 1:
                    width = 80
                else:
                    width = 80
            else:
                ie_tags_found.append(f"  IGNORED(2.4GHz)")

        elif tag == 255 and length >= 1:
            # Extension IE — check extension ID
            ext_id = data[0]
            if ext_id == 36 and length >= 3:
                # HE Operation (802.11ax)
                has_he = True
                ie_tags_found.append(f"HE_OP(len={length})")
                if not phy:
                    phy = "802.11ax"
                # VHT Operation Info subfield (only meaningful on 5/6 GHz)
                if "2.4" not in band and length >= 4:
                    vht_op_present = data[3] & 1
                    if vht_op_present and length >= 10:
                        # VHT Operation Info at offset 9 (after 3-byte params + 3-byte BSS Color + 2-byte MCS)
                        vht_op = data[9]
                        chan_width = vht_op & 3
                        if chan_width == 3:
                            width = 240
                        elif chan_width == 2:
                            width = 160
                        elif chan_width == 1:
                            width = 80
                        else:
                            width = 80
                        ie_tags_found.append(f"  VHT_OpInfo: cw={chan_width}, width={width}")
            elif ext_id == 35:
                # HE Capabilities — use to detect PHY
                has_he = True
                if not phy:
                    phy = "802.11ax"
                ie_tags_found.append(f"HE_CAP")
            else:
                ie_tags_found.append(f"EXT({ext_id})")

        i = i + 2 + length

    # Determine Wi-Fi generation and supported modes
    is_5ghz = "5" in band or "6" in band
    if has_he:
        generation = "Wi-Fi 6E" if "6" in band else "Wi-Fi 6"
        modes = "a/n/ac/ax/be" if has_eht else ("a/n/ac/ax" if is_5ghz else "b/g/n/ax")
    elif has_vht:
        generation = "Wi-Fi 5"
        modes = "a/n/ac"
    elif has_ht:
        generation = "Wi-Fi 4"
        modes = "a/n" if is_5ghz else "b/g/n"
    else:
        generation = "Legacy"
        modes = "a" if is_5ghz else "b/g"

    if ie_tags_found:
        print(f"[ie_debug] IEs: {', '.join(ie_tags_found)}", file=sys.stderr, flush=True)

    return {
        "width": width,
        "phy": phy or "Unknown",
        "security": security or None,
        "cipher": cipher or "",
        "auth": auth or "",
        "generation": generation,
        "modes": modes,
        "station_count": result["station_count"],
        "channel_utilization": result["channel_utilization"],
    }


# ── RSN IE (WPA2/WPA3) parsing ──

# Cipher suite selectors (OUI 00-0f-ac, last byte)
_CIPHER_NAMES = {
    0x01: "WEP-40", 0x02: "TKIP", 0x04: "AES-CCMP",
    0x05: "WEP-104", 0x06: "TKIP+AES", 0x08: "GCMP",
    0x09: "GCMP-256", 0x0a: "CCMP-256", 0x0b: "BIP-GMAC-128",
    0x0c: "BIP-GMAC-256", 0x0d: "BIP-CMAC-256",
}

# AKM suite selectors (OUI 00-0f-ac, last byte)
_AKM_NAMES = {
    0x01: "802.1X", 0x02: "PSK", 0x03: "FT-802.1X",
    0x04: "FT-PSK", 0x05: "802.1X-SHA256", 0x06: "PSK-SHA256",
    0x08: "SAE", 0x09: "FT-SAE", 0x0d: "OWE", 0x0e: "OWE-TM",
    0x12: "FILS-SHA256", 0x13: "FILS-SHA384",
    0x14: "FT-FILS-SHA256", 0x15: "FT-FILS-SHA384",
}


def _read_oui_type(data: bytes, offset: int) -> int | None:
    """Read a 4-byte OUI+type suite selector, return last byte if OUI is 00-0f-ac."""
    if offset + 4 > len(data):
        return None
    if data[offset:offset + 3] == b"\x00\x0f\xac":
        return data[offset + 3]
    return None


def _parse_rsn_ie(data: bytes) -> tuple[str | None, str | None, str | None]:
    """Parse RSN IE (tag 48), return (cipher, auth, security_label)."""
    if len(data) < 8:
        return None, None, None

    # Group Cipher Suite (bytes 2-5 in RSN, offset 0 in data)
    pos = 2 if len(data) > 2 else 0
    group_cipher = _read_oui_type(data, pos)
    pos += 4

    cipher = _CIPHER_NAMES.get(group_cipher or 0, "") if group_cipher else ""

    # Pairwise Cipher Suite Count
    if pos + 2 > len(data):
        return cipher or None, None, None
    pw_count = int.from_bytes(data[pos:pos + 2], "little")
    pos += 2

    # Pairwise Cipher Suite List — take first one
    if pw_count > 0 and pos + 4 <= len(data):
        pw_cipher = _read_oui_type(data, pos)
        if pw_cipher and pw_cipher != group_cipher:
            cipher = _CIPHER_NAMES.get(pw_cipher, cipher)
        pos += pw_count * 4

    # AKM Suite Count
    if pos + 2 > len(data):
        return cipher or None, None, None
    akm_count = int.from_bytes(data[pos:pos + 2], "little")
    pos += 2

    # AKM Suite List — check all for security classification
    akm_types = []
    for _ in range(min(akm_count, 4)):  # cap at 4
        if pos + 4 > len(data):
            break
        akm_byte = _read_oui_type(data, pos)
        if akm_byte is not None:
            akm_types.append(akm_byte)
        pos += 4

    # Classify security from AKM suites
    auth_str = " / ".join(_AKM_NAMES.get(a, f"0x{a:02x}") for a in akm_types)

    if 0x08 in akm_types or 0x09 in akm_types:
        if 0x02 in akm_types:
            security = "WPA3-Transition"
        elif 0x09 in akm_types:
            security = "WPA3-Personal (FT)"
        else:
            security = "WPA3-Personal"
    elif 0x0d in akm_types:
        security = "OWE"
    elif 0x01 in akm_types or 0x05 in akm_types:
        security = "WPA2-Enterprise"
    elif 0x02 in akm_types or 0x04 in akm_types or 0x06 in akm_types:
        if 0x04 in akm_types:
            security = "WPA2-Personal (FT)"
        else:
            security = "WPA2-Personal"
    else:
        security = "WPA2"  # generic, couldn't determine specific

    return cipher or None, auth_str or None, security


class PwshScanner:
    """Wi-Fi scanner using PowerShell/.NET P/Invoke bridge.

    Works on ARM64 Windows where ctypes-based wlanapi calls fail
    due to libffi struct parameter handling bugs.
    """

    def __init__(self):
        self._interface_name: Optional[str] = None
        self._last_error: Optional[str] = None
        self._debug_callback: Optional[callable] = None

    def set_debug_callback(self, callback: callable) -> None:
        self._debug_callback = callback

    def _debug(self, msg: str) -> None:
        if self._debug_callback:
            self._debug_callback(msg)
        print(f"[pwsh_debug] {msg}", file=sys.stderr, flush=True)

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
        networks: dict[str, Network] = {}

        try:
            proc = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", _BRIDGE_PS1],
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
            )
        except FileNotFoundError:
            self._last_error = "PowerShell not found"
            return self._empty_result()
        except subprocess.TimeoutExpired:
            self._last_error = "PowerShell bridge timed out"
            return self._empty_result()

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
            print(f"[pwsh_debug] JSON parse FAILED: {e}", file=sys.stderr, flush=True)
            print(f"[pwsh_debug] First 500 chars: {proc.stdout[:500]}", file=sys.stderr, flush=True)
            self._last_error = f"JSON parse error: {proc.stdout[:200]}"
            if proc.stderr:
                self._last_error += f" | stderr: {proc.stderr.strip()[:200]}"
            return self._empty_result()

        if data.get("error"):
            self._last_error = data["error"]
            self._debug(f"pwsh bridge error: {data['error']}")
            return self._empty_result()

        self._interface_name = data.get("interface", "")
        entries = data.get("entries", [])
        self._debug(f"pwsh: {len(entries)} BSS entries, {len(networks)} networks")

        for raw in entries:
            # Parse with ORIGINAL test_bss_bridge key names
            ssid = raw.get("ssid", "")
            bssid = raw.get("bssid", "")
            rssi = raw.get("rssi", 0)
            freq_khz = raw.get("channelFrequency", 0)
            phy_num = raw.get("phyType", 0)
            max_rate = raw.get("maxRate", 0.0)
            ie_hex = raw.get("ieHex", "")
            cap = raw.get("capabilities", 0)

            band = _freq_to_band(freq_khz)
            channel = _freq_to_channel(freq_khz, band)
            phy_str = _PHY_SHORT.get(phy_num, _PHY_TYPE_MAP.get(phy_num, f"phy({phy_num})"))

            # Parse IEs
            ie_info = _parse_ie_hex(ie_hex, band)
            channel_width = ie_info["width"] or 20
            if ie_info["phy"] and ie_info["phy"] != "Unknown":
                phy_str = ie_info["phy"]
            security_str = ie_info["security"]
            cipher_str = ie_info["cipher"]
            auth_str = ie_info["auth"]
            wifi_gen = ie_info["generation"]
            wifi_modes = ie_info["modes"]
            sta_count = ie_info.get("station_count", 0)
            chan_util = ie_info.get("channel_utilization", 0)

            self._debug(
                f"  {bssid} ch={channel} band={band} phy={phy_str} "
                f"width={channel_width}MHz ie_bytes={len(ie_hex)//2}")

            # Security fallback from capabilities field (Privacy bit)
            if not security_str:
                if cap & 0x0010:
                    security_str = "WPA/WPA2"
                else:
                    security_str = "Open"

            bss = BSSEntry(
                ssid=ssid,
                bssid=bssid,
                rssi=rssi,
                channel=channel,
                band=band,
                center_frequency=int(freq_khz / 1000),
                channel_width=channel_width,
                phy_type=phy_str,
                security=security_str or "Unknown",
                cipher=cipher_str,
                auth_algo=auth_str,
                wifi_generation=wifi_gen,
                supported_modes=wifi_modes,
                max_rate=max_rate,
                vendor=lookup_vendor(bssid),
                station_count=sta_count,
                channel_utilization=chan_util,
            )

            if ssid not in networks:
                networks[ssid] = Network(ssid=ssid)
            networks[ssid].bss_list.append(bss)

        for net in networks.values():
            net.refresh_summary()

        result = ScanResult(
            networks=list(networks.values()),
            interface_name=self._interface_name or "",
        )
        self._debug(f"pwsh result: {len(result.networks)} networks, {sum(len(n.bss_list) for n in result.networks)} BSS total")
        return result
