"""Utility functions: NaN handling, rate limiting."""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any


# ── NaN handling ───────────────────────────────────────────────────────────

def clean_nan(value: Any) -> Any:
    """Convert NaN/Inf floats to None for JSON safety."""
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def ticker_to_dict(ticker: Any) -> dict[str, Any]:
    """Extract price fields from an ib_async Ticker, cleaning NaN values."""
    last = clean_nan(getattr(ticker, "last", None))
    close = clean_nan(getattr(ticker, "close", None))
    bid = clean_nan(getattr(ticker, "bid", None))
    ask = clean_nan(getattr(ticker, "ask", None))
    volume = clean_nan(getattr(ticker, "volume", None))

    # Compute change from previous close
    change = None
    change_pct = None
    if last is not None and close is not None and close != 0:
        change = round(last - close, 4)
        change_pct = round((last - close) / close * 100, 2)

    return {
        "bid": bid,
        "ask": ask,
        "last": last,
        "close": close,
        "change": change,
        "change_pct": change_pct,
        "volume": volume,
    }


# ── Rate limiter ───────────────────────────────────────────────────────────

class RateLimiter:
    """Token bucket rate limiter for IB API calls."""

    def __init__(self, max_per_second: float) -> None:
        self._interval = 1.0 / max_per_second
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._last + self._interval - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()

    async def __aexit__(self, *exc: Any) -> None:
        pass


# Global rate limiters matching IB API limits
market_data_limiter = RateLimiter(max_per_second=45)
historical_data_limiter = RateLimiter(max_per_second=1)
