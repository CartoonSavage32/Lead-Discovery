from __future__ import annotations

import asyncio
import time

from app.rate_limit import RateLimiter


def test_rate_limiter_enforces_interval():
    async def run() -> float:
        limiter = RateLimiter(requests_per_minute=120)
        started = time.monotonic()
        await limiter.acquire()
        await limiter.acquire()
        return time.monotonic() - started

    elapsed = asyncio.run(run())
    assert elapsed >= 0.4
