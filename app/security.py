
"""Basic in-memory request protections for the public demo."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock


class InMemoryRateLimiter:
    """A small per-client sliding-window rate limiter."""

    def __init__(self, window_seconds: int = 60) -> None:
        self.window_seconds = window_seconds
        self._requests: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(
        self,
        client_id: str,
        category: str,
        limit: int,
    ) -> bool:
        now = time.monotonic()
        key = (client_id, category)
        cutoff = now - self.window_seconds

        with self._lock:
            requests = self._requests[key]

            while requests and requests[0] <= cutoff:
                requests.popleft()

            if len(requests) >= limit:
                return False

            requests.append(now)
            return True
