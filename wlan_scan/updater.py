"""Update check: compare this build's version with the latest GitHub release.

v1 compared the build's git SHA with main's HEAD commit, so any commit (a
README edit) showed "Update available", pointing at a release that could
be older than the running build.
"""

from __future__ import annotations

import json
import re
import ssl
import urllib.request
from dataclasses import dataclass
from typing import Optional

RELEASES_API = "https://api.github.com/repos/bryjogar/wlan-scan/releases/latest"
TAG_PREFIX = "wlan-scan-v"


@dataclass
class UpdateInfo:
    current: str
    latest: str
    url: str
    name: str = ""


def parse_version(v: str) -> Optional[tuple]:
    """'2.1.0' / 'v2.1.0' / 'wlan-scan-v2.1.0' / '2.1.0rc1' → comparable tuple.

    Pre-releases (a/b/rc/dev) sort before the final release; local
    ('+...') and post ('.postN') parts sort after it. None if unparsable.
    """
    v = (v or "").strip()
    if v.startswith(TAG_PREFIX):
        v = v[len(TAG_PREFIX):]
    v = v.lstrip("vV")
    m = re.match(r"(\d+)\.(\d+)(?:\.(\d+))?(?:[.-]?(a|b|rc|dev|post)\.?(\d*))?", v)
    if not m:
        return None
    major, minor, patch, kind, n = m.groups()
    rank = {"dev": -4, "a": -3, "b": -2, "rc": -1, None: 0, "post": 1}[kind]
    return (int(major), int(minor), int(patch or 0), rank, int(n or 0))


def is_newer(latest: str, current: str) -> bool:
    lv, cv = parse_version(latest), parse_version(current)
    return lv is not None and cv is not None and lv > cv


def is_release_build(version: str) -> bool:
    """False for from-source / untagged builds ('0.0.0+dev', '0.0.0+gabc')."""
    p = parse_version(version)
    return p is not None and p[:3] != (0, 0, 0)


def fetch_latest_release(timeout: float = 5) -> Optional[dict]:
    req = urllib.request.Request(RELEASES_API, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "WLAN-Scan-UpdateCheck/2",
    })
    with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
        return json.loads(r.read())


def check_for_updates(current_version: str, fetch=fetch_latest_release) -> Optional[UpdateInfo]:
    """UpdateInfo when the latest release is newer than ``current_version``.

    Run off the GUI thread. Returns None for dev builds, on any network or
    API error, and when up to date.
    """
    if not is_release_build(current_version):
        return None
    try:
        data = fetch()
    except Exception:
        return None
    if not data or data.get("draft") or data.get("prerelease"):
        return None
    tag = data.get("tag_name", "")
    if not is_newer(tag, current_version):
        return None
    latest = tag[len(TAG_PREFIX):] if tag.startswith(TAG_PREFIX) else tag.lstrip("v")
    return UpdateInfo(current=current_version, latest=latest,
                      url=data.get("html_url") or "https://github.com/bryjogar/wlan-scan/releases/latest",
                      name=data.get("name") or tag)
