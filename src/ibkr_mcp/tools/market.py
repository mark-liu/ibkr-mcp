"""Market data tools: quotes, historical bars, FX rates."""

from __future__ import annotations

import json

from fastmcp import Context

from ibkr_mcp.models import FxRateInput, HistoricalBarsInput, QuoteInput


def _error_or_cached(e: Exception) -> str:
    """Return cached data if available on ConnectionError, otherwise error JSON."""
    cached = getattr(e, "cached_data", None)
    if cached is not None:
        return json.dumps({"stale": True, "data": cached}, indent=2)
    return json.dumps({"error": str(e)})


async def ibkr_quote(symbols: str, ctx: Context) -> str:
    """Get current price quotes for one or more symbols.

    Args:
        symbols: Comma or space separated symbols (max 20). Example: "AAPL MSFT" or "SPY,QQQ"
    """
    parsed = QuoteInput(symbols=symbols)
    client = ctx.lifespan_context["client"]
    try:
        result = await client.get_quote(parsed.symbol_list)
        return json.dumps(result, indent=2)
    except ConnectionError as e:
        return _error_or_cached(e)


async def ibkr_historical_bars(
    symbol: str,
    duration: str = "1 M",
    bar_size: str = "1 day",
    what_to_show: str = "TRADES",
    use_rth: bool = True,
    ctx: Context = None,  # type: ignore[assignment]
) -> str:
    """Get OHLCV historical bars for a symbol.

    Args:
        symbol: Ticker symbol (e.g. "AAPL", "SPY")
        duration: Lookback period in IB format: "1 D", "1 W", "1 M", "1 Y"
        bar_size: Bar size: "1 min", "5 mins", "1 hour", "1 day", "1 week"
        what_to_show: Data type: "TRADES", "MIDPOINT", "BID", "ASK"
        use_rth: Regular trading hours only (default true)
    """
    parsed = HistoricalBarsInput(
        symbol=symbol, duration=duration, bar_size=bar_size,
        what_to_show=what_to_show, use_rth=use_rth,
    )
    client = ctx.lifespan_context["client"]
    try:
        result = await client.get_historical_bars(
            parsed.symbol, parsed.duration, parsed.bar_size,
            parsed.what_to_show, parsed.use_rth,
        )
        return json.dumps(result, indent=2)
    except ConnectionError as e:
        return _error_or_cached(e)


async def ibkr_fx_rate(pair: str, ctx: Context) -> str:
    """Get live FX rate for a currency pair.

    Args:
        pair: Currency pair like "EURUSD", "AUDUSD", "USDJPY"
    """
    parsed = FxRateInput(pair=pair)
    client = ctx.lifespan_context["client"]
    try:
        result = await client.get_fx_rate(parsed.pair)
        return json.dumps(result, indent=2)
    except ConnectionError as e:
        return _error_or_cached(e)
