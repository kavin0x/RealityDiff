from __future__ import annotations

import logging
import threading
import time

from realitydiff.models import WatchResult
from realitydiff.service import RealityDiff

log = logging.getLogger("realitydiff.watcher")


class WatchLoop:
    """Continuously surveys the internet for every watching claim."""

    def __init__(self, engine: RealityDiff, *, tick_seconds: float = 15.0) -> None:
        self.engine = engine
        self.tick_seconds = tick_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="realitydiff-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                log.exception("watch tick failed")
            self._stop.wait(self.tick_seconds)

    def _tick(self) -> list[WatchResult]:
        results: list[WatchResult] = []
        now = time.time()
        for record in self.engine.store.list_claims():
            if not record.watching or not record.head:
                continue
            last = record.last_watched_at.timestamp() if record.last_watched_at else 0.0
            if now - last < record.watch_interval_seconds:
                continue
            try:
                results.append(self.engine.watch(record.id, author="watcher"))
            except Exception:
                log.exception("watch failed for %s", record.id)
                self.engine.store.mark_watched(record.id)
        return results
