"""Async token bucket used to stay under the Gemini free-tier requests-per-minute limits."""

import asyncio
import time
from collections.abc import Awaitable, Callable


class TokenBucket:
    """Capacity `rpm`, refilled at rpm/60 tokens per second. Starts full."""

    def __init__(
        self,
        rpm: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rpm < 1:
            raise ValueError("rpm must be >= 1")
        self._capacity = float(rpm)
        self._rate = rpm / 60.0
        self._tokens = float(rpm)
        self._clock = clock
        self._sleep = sleep
        self._updated = clock()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._rate)
        self._updated = now

    async def acquire(self) -> None:
        # Waiting while holding the lock keeps waiters FIFO and the accounting simple.
        async with self._lock:
            self._refill()
            while self._tokens < 1.0:
                await self._sleep((1.0 - self._tokens) / self._rate)
                self._refill()
            self._tokens -= 1.0
