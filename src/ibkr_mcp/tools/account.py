"""Account tools: positions and account summary."""

from __future__ import annotations

import json

from fastmcp import Context


def _error_or_cached(e: Exception) -> str:
    """Return cached data if available on ConnectionError, otherwise error JSON."""
    cached = getattr(e, "cached_data", None)
    if cached is not None:
        return json.dumps({"stale": True, "data": cached}, indent=2)
    return json.dumps({"error": str(e)})


async def ibkr_positions(ctx: Context) -> str:
    """Get all portfolio positions with P&L, market value, and weight %.

    Returns positions sorted by absolute market value (largest first).
    """
    client = ctx.request_context.lifespan_context["client"]
    try:
        result = await client.get_positions()
        return json.dumps(result, indent=2)
    except ConnectionError as e:
        return _error_or_cached(e)


async def ibkr_account_summary(ctx: Context) -> str:
    """Get account summary: net liquidation, cash, margin, buying power, P&L.

    Returns key account metrics grouped by currency.
    """
    client = ctx.request_context.lifespan_context["client"]
    try:
        result = await client.get_account_summary()
        return json.dumps(result, indent=2)
    except ConnectionError as e:
        return _error_or_cached(e)
