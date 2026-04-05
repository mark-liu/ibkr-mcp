"""Contract search tool."""

from __future__ import annotations

import json

from fastmcp import Context

from ibkr_mcp.models import ContractSearchInput


async def ibkr_contract_search(pattern: str, ctx: Context) -> str:
    """Search for contracts by name or symbol using IB's fuzzy matching.

    Args:
        pattern: Search text (e.g. "Apple", "AAPL", "Bitcoin")
    """
    parsed = ContractSearchInput(pattern=pattern)
    client = ctx.request_context.lifespan_context["client"]
    try:
        result = await client.search_contracts(parsed.pattern)
        return json.dumps(result, indent=2)
    except ConnectionError as e:
        return json.dumps({"error": str(e)})
