"""Minimal async dispatch queue for bridge calls.

MA2 Bridge-Calls asynchronisiert (MA3-Pattern), Payload vollständig erhalten.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Callable

_log = logging.getLogger(__name__)

_q: "queue.Queue[Callable[[], None] | object]" = queue.Queue(maxsize=10000)
_sentinel: object = object()
_shutdown = False
_lock = threading.Lock()


def _worker() -> None:
    while True:
        try:
            fn = _q.get()
            if fn is _sentinel:
                _q.task_done()
                break
            callable_fn = fn
            assert callable(callable_fn)
            callable_fn()
        except Exception:  # pragma: no cover - defensive fallback
            _log.exception("async task failed")
        finally:
            _q.task_done()


_thread = threading.Thread(target=_worker, name="AsyncBridge", daemon=True)
_thread.start()


def enqueue(fn: Callable[[], None]) -> None:
    """Schedule *fn* for background execution without blocking the UI."""

    if fn is None:
        return
    if _shutdown:
        return
    try:
        _q.put_nowait(fn)
    except queue.Full:
        _log.warning("async queue full; dropping event")


def shutdown(timeout: float = 2.0) -> None:
    """Stop the background worker and drop any queued callbacks."""

    global _thread, _shutdown
    with _lock:
        if _shutdown:
            return
        _shutdown = True
        try:
            _q.put_nowait(_sentinel)
        except queue.Full:
            try:
                item = _q.get_nowait()
            except queue.Empty:
                pass
            else:
                _q.task_done()
            _q.put(_sentinel)
        if _thread is not None:
            _thread.join(timeout)
        while True:
            try:
                leftover = _q.get_nowait()
            except queue.Empty:
                break
            else:
                _q.task_done()
        _thread = None
