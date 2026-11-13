"""Build the bundled IEEE OUI snapshot: wlan_scan/resources/oui.txt.gz

    python tools/build_oui.py                 # download the IEEE MA-L list
    python tools/build_oui.py --from oui.csv  # use a local copy

The build scripts call this; if the download fails they keep the
committed snapshot, so a build never depends on the network.
Format and parsing live in wlan_scan/vendors.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wlan_scan.vendors import (BUNDLED, IEEE_URL, download_ieee,  # noqa: E402
                               parse_ieee_csv, write_snapshot)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", help="local oui.csv instead of downloading")
    ap.add_argument("--out", default=str(BUNDLED))
    args = ap.parse_args(argv)
    try:
        text = Path(args.src).read_text(encoding="utf-8") if args.src else download_ieee()
    except Exception as e:
        print(f"OUI download failed ({e}); keeping existing snapshot", file=sys.stderr)
        return 1
    entries = parse_ieee_csv(text)
    if len(entries) < 10000:
        print(f"Only {len(entries)} OUIs parsed; keeping existing snapshot", file=sys.stderr)
        return 1
    write_snapshot(entries, Path(args.out), IEEE_URL + (" (local copy)" if args.src else ""))
    print(f"Wrote {len(entries)} OUIs to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
