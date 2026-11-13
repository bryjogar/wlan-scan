"""Build spec-shaped 802.11 information elements for tests and fixtures.

Field layouts follow IEEE 802.11-2020 / 802.11ax-2021 / 802.11be. The blobs
are synthetic (not over-the-air captures) but byte-accurate, so they test
the parser against the standard rather than against itself.
"""

from __future__ import annotations

import struct

OUI_IEEE = b"\x00\x0f\xac"
OUI_MS = b"\x00\x50\xf2"


def ie(eid: int, body: bytes) -> bytes:
    assert len(body) <= 255
    return bytes([eid, len(body)]) + body


def ext(ext_id: int, body: bytes) -> bytes:
    return ie(255, bytes([ext_id]) + body)


def ssid(name: str) -> bytes:
    return ie(0, name.encode())


def rates(mbps=(6, 9, 12, 18, 24, 36, 48, 54), basic=(6, 12, 24)) -> bytes:
    out = bytearray()
    for r in mbps:
        b = int(r * 2)
        if r in basic:
            b |= 0x80
        out.append(b)
    return ie(1, bytes(out))


def ds(channel: int) -> bytes:
    return ie(3, bytes([channel]))


def bss_load(stations: int, util_raw: int, aac: int = 0) -> bytes:
    return ie(11, struct.pack("<HBH", stations, util_raw, aac))


def ht_cap(nss: int = 2, width40: bool = True, sgi20: bool = True,
           sgi40: bool = True) -> bytes:
    info = (0x0002 if width40 else 0) | (0x0020 if sgi20 else 0) | (0x0040 if sgi40 else 0)
    mcs = bytearray(16)
    for i in range(nss):
        mcs[i] = 0xFF
    return ie(45, struct.pack("<HB", info, 0x17) + bytes(mcs) + b"\x00\x00" + b"\x00" * 4 + b"\x00")


def ht_op(primary: int, sec_offset: int = 0, sta_width: int = 0) -> bytes:
    """sec_offset: 0 none, 1 above (SCA), 3 below (SCB)."""
    info1 = (sec_offset & 3) | ((sta_width & 1) << 2)
    return ie(61, bytes([primary, info1]) + b"\x00" * 4 + b"\x00" * 16)


def _mcs_map(nss: int, mcs_code: int) -> int:
    """VHT/HE MCS map: 2 bits per stream, 3 = not supported."""
    m = 0
    for s in range(8):
        m |= ((mcs_code if s < nss else 3) << (2 * s))
    return m


def vht_cap(nss: int = 2, width_set: int = 0, sgi80: bool = True,
            sgi160: bool = False) -> bytes:
    """width_set: 0 = 80 only, 1 = 160, 2 = 160 + 80+80."""
    info = (width_set & 3) << 2
    if sgi80:
        info |= 1 << 5
    if sgi160:
        info |= 1 << 6
    mm = _mcs_map(nss, 2)  # MCS 0-9
    return ie(191, struct.pack("<IHHHH", info, mm, 0, mm, 0))


def vht_op(chwidth: int, ccfs0: int, ccfs1: int = 0) -> bytes:
    """chwidth: 0 = 20/40, 1 = 80/160/80+80, 2 = 160 (old), 3 = 80+80 (old)."""
    return ie(192, bytes([chwidth, ccfs0, ccfs1]) + b"\xfc\xff")


def he_cap(nss: int = 2, w40_24: bool = True, w80_5: bool = True,
           w160_5: bool = False) -> bytes:
    mac = b"\x00" * 6
    width = (0x02 if w40_24 else 0) | (0x04 if w80_5 else 0) | (0x08 if w160_5 else 0)
    phy = bytes([width]) + b"\x00" * 10
    mm = _mcs_map(nss, 2)  # HE-MCS 0-11
    mcs = struct.pack("<HH", mm, mm)
    if w160_5:
        mcs += struct.pack("<HH", mm, mm)
    return ext(35, mac + phy + mcs)


def he_op(vht_info: tuple[int, int, int] | None = None,
          six_ghz: tuple[int, int, int, int] | None = None,
          bss_color: int = 5) -> bytes:
    """vht_info = (chwidth, ccfs0, ccfs1); six_ghz = (primary, width, ccfs0, ccfs1)
    with width 0=20 1=40 2=80 3=160."""
    params = 0
    if vht_info is not None:
        params |= 1 << 14
    if six_ghz is not None:
        params |= 1 << 17
    body = params.to_bytes(3, "little") + bytes([bss_color & 0x3F]) + b"\xfc\xff"
    if vht_info is not None:
        body += bytes(vht_info)
    if six_ghz is not None:
        primary, width, c0, c1 = six_ghz
        body += bytes([primary, width & 3, c0, c1, 6])
    return ext(36, body)


def eht_cap(w320: bool = False, nss: int = 2) -> bytes:
    mac = b"\x00\x00"
    phy = bytearray(9)
    if w320:
        phy[0] |= 0x02  # B1: 320 MHz in 6 GHz
    n = nss & 0xF
    mcs = bytes([n | (n << 4)] * 3)  # rx/tx max NSS for MCS 0-9,10-11,12-13
    sets = mcs * (3 if not w320 else 4)  # <=80, 160, (320)
    return ext(108, mac + bytes(phy) + sets)


def eht_op(width: int | None = None, ccfs0: int = 0, ccfs1: int = 0) -> bytes:
    """width: 0=20 1=40 2=80 3=160 4=320 (None = no EHT Operation Info)."""
    params = 0x01 if width is not None else 0
    body = bytes([params]) + b"\x00\x00\x00\x00"
    if width is not None:
        body += bytes([width & 7, ccfs0, ccfs1])
    return ext(106, body)


def rsn(akms=(2,), pairwise=(4,), group=4, mfpc=False, mfpr=False,
        truncate: int | None = None) -> bytes:
    body = struct.pack("<H", 1) + OUI_IEEE + bytes([group])
    body += struct.pack("<H", len(pairwise)) + b"".join(OUI_IEEE + bytes([c]) for c in pairwise)
    body += struct.pack("<H", len(akms)) + b"".join(OUI_IEEE + bytes([a]) for a in akms)
    caps = (0x40 if mfpr else 0) | (0x80 if mfpc else 0)
    body += struct.pack("<H", caps)
    if truncate is not None:
        body = body[:truncate]
    return ie(48, body)


def wpa1(akms=(2,), pairwise=(2,), group=2) -> bytes:
    body = OUI_MS + b"\x01" + struct.pack("<H", 1) + OUI_MS + bytes([group])
    body += struct.pack("<H", len(pairwise)) + b"".join(OUI_MS + bytes([c]) for c in pairwise)
    body += struct.pack("<H", len(akms)) + b"".join(OUI_MS + bytes([a]) for a in akms)
    return ie(221, body)


def join(*parts: bytes) -> bytes:
    return b"".join(parts)


# ---------------------------------------------------------------------------
# Ready-made profiles: (ies, band, primary channel, centre freq MHz)
# ---------------------------------------------------------------------------


def profile_11n_24(ch=6, width40=False) -> bytes:
    return join(ssid("n24"), rates(), ds(ch), ht_cap(nss=2, width40=width40),
                ht_op(ch, 1 if width40 else 0, 1 if width40 else 0), rsn())


def profile_11ac_5(ch=36, ccfs0=42) -> bytes:
    return join(ssid("ac5"), rates(), ht_cap(nss=3), ht_op(ch, 1, 1),
                vht_cap(nss=3), vht_op(1, ccfs0), rsn())


def profile_11ax_24(ch=1) -> bytes:
    # HE caps advertise 40 MHz on 2.4, but the BSS operates at 20 MHz.
    return join(ssid("ax24"), rates(), ds(ch), ht_cap(nss=2, width40=True),
                ht_op(ch, 0, 0), he_cap(nss=2), he_op(), rsn(akms=(2, 8), mfpc=True))


def profile_11ax_5_160(ch=36) -> bytes:
    return join(ssid("ax5"), rates(), ht_cap(nss=4), ht_op(ch, 1, 1),
                vht_cap(nss=4, width_set=1, sgi160=True), vht_op(1, 42, 50),
                he_cap(nss=4, w160_5=True), he_op(), rsn(akms=(8,), mfpc=True, mfpr=True))


def profile_11ax_6(primary=37, ccfs0=39, ccfs1=0, width=2) -> bytes:
    return join(ssid("ax6"), rates(), he_cap(nss=2, w160_5=width == 3),
                he_op(six_ghz=(primary, width, ccfs0, ccfs1)),
                rsn(akms=(8,), mfpc=True, mfpr=True))


def profile_11be_6_320(primary=37) -> bytes:
    return join(ssid("be6"), rates(), he_cap(nss=2, w160_5=True),
                he_op(six_ghz=(primary, 3, 39, 47)),
                eht_cap(w320=True, nss=2), eht_op(4, 47, 31),
                rsn(akms=(8, 24), mfpc=True, mfpr=True))
