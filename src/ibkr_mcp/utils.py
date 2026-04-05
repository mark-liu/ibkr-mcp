"""Utility functions: NaN handling, formatting, rate limiting, retry."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, TypeVar

T = TypeVar("T")


# ── NaN handling ───────────────────────────────────────────────────────────

def clean_nan(value: Any) -> Any:
    """Convert NaN/Inf floats to None for JSON safety."""
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def clean_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Recursively clean NaN values from a dict."""
    return {k: clean_nan(v) if not isinstance(v, dict) else clean_dict(v) for k, v in d.items()}


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


# ── Formatting ─────────────────────────────────────────────────────────────

def fmt_currency(value: float | None, currency: str = "USD") -> str:
    if value is None:
        return "N/A"
    return f"{value:,.2f} {currency}"


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.2f}%"


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


# ── Retry decorator ────────────────────────────────────────────────────────

def with_retry(
    max_retries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple[type[Exception], ...] = (ConnectionError, asyncio.TimeoutError),
) -> Callable:
    """Retry decorator with exponential backoff for async functions."""
    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exc: Exception | None = None
            current_delay = delay
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt < max_retries:
                        await asyncio.sleep(current_delay)
                        current_delay *= backoff
            raise last_exc  # type: ignore[misc]
        return wrapper
    return decorator
