"""Connection status tool."""

from __future__ import annotations

import json

from fastmcp import Context


async def ibkr_connection_status(ctx: Context) -> str:
    """Check IB Gateway connection health and configuration.

    Returns connection state, managed accounts, market data type,
    market hours status, and cache statistics.
    """
    client = ctx.request_context.lifespan_context["client"]
    result = await client.get_connection_status()
    return json.dumps(result, indent=2)
