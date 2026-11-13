"""Run a blocking call on a daemon thread and deliver the result on the GUI thread."""

from __future__ import annotations

import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal


class _Relay(QObject):
    done = Signal(object)


_live: set[_Relay] = set()


def run_in_background(fn: Callable[[], Any], on_done: Callable[[Any], None],
                      parent: QObject | None = None) -> threading.Thread:
    """Call ``fn()`` on a daemon thread; ``on_done(result)`` runs on the GUI
    thread (queued signal). Exceptions are delivered as ``None``.
    """
    relay = _Relay(parent)
    _live.add(relay)

    def finish(result):
        _live.discard(relay)
        try:
            on_done(result)
        finally:
            relay.deleteLater()

    relay.done.connect(finish)

    def work():
        try:
            result = fn()
        except Exception:
            result = None
        relay.done.emit(result)

    th = threading.Thread(target=work, name="bg-task", daemon=True)
    th.start()
    return th
