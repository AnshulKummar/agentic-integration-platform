"""Sliding-window rate limiter for credential endpoints.

In-memory and per process: correct for a single API instance. A multi-instance
deployment needs a shared store (e.g. Redis ZSET per key with ZREMRANGEBYSCORE +
ZCARD); until then the effective limit is ``limit x instances``.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable

from app.platform.errors import RateLimited


class SlidingWindowLimiter:
    def __init__(
        self,
        limit: int,
        window_s: float = 60.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 100_000,
    ) -> None:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> None:
        """Record an attempt for ``key``; raise RateLimited if the window is already full."""
        now = self._clock()
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                if len(self._hits) >= self._max_keys:
                    self._prune(now)
                hits = self._hits[key] = deque()
            self._expire(hits, now)
            if len(hits) >= self.limit:
                retry_after = max(1, math.ceil(hits[0] + self.window_s - now))
                raise RateLimited("too many attempts; try again later", retry_after_s=retry_after)
            hits.append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

    def _expire(self, hits: deque[float], now: float) -> None:
        cutoff = now - self.window_s
        while hits and hits[0] <= cutoff:
            hits.popleft()

    def _prune(self, now: float) -> None:
        for key in list(self._hits):
            hits = self._hits[key]
            self._expire(hits, now)
            if not hits:
                del self._hits[key]
