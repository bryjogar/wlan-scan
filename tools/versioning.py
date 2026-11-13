"""Derive the app version from the release tag and write wlan_scan/version.py.

Order: $GITHUB_REF_NAME when it is a ``wlan-scan-v*`` tag (CI checks out
without tags), then ``git describe --tags --match 'wlan-scan-v*'``, then
``0.0.0+g<sha>``. Used by build_exe.py, build_portable.py, build_mac.py.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "wlan_scan" / "version.py"
TAG_PREFIX = "wlan-scan-v"


def _git(*args: str) -> str:
    try:
        r = subprocess.run(["git", *args], capture_output=True, text=True, cwd=ROOT, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def version_from_describe(desc: str) -> str:
    """'wlan-scan-v2.1.0' → '2.1.0'; 'wlan-scan-v2.1.0-3-gabc1234' → '2.1.0.post3+gabc1234'."""
    m = re.fullmatch(re.escape(TAG_PREFIX) + r"(\d+\.\d+\.\d+[0-9A-Za-z.\-]*?)(?:-(\d+)-g([0-9a-f]+))?",
                     desc)
    if not m:
        return ""
    base, ahead, sha = m.groups()
    if ahead and ahead != "0":
        return f"{base}.post{ahead}+g{sha}"
    return base


def resolve_version() -> tuple[str, str]:
    """(version, commit sha)."""
    sha = _git("rev-parse", "HEAD")
    ref = os.environ.get("GITHUB_REF_NAME", "")
    if ref.startswith(TAG_PREFIX):
        v = version_from_describe(ref)
        if v:
            return v, sha
    v = version_from_describe(_git("describe", "--tags", "--match", f"{TAG_PREFIX}*"))
    if v:
        return v, sha
    return f"0.0.0+g{sha[:7]}" if sha else "0.0.0+dev", sha


def bundle_version(version: str) -> str:
    """X.Y.Z for CFBundleShortVersionString (no local/post parts)."""
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", version)
    return ".".join(m.groups()) if m else "0.0.0"


def write_version_file(path: Path = VERSION_FILE) -> str:
    version, sha = resolve_version()
    path.write_text(
        "# Auto-generated at build time by tools/versioning.py — do not edit.\n"
        f'__version__ = "{version}"\n'
        f'__commit__ = "{sha}"\n'
        f'__build_time__ = "{datetime.now().isoformat(timespec="seconds")}"\n',
        encoding="utf-8",
    )
    print(f"  Version: {version} ({sha[:7] or 'no git'})")
    return version


if __name__ == "__main__":
    write_version_file()
