"""Outbound token bucket.

LinkedIn tolerates roughly one to two Voyager requests per minute per account before
flagging it, and design.md section 8d records a session dying after three requests at that
pace, so the default is deliberately slow. A small burst allowance lets a single profile
fetch issue supplementary calls without stalling.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable

from app.errors import RateLimited


class TokenBucket:
    def __init__(
        self,
        rate_seconds: float,
        burst: int,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._rate = rate_seconds
        self._burst = burst
        self._sleep = sleep
        self._clock = clock
        self._tokens = float(burst)
        self._updated = clock()
        self._lock = asyncio.Lock()

    async def acquire(self, max_wait: float | None = None) -> None:
        """Take a token, waiting if one is not ready yet.

        With ``max_wait`` set, a wait longer than that raises RateLimited instead of
        sleeping. Callers serving an HTTP request want this: queueing behind a 30 second
        bucket makes a caller wait minutes with no explanation, where a 429 carrying
        Retry-After tells them exactly when to come back. That is what design.md section 8
        specifies.
        """
        async with self._lock:
            now = self._clock()
            self._tokens = min(self._burst, self._tokens + (now - self._updated) / self._rate)
            self._updated = now
            if self._tokens < 1:
                wait = (1 - self._tokens) * self._rate
                if max_wait is not None and wait > max_wait:
                    raise RateLimited(
                        f"Outbound rate limit reached, retry in {int(wait) + 1} seconds",
                        retry_after=int(wait) + 1,
                    )
                await self._sleep(wait)
                self._tokens = 1
            self._tokens -= 1


class InboundLimiter:
    """Fixed-window limiter for callers of this API, keyed by API key.

    Distinct from TokenBucket: that one paces our calls out to LinkedIn, this one caps how
    fast a client may call us so a single caller cannot monopolise the account's limited
    outbound budget.
    """

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._quota = per_minute
        self._clock = clock
        self._hits: dict[str, list[float]] = {}

    def check(self, key: str) -> int | None:
        """Record a hit. Returns seconds to wait when over quota, otherwise None."""
        now = self._clock()
        window = [hit for hit in self._hits.get(key, []) if now - hit < 60]
        if len(window) >= self._quota:
            self._hits[key] = window
            return max(1, int(60 - (now - window[0])))
        window.append(now)
        self._hits[key] = window
        return None
