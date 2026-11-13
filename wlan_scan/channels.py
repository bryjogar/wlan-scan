"""Channel / frequency helpers shared by every scanner and the GUI.

All frequencies are MHz. Channel numbering per IEEE 802.11-2020 Annex E:
    2.4 GHz  ch 1-13 = 2407 + 5*ch, ch 14 = 2484
    5 GHz    ch = (f - 5000) / 5
    6 GHz    ch = (f - 5950) / 5, plus ch 2 at 5935 MHz
"""

from __future__ import annotations

BAND_24 = "2.4 GHz"
BAND_5 = "5 GHz"
BAND_6 = "6 GHz"

#: Widest channel each band allows (MHz).
MAX_WIDTH = {BAND_24: 40, BAND_5: 160, BAND_6: 320}


def freq_to_channel(mhz: float) -> tuple[int, str]:
    """Centre frequency (MHz) → (channel, band). (0, "Unknown") if unrecognised."""
    f = int(round(mhz))
    if f == 2484:
        return 14, BAND_24
    if 2412 <= f <= 2472 and (f - 2407) % 5 == 0:
        return (f - 2407) // 5, BAND_24
    if f == 5935:
        return 2, BAND_6
    if 5150 <= f <= 5895 and f % 5 == 0:
        return (f - 5000) // 5, BAND_5
    if 5955 <= f <= 7115 and (f - 5950) % 5 == 0:
        return (f - 5950) // 5, BAND_6
    return 0, "Unknown"


def channel_to_freq(channel: int, band: str) -> int:
    """(channel, band) → centre frequency in MHz (0 if unknown)."""
    if not channel:
        return 0
    if band == BAND_24:
        return 2484 if channel == 14 else 2407 + 5 * channel
    if band == BAND_5:
        return 5000 + 5 * channel
    if band == BAND_6:
        return 5935 if channel == 2 else 5950 + 5 * channel
    return 0


def band_from_channel(channel: int) -> str:
    """Guess band from a channel number alone (no 6 GHz: its numbers overlap)."""
    if 1 <= channel <= 14:
        return BAND_24
    if 32 <= channel <= 177:
        return BAND_5
    return "Unknown"


def clamp_width(width: int, band: str) -> int:
    return min(width, MAX_WIDTH.get(band, width))


def standard_center_channel(primary: int, width: int, band: str) -> int:
    """Centre channel of the standard 5/6 GHz block holding ``primary``.

    5 and 6 GHz use fixed channel plans, so a primary channel plus a width
    pins down the block. 2.4 GHz has no fixed plan (40 MHz can extend either
    way), so it returns 0 there and for anything unrecognised.
    """
    if width <= 20 or not primary:
        return primary if primary else 0
    n = width // 20
    if band == BAND_5:
        if 36 <= primary <= 144:
            base = 36
        elif 149 <= primary <= 177:
            base = 149
        else:
            return 0
        if width > 160:
            return 0
    elif band == BAND_6:
        if not 1 <= primary <= 233:
            return 0
        base = 1
    else:
        return 0
    idx = (primary - base) // 4
    start = (idx // n) * n
    return base + start * 4 + (n - 1) * 2
