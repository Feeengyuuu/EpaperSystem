"""Small in-process sliding-window limiter with bounded client cardinality."""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass
import hashlib
import math
import threading
import time


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after_seconds: float = 0.0


@dataclass
class _Bucket:
    timestamps: deque
    window_seconds: float
    last_seen: float


class BoundedRateLimiter:
    def __init__(
        self,
        *,
        default_limit=60,
        default_window_seconds=60.0,
        max_keys=2048,
        clock=time.monotonic,
    ):
        self.default_limit = self._limit(default_limit)
        self.default_window_seconds = self._window(default_window_seconds)
        if isinstance(max_keys, bool) or not isinstance(max_keys, int):
            raise ValueError("max_keys must be a positive integer")
        if not 1 <= max_keys <= 100_000:
            raise ValueError("max_keys must be between 1 and 100000")
        self.max_keys = max_keys
        self._clock = clock
        self._lock = threading.Lock()
        self._buckets: OrderedDict[tuple[str, str], _Bucket] = OrderedDict()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._buckets)

    def check(
        self,
        client,
        action,
        *,
        limit=None,
        window_seconds=None,
    ) -> RateLimitResult:
        effective_limit = self.default_limit if limit is None else self._limit(limit)
        effective_window = (
            self.default_window_seconds
            if window_seconds is None
            else self._window(window_seconds)
        )
        key = (self._bounded_key(client), self._bounded_key(action))
        now = float(self._clock())
        with self._lock:
            self._prune_inactive_locked(now)
            bucket = self._buckets.pop(key, None)
            if bucket is None:
                while len(self._buckets) >= self.max_keys:
                    self._buckets.popitem(last=False)
                bucket = _Bucket(deque(), effective_window, now)
            bucket.window_seconds = effective_window
            bucket.last_seen = now
            cutoff = now - effective_window
            while bucket.timestamps and bucket.timestamps[0] <= cutoff:
                bucket.timestamps.popleft()
            if len(bucket.timestamps) >= effective_limit:
                retry_after = max(
                    0.001,
                    bucket.timestamps[0] + effective_window - now,
                )
                self._buckets[key] = bucket
                return RateLimitResult(False, retry_after)
            bucket.timestamps.append(now)
            self._buckets[key] = bucket
            return RateLimitResult(True, 0.0)

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()

    def _prune_inactive_locked(self, now) -> None:
        expired = [
            key
            for key, bucket in self._buckets.items()
            if now - bucket.last_seen > bucket.window_seconds
        ]
        for key in expired:
            self._buckets.pop(key, None)

    @staticmethod
    def _bounded_key(value) -> str:
        text = str(value or "unknown")
        if len(text) <= 128:
            return text
        return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()

    @staticmethod
    def _limit(value) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("rate limit must be a positive integer")
        if not 1 <= value <= 1000:
            raise ValueError("rate limit must be between 1 and 1000")
        return value

    @staticmethod
    def _window(value) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("rate window must be finite and positive") from error
        if not math.isfinite(number) or not 0.1 <= number <= 24 * 60 * 60:
            raise ValueError("rate window must be between 0.1 seconds and 1 day")
        return number
