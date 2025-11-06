"""Lightweight helpers for running background tasks off the UI thread."""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable, Optional, Tuple

log = logging.getLogger(__name__)

__all__ = ["AsyncCallQueue"]


class AsyncCallQueue:
    """Execute callables on a dedicated worker thread."""

    def __init__(
        self,
        name: str = "AsyncCallQueue",
        *,
        maxsize: int = 1000,
        perf_logging: bool = False,
    ) -> None:
        self._name = name
        self._queue: "queue.Queue[Callable[[], None] | object]" = queue.Queue(
            maxsize=maxsize
        )
        self._maxsize = maxsize
        self._perf_logging = perf_logging
        self._last_load_log = 0.0
        self._sentinel: object = object()
        self._closed = False
        self._worker = threading.Thread(target=self._run, name=name, daemon=True)
        self._worker.start()

    def submit(self, fn: Optional[Callable[[], None]]) -> None:
        """Enqueue *fn* for background execution, dropping on saturation."""

        if fn is None or self._closed:
            return
        try:
            self._queue.put_nowait(fn)
        except queue.Full:
            log.warning("%s full – dropping task", self._name)
        else:
            if self._perf_logging and self._maxsize:
                now = time.monotonic()
                if now - self._last_load_log >= 1.0:
                    load = self._queue.qsize() / self._maxsize
                    if load >= 0.8:
                        log.debug("%s load at %.0f%%", self._name, load * 100.0)
                        self._last_load_log = now

    def load(self) -> Tuple[int, int]:
        """Return the current queue length and capacity."""

        return (self._queue.qsize(), self._maxsize)

    def close(self, *, timeout: float = 2.0) -> None:
        """Signal the worker thread to stop and drain pending work."""

        if self._closed:
            return
        self._closed = True
        try:
            self._queue.put_nowait(self._sentinel)
        except queue.Full:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                pass
            else:
                self._queue.task_done()
            self._queue.put(self._sentinel)
        worker = self._worker
        if worker is not None:
            worker.join(timeout)
        self._drain_queue()
        self._worker = None

    # ------------------------------------------------------------------
    def _run(self) -> None:
        queue_obj = self._queue
        sentinel = self._sentinel
        while True:
            fn = queue_obj.get()
            if fn is sentinel:
                queue_obj.task_done()
                break
            start = time.perf_counter()
            try:
                callable_fn = fn  # help mypy
                assert callable(callable_fn)
                callable_fn()
            except Exception:  # pragma: no cover - defensive fallback
                log.exception("%s task failed", self._name)
            finally:
                queue_obj.task_done()
                if self._perf_logging:
                    duration = (time.perf_counter() - start) * 1000.0
                    if duration >= 5.0:
                        log.debug("%s task took %.2f ms", self._name, duration)
        self._drain_queue()

    def _drain_queue(self) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            else:
                self._queue.task_done()
