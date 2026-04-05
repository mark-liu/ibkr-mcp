"""Account tools: positions and account summary."""

from __future__ import annotations

import json

from fastmcp import Context


async def ibkr_positions(ctx: Context) -> str:
    """Get all portfolio positions with P&L, market value, and weight %.

    Returns positions sorted by absolute market value (largest first).
    """
    client = ctx.request_context.lifespan_context["client"]
    result = await client.get_positions()
    return json.dumps(result, indent=2)


async def ibkr_account_summary(ctx: Context) -> str:
    """Get account summary: net liquidation, cash, margin, buying power, P&L.

    Returns key account metrics grouped by currency.
    """
    client = ctx.request_context.lifespan_context["client"]
    result = await client.get_account_summary()
    return json.dumps(result, indent=2)
