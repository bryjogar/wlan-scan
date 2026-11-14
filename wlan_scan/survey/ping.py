"""Short ping at a survey point: N echo requests, average and loss.

Runs ``ping -n N`` (Windows) or ``ping -c N`` once and counts reply lines
(``time=1.2 ms`` / ``time<1ms``, same localised words as the Ping dialog).
Sent is the requested count, so missing replies are losses in any language.
"""

from __future__ import annotations

import re
import subprocess
import sys
from typing import Callable, Optional

_REPLY_TIME_RE = re.compile(r"(?:time|Zeit|temps|tiempo)\s*([=<])\s*([\d.,]+)\s*ms",
                            re.IGNORECASE)


def parse_ping_output(text: str, sent: int) -> dict:
    times = []
    for m in _REPLY_TIME_RE.finditer(text):
        v = float(m.group(2).replace(",", "."))
        times.append(v / 2 if m.group(1) == "<" else v)   # "<1ms" → 0.5
    recv = min(len(times), sent)
    return {
        "sent": sent,
        "received": recv,
        "loss_pct": round(100.0 * (sent - recv) / sent, 1) if sent else 0.0,
        "avg_ms": round(sum(times) / len(times), 2) if times else None,
    }


def ping_command(target: str, count: int, platform: str = sys.platform) -> list[str]:
    if platform == "win32":
        return ["ping", "-n", str(count), "-w", "1000", target]
    return ["ping", "-c", str(count), "-W", "1", target] if platform.startswith("linux") \
        else ["ping", "-c", str(count), target]


def ping_summary(target: str, count: int = 5,
                 run: Optional[Callable] = None) -> dict:
    """Blocking (call it in the background). Never raises; errors go in "error"."""
    run = run or subprocess.run
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        cp = run(ping_command(target, count), capture_output=True, text=True,
                 timeout=count * 2 + 5, **kw)
        out = parse_ping_output(cp.stdout or "", count)
    except (OSError, subprocess.SubprocessError) as e:
        out = {"sent": count, "received": 0, "loss_pct": 100.0, "avg_ms": None,
               "error": str(e)}
    out["target"] = target
    return out
