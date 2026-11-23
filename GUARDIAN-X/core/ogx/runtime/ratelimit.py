"""Thread-safe token-bucket rate limiter with bounded memory (LRU eviction)."""
from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from typing import Callable


class RateLimiter:
    def __init__(self, max_clients: int = 10_000, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._max = max(100, int(max_clients))
        self._lock = threading.Lock()
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()  # key -> (tokens, last_seen)

    def allow(self, key: str, requests_per_minute: int, burst: int) -> tuple[bool, int]:
        """Consume one token. Returns (allowed, retry_after_seconds)."""
        rate = max(requests_per_minute, 1) / 60.0
        capacity = float(max(burst, 1))
        now = self._clock()
        with self._lock:
            tokens, last = self._buckets.get(key, (capacity, now))
            tokens = min(capacity, tokens + max(0.0, now - last) * rate)
            if tokens >= 1.0:
                tokens -= 1.0
                allowed, retry = True, 0
            else:
                allowed, retry = False, max(1, math.ceil((1.0 - tokens) / rate))
            self._buckets[key] = (tokens, now)
            self._buckets.move_to_end(key)
            while len(self._buckets) > self._max:
                self._buckets.popitem(last=False)
        return allowed, retry

    def __len__(self) -> int:
        return len(self._buckets)
