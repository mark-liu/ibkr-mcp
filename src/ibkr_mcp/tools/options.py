"""Options tools: option chain discovery."""

from __future__ import annotations

import json

from fastmcp import Context

from ibkr_mcp.models import OptionChainInput


async def ibkr_option_chain(symbol: str, exchange: str = "", ctx: Context = None) -> str:  # type: ignore[assignment]
    """Get available option expirations and strikes for a symbol.

    Returns the chain structure (what expirations and strikes exist),
    not Greeks for individual contracts. Use ibkr_quote with specific
    option symbols for Greeks.

    Args:
        symbol: Underlying symbol (e.g. "AAPL", "FCX")
        exchange: Optional exchange filter (empty = all exchanges)
    """
    parsed = OptionChainInput(symbol=symbol, exchange=exchange)
    client = ctx.request_context.lifespan_context["client"]
    try:
        result = await client.get_option_chain(parsed.symbol, parsed.exchange)
        return json.dumps(result, indent=2)
    except ConnectionError as e:
        return json.dumps({"error": str(e)})
