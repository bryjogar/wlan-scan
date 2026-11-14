"""Linux scanning: ``iw`` (dBm, IE details) and ``nmcli`` (percent signal).

iw
    ``iw dev <if> scan`` triggers a scan and prints every BSS with its
    signal in dBm and the decoded information elements. Triggering needs
    root or CAP_NET_ADMIN. Without it, ``iw dev <if> scan dump`` still reads
    the kernel's cached results, so the iw backend asks NetworkManager to
    rescan (``nmcli … --rescan yes``, allowed for the logged-in user) and then
    dumps. iw's text is mapped onto the same security / width logic the IE
    parser uses for raw IEs on Windows and macOS.

nmcli
    ``nmcli -t -f ACTIVE,BSSID,SSID,CHAN,FREQ,SIGNAL,SECURITY dev wifi list
    --rescan yes`` works for any logged-in user. SIGNAL is a percent; RSSI is
    converted back with NetworkManager's own mapping (-100…-40 dBm ↔
    0…100 %) and marked as an estimate, as is the channel width (nmcli
    doesn't report it here).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .channels import freq_to_channel, standard_center_channel
from .ie_parser import (AKM_NAMES, CIPHER_NAMES_IE, _apply_security, _vht_style_width)
from .models import BSSEntry, ScanResult, group_networks
from .vendors import lookup_vendor

Runner = Callable[..., subprocess.CompletedProcess]

NMCLI_FIELDS = "ACTIVE,BSSID,SSID,CHAN,FREQ,SIGNAL,SECURITY"


def which(name: str) -> Optional[str]:
    """``shutil.which`` that also looks in sbin (iw lives there on some distros)."""
    return shutil.which(name, path=os.environ.get("PATH", "") + ":/usr/sbin:/sbin")


def _run(cmd: list[str], timeout: float = 30.0, run: Optional[Runner] = None
         ) -> subprocess.CompletedProcess:
    env = dict(os.environ, LC_ALL="C")   # untranslated output
    env["PATH"] = env.get("PATH", "") + ":/usr/sbin:/sbin"   # iw often lives in sbin
    return (run or subprocess.run)(cmd, capture_output=True, text=True, timeout=timeout,
                                   env=env)


# ---------------------------------------------------------------------------
# Interfaces
# ---------------------------------------------------------------------------


def parse_iw_dev(text: str) -> list[str]:
    return re.findall(r"^\s*Interface\s+(\S+)", text, re.MULTILINE)


def wireless_interfaces(run: Optional[Runner] = None,
                        sys_class_net: Path = Path("/sys/class/net")) -> list[str]:
    """Wireless interfaces from ``iw dev``, else /sys/class/net/*/wireless."""
    if which("iw") or run:
        try:
            cp = _run(["iw", "dev"], 5, run)
            names = parse_iw_dev(cp.stdout or "")
            if names:
                return names
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        return sorted(p.name for p in sys_class_net.iterdir() if (p / "wireless").exists())
    except OSError:
        return []


# ---------------------------------------------------------------------------
# nmcli
# ---------------------------------------------------------------------------


def split_terse(line: str) -> list[str]:
    """Split an ``nmcli -t`` line on unescaped ':' and unescape ``\\:`` / ``\\\\``."""
    out, cur, esc = [], [], False
    for ch in line:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def percent_to_dbm(pct: int) -> int:
    """Inverse of NetworkManager's quality mapping (−100…−40 dBm → 0…100 %)."""
    pct = max(0, min(100, int(pct)))
    return int(round(-40 - (100 - pct) * 0.6))


def nm_security(text: str) -> str:
    """nmcli SECURITY flags ("WPA2 WPA3", "WPA1 WPA2 802.1X", "OWE", "--")."""
    t = set(text.replace("--", "").split())
    if "802.1X" in t:
        if "WPA3" in t:
            return "WPA3-Enterprise"
        if "WPA2" in t:
            return "WPA/WPA2-Enterprise" if "WPA1" in t else "WPA2-Enterprise"
        return "WPA-Enterprise" if "WPA1" in t else "802.1X"
    if "OWE" in t:
        return "OWE (Enhanced Open)"
    if "WPA2" in t and "WPA3" in t:
        return "WPA2/WPA3"
    if "WPA3" in t:
        return "WPA3"
    if "WPA2" in t:
        return "WPA/WPA2" if "WPA1" in t else "WPA2"
    if "WPA1" in t:
        return "WPA"
    if "WEP" in t:
        return "WEP"
    return "Open"


def parse_nmcli(text: str, now: Optional[datetime] = None) -> tuple[list[BSSEntry], str]:
    """Rows of ``nmcli -t -f ACTIVE,BSSID,SSID,CHAN,FREQ,SIGNAL,SECURITY``.
    Returns (entries, connected BSSID)."""
    now = now or datetime.now()
    entries, connected, seen = [], "", set()
    for line in text.splitlines():
        if not line.strip():
            continue
        f = split_terse(line)
        if len(f) < 7:
            continue
        active, bssid, ssid, chan, freq, signal, sec = f[:7]
        bssid = bssid.upper()
        if not re.fullmatch(r"[0-9A-F]{2}(:[0-9A-F]{2}){5}", bssid) or bssid in seen:
            continue
        seen.add(bssid)
        try:
            mhz = int(re.match(r"\d+", freq).group())
        except (AttributeError, ValueError):
            mhz = 0
        ch, band = freq_to_channel(mhz) if mhz else (0, "")
        if not ch and chan.isdigit():
            ch = int(chan)
        try:
            rssi = percent_to_dbm(int(signal))
        except ValueError:
            rssi = -100
        if active.lower() == "yes":
            connected = bssid
        entries.append(BSSEntry(
            bssid=bssid, ssid=ssid, rssi=rssi, channel=ch, band=band or "2.4 GHz",
            center_frequency=mhz, security=nm_security(sec), vendor=lookup_vendor(bssid),
            estimated="rssi,channel_width", timestamp=now))
    return entries, connected


def nmcli_scan(rescan: bool = True, run: Optional[Runner] = None,
               ifname: str = "") -> ScanResult:
    """Scan with nmcli. A refused rescan (rate limit, radio off) reads the
    current list instead."""
    base = ["nmcli", "-t", "-e", "yes", "-f", NMCLI_FIELDS, "dev", "wifi", "list"]
    tail = ["ifname", ifname] if ifname else []
    cp = _run(base + ["--rescan", "yes" if rescan else "no"] + tail, 40, run)
    if cp.returncode != 0 and rescan:
        cp = _run(base + ["--rescan", "no"] + tail, 20, run)
    if cp.returncode != 0:
        raise RuntimeError(f"nmcli failed: {(cp.stderr or cp.stdout).strip()}")
    entries, connected = parse_nmcli(cp.stdout or "")
    return ScanResult(networks=group_networks(entries), interface_name=ifname,
                      connected_bssid=connected)


# ---------------------------------------------------------------------------
# iw
# ---------------------------------------------------------------------------

# iw's suite names (util.c) → IEEE AKM / cipher selector numbers.
_IW_AKM = {
    "IEEE 802.1X": 1, "PSK": 2, "FT/IEEE 802.1X": 3, "FT/PSK": 4,
    "IEEE 802.1X/SHA-256": 5, "PSK/SHA-256": 6, "TDLS/TPK": 7, "SAE": 8, "FT/SAE": 9,
    "IEEE 802.1X/SUITE-B": 11, "IEEE 802.1X/SUITE-B-192": 12,
    "FT/IEEE 802.1X/SHA-384": 13, "FILS/SHA-256": 14, "FILS/SHA-384": 15,
    "FT/FILS/SHA-256": 16, "FT/FILS/SHA-384": 17, "OWE": 18,
    "FT/PSK/SHA-384": 19, "PSK/SHA-384": 20, "SAE-EXT-KEY": 24, "FT/SAE-EXT-KEY": 25,
}
_IW_CIPHER = {
    "WEP-40": 1, "TKIP": 2, "CCMP": 4, "CCMP-128": 4, "WEP-104": 5, "AES-128-CMAC": 6,
    "BIP-CMAC-128": 6, "GCMP-128": 8, "GCMP": 8, "GCMP-256": 9, "CCMP-256": 10,
    "BIP-GMAC-128": 11, "BIP-GMAC-256": 12, "BIP-CMAC-256": 13,
}
_SUITE_RAW = re.compile(r"00-0f-ac:(\d+)", re.IGNORECASE)


def _suites(value: str, table: dict[str, int]) -> list[int]:
    """Map iw's space-separated suite list. Names can contain spaces
    ("IEEE 802.1X/SHA-256"), so match the longest name at each position;
    unknown suites print as ``00-0f-ac:N``."""
    names = sorted(table, key=len, reverse=True)
    out, rest = [], value.strip()
    while rest:
        m = _SUITE_RAW.match(rest)
        if m:
            out.append(int(m.group(1)))
            rest = rest[m.end():].lstrip()
            continue
        for n in names:
            if rest.startswith(n) and (len(rest) == len(n) or rest[len(n)] == " "):
                out.append(table[n])
                rest = rest[len(n):].lstrip()
                break
        else:
            rest = rest.partition(" ")[2].lstrip()     # unknown word: skip it
    return out


def _sec_dict(block: dict, wpa1: bool = False) -> dict:
    akms = _suites(block.get("Authentication suites", ""), _IW_AKM)
    if wpa1:
        akms = [a for a in akms if a in (1, 2)]
    ciphers = _suites(block.get("Pairwise ciphers", ""), _IW_CIPHER)
    group = _suites(block.get("Group cipher", ""), _IW_CIPHER)
    caps = block.get("Capabilities", "")
    pmf = "Required" if "MFP-required" in caps else ("Capable" if "MFP-capable" in caps
                                                     else "Disabled")
    names = {1: "802.1X", 2: "PSK"} if wpa1 else AKM_NAMES
    return {
        "ok": True,
        "akm_types": set(akms),
        "akms": [names.get(a, f"AKM {a}") for a in akms],
        "ciphers": [CIPHER_NAMES_IE.get(c, f"Cipher {c}") for c in ciphers],
        "group": CIPHER_NAMES_IE.get(group[0], "") if group else "",
        "pmf": pmf,
    }


def _decode_ssid(raw: str) -> str:
    """iw prints non-printable SSID bytes as \\xNN."""
    if "\\x" not in raw:
        return raw
    out = bytearray()
    i = 0
    while i < len(raw):
        if raw.startswith("\\x", i) and re.fullmatch(r"[0-9a-fA-F]{2}", raw[i + 2:i + 4]):
            out.append(int(raw[i + 2:i + 4], 16))
            i += 4
        else:
            out += raw[i].encode("utf-8")
            i += 1
    return out.decode("utf-8", errors="replace")


_BSS_LINE = re.compile(r"^BSS ([0-9a-fA-F:]{17})")


def split_iw_scan(text: str) -> list[list[str]]:
    blocks, cur = [], None
    for line in text.splitlines():
        if _BSS_LINE.match(line):
            cur = [line]
            blocks.append(cur)
        elif cur is not None:
            cur.append(line)
    return blocks


def _subfields(lines: list[str], start: int) -> tuple[dict, int]:
    """Read the ``* key: value`` lines under an element header."""
    d = {}
    i = start
    while i < len(lines):
        s = lines[i].strip()
        if not s.startswith("*"):
            break
        k, _, v = s.lstrip("* ").partition(":")
        d[k.strip()] = v.strip()
        i += 1
    return d, i


def parse_iw_block(lines: list[str], now: Optional[datetime] = None) -> Optional[tuple[BSSEntry, bool]]:
    """One ``BSS …`` block → (BSSEntry, associated)."""
    m = _BSS_LINE.match(lines[0])
    if not m:
        return None
    bssid = m.group(1).upper()
    associated = "-- associated" in lines[0]
    info: dict = {"ssid": "", "freq": 0.0, "signal": None, "privacy": False,
                  "owe_tm": False, "op_width": 0, "quality": False}
    rsn = wpa = ht_op = vht_op = load = None
    gens = set()
    i = 1
    while i < len(lines):
        raw = lines[i]
        s = raw.strip()
        key, _, val = s.partition(":")
        key, val = key.strip(), val.strip()
        if key == "freq":
            try:
                info["freq"] = float(val.split()[0])
            except (ValueError, IndexError):
                pass
        elif key == "signal":
            mm = re.match(r"(-?\d+(?:\.\d+)?)\s*dBm", val)
            mq = re.match(r"(\d+)/100", val)
            if mm:
                info["signal"] = float(mm.group(1))
            elif mq:      # driver reports quality, not dBm
                info["signal"], info["quality"] = float(percent_to_dbm(int(mq.group(1)))), True
        elif key == "OWE Transition Mode":
            info["owe_tm"] = True
        elif key.lstrip("* ") == "BSS operating channel width":
            mm = re.match(r"(\d+)\s*MHz", val)
            if mm:
                info["op_width"] = int(mm.group(1))
        elif key == "SSID" and raw.startswith("\t") and not raw.startswith("\t\t"):
            info["ssid"] = _decode_ssid(raw.split("SSID:", 1)[1].strip())
        elif key == "capability":
            info["privacy"] = "Privacy" in val.split()
        elif key in ("RSN", "WPA"):
            # iw prints the first sub-field on the header line: "RSN:\t * Version: 1"
            first = {}
            if "*" in val:
                k2, _, v2 = val.lstrip("* ").partition(":")
                first[k2.strip()] = v2.strip()
            d, i = _subfields(lines, i + 1)
            first.update(d)
            if key == "RSN":
                rsn = first
            else:
                wpa = first
            continue
        elif key == "HT operation":
            ht_op, i = _subfields(lines, i + 1)
            continue
        elif key == "VHT operation":
            vht_op, i = _subfields(lines, i + 1)
            continue
        elif key == "BSS Load":
            load, i = _subfields(lines, i + 1)
            continue
        elif key == "HT capabilities":
            gens.add(4)
        elif key == "VHT capabilities":
            gens.add(5)
        elif key in ("HE capabilities", "HE Capabilities"):
            gens.add(6)
        elif key in ("EHT capabilities", "EHT Capabilities"):
            gens.add(7)
        i += 1

    mhz = int(round(info["freq"]))
    ch, band = freq_to_channel(mhz) if mhz else (0, "")
    estimated = []
    rssi = info["signal"]
    if rssi is None or info["quality"]:
        rssi, estimated = (-100.0 if rssi is None else rssi), ["rssi"]

    width, center = 20, 0
    found_width = False
    if ht_op is not None:
        found_width = True
        off = ht_op.get("secondary channel offset", "")
        width_any = "any" in ht_op.get("STA channel width", "any")
        if off in ("above", "below") and width_any:
            width, center = 40, ch + (2 if off == "above" else -2)
    if vht_op is not None:
        found_width = True
        try:
            cw = int(vht_op.get("channel width", "0").split()[0])
            s1 = int(vht_op.get("center freq segment 1", "0"))
            s2 = int(vht_op.get("center freq segment 2", "0"))
            w, c = _vht_style_width(cw, s1, s2)
            if w:
                width, center = w, c
        except (ValueError, IndexError):
            pass
    if info["op_width"] in (20, 40, 80, 160, 320) and info["op_width"] >= width:
        found_width = True
        if info["op_width"] != width:
            width = info["op_width"]
            center = standard_center_channel(ch, width, band) if ch else 0
    if not found_width and band in ("5 GHz", "6 GHz"):
        # No operation elements decoded (e.g. 6 GHz HE-only): width unknown.
        estimated.append("channel_width")
        center = standard_center_channel(ch, width, band) if ch else 0

    result: dict = {}
    _apply_security(result, _sec_dict(rsn) if rsn is not None else None,
                    _sec_dict(wpa, wpa1=True) if wpa is not None else None,
                    info["privacy"], info["owe_tm"])
    stations, util = 0, 0.0
    if load:
        try:
            stations = int(load.get("station count", "0").split()[0])
        except (ValueError, IndexError):
            pass
        mm = re.match(r"(\d+)\s*/\s*255", load.get("channel utilisation", ""))
        if mm:
            util = round(int(mm.group(1)) * 100 / 255, 1)

    gen = max(gens) if gens else 0
    gen_name = {4: "Wi-Fi 4", 5: "Wi-Fi 5", 6: "Wi-Fi 6E" if band == "6 GHz" else "Wi-Fi 6",
                7: "Wi-Fi 7"}.get(gen, "")
    phy = {4: "802.11n (HT)", 5: "802.11ac (VHT)", 6: "802.11ax (HE)",
           7: "802.11be (EHT)"}.get(gen, "Unknown")
    entry = BSSEntry(
        bssid=bssid, ssid=info["ssid"], rssi=int(round(rssi)), channel=ch,
        channel_width=width, center_channel=center if center != ch else 0,
        band=band or "2.4 GHz", center_frequency=mhz, phy_type=phy,
        security=result["security"], auth_algo=result["auth_algo"], cipher=result["cipher"],
        pmf=result["pmf"], capability_info=0x0010 if info["privacy"] else 0,
        vendor=lookup_vendor(bssid), wifi_generation=gen_name,
        station_count=stations, channel_utilization=util,
        estimated=",".join(estimated), timestamp=now or datetime.now())
    return entry, associated


def parse_iw_scan(text: str, now: Optional[datetime] = None) -> tuple[list[BSSEntry], str]:
    entries, connected, seen = [], "", set()
    for block in split_iw_scan(text):
        r = parse_iw_block(block, now)
        if r is None:
            continue
        e, assoc = r
        if e.bssid in seen:
            continue
        seen.add(e.bssid)
        entries.append(e)
        if assoc:
            connected = e.bssid
    return entries, connected


def is_permission_error(cp: subprocess.CompletedProcess) -> bool:
    err = (cp.stderr or "") + (cp.stdout or "")
    return "Operation not permitted" in err or "(-1)" in err


def parse_iw_link(text: str) -> str:
    m = re.search(r"Connected to ([0-9a-fA-F:]{17})", text)
    return m.group(1).upper() if m else ""


def linux_connected_bssid(run: Optional[Runner] = None) -> Optional[str]:
    """``iw dev <if> link`` (no root needed), else nmcli's ACTIVE row."""
    if which("iw") or run:
        for ifname in wireless_interfaces(run):
            try:
                b = parse_iw_link(_run(["iw", "dev", ifname, "link"], 5, run).stdout or "")
            except (OSError, subprocess.SubprocessError):
                b = ""
            if b:
                return b
    if which("nmcli") or run:
        try:
            cp = _run(["nmcli", "-t", "-e", "yes", "-f", "ACTIVE,BSSID", "dev", "wifi", "list",
                       "--rescan", "no"], 10, run)
            for line in (cp.stdout or "").splitlines():
                f = split_terse(line)
                if len(f) >= 2 and f[0].lower() == "yes":
                    return f[1].upper()
        except (OSError, subprocess.SubprocessError):
            pass
    return None
