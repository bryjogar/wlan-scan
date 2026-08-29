"""Build wlan_scan.icns from wlan_scan.ico (or a PNG source).

Usage: python tools/make_icns.py [source.png|source.ico]
"""
from __future__ import annotations

import io
import struct
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "wlan_scan.ico"
OUT = ROOT / "wlan_scan.icns"


def main() -> None:
    img = Image.open(SRC).convert("RGBA")
    print(f"source: {SRC} ({img.size[0]}x{img.size[1]})")

    # ICNS family: 512@2x (1024), 512, 256@2x (512), 256, 128@2x (256), 128
    icns_sizes = [
        (1024, "ic10"),
        (512, "ic09"),
        (512, "ic08"),
        (256, "ic07"),
        (256, "ic06"),
        (128, "ic05"),
    ]
    entries = []
    for px, key in icns_sizes:
        icon = img if px == img.width else img.resize((px, px), Image.LANCZOS)
        buf = io.BytesIO()
        icon.save(buf, format="PNG")
        entries.append((key, buf.getvalue()))

    with open(OUT, "wb") as f:
        f.write(b"icns")
        total = 8 + sum(8 + len(d) for _, d in entries)
        f.write(struct.pack(">I", total))
        for key, data in entries:
            f.write(key.encode("ascii"))
            f.write(struct.pack(">I", 8 + len(data)))
            f.write(data)
    print(f"wrote {OUT} ({total} bytes, {len(entries)} sizes)")


if __name__ == "__main__":
    main()
