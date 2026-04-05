"""IBKR Gateway client with connection management, caching, and market hours detection."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ib_async import IB, Contract, Forex, Stock

from ibkr_mcp.cache import ContractCache, ResponseCache
from ibkr_mcp.config import IBKRConfig
from ibkr_mcp.utils import clean_nan, market_data_limiter, historical_data_limiter, ticker_to_dict

logger = logging.getLogger(__name__)


def _is_market_open() -> bool:
    """Check if NYSE is currently in session using exchange_calendars."""
    try:
        import exchange_calendars as xcals
        import pandas as pd

        cal = xcals.get_calendar("XNYS")
        now = pd.Timestamp.now(tz="America/New_York")
        return cal.is_open_on_minute(now)
    except Exception:
        return False


class IBKRClient:
    """Wrapper around ib_async.IB with connection management and caching."""

    def __init__(self, config: IBKRConfig, contract_cache: ContractCache, response_cache: ResponseCache) -> None:
        self._config = config
        self._ib = IB()
        self._contract_cache = contract_cache
        self._response_cache = response_cache
        self._reconnecting = False  # guard for reconnect loop only
        self._reconnect_task: asyncio.Task[None] | None = None
        self._last_error: str | None = None

        self._ib.disconnectedEvent += self._on_disconnect

    # ── Connection lifecycle ───────────────────────────────────────────────

    async def connect(self) -> None:
        """Connect to IB Gateway."""
        try:
            await self._ib.connectAsync(
                self._config.host,
                self._config.port,
                clientId=self._config.client_id,
                timeout=10,
                readonly=True,
            )
            mdt = 1 if _is_market_open() else self._config.market_data_type
            self._ib.reqMarketDataType(mdt)
            self._reconnecting = False
            self._last_error = None
            logger.info("Connected to IB Gateway at %s:%d (market data type: %d)",
                        self._config.host, self._config.port, mdt)
        except Exception as e:
            self._last_error = str(e)
            raise

    async def disconnect(self) -> None:
        """Disconnect and cancel any reconnect task."""
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
        self._ib.disconnect()
        self._reconnecting = False

    def _on_disconnect(self) -> None:
        self._reconnecting = True
        self._last_error = "Disconnected from IB Gateway"
        logger.warning("Disconnected from IB Gateway — starting reconnect loop")
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        """Background loop that retries connection until successful."""
        while self._reconnecting and not self.is_connected:
            await asyncio.sleep(self._config.reconnect_interval)
            try:
                await self.connect()
                logger.info("Reconnected to IB Gateway")
            except Exception as e:
                logger.debug("Reconnect attempt failed: %s", e)

    @property
    def is_connected(self) -> bool:
        return self._ib.isConnected()

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def _require_connected(self, cache_key: str | None = None) -> None:
        """Raise ConnectionError if not connected. Returns cached data if available."""
        if not self.is_connected:
            if cache_key:
                cached = self._response_cache.get(cache_key)
                if cached is not None:
                    data, _ = cached
                    # Raise with cached data attached for callers to handle
                    err = ConnectionError("Not connected to IB Gateway")
                    err.cached_data = data  # type: ignore[attr-defined]
                    raise err
            raise ConnectionError("Not connected to IB Gateway")

    # ── Contract resolution with caching ───────────────────────────────────

    async def _qualify(self, contract: Contract) -> Contract:
        """Qualify a single contract, using cache when possible."""
        key = ContractCache.make_key(
            contract.symbol,
            contract.secType or "STK",
            contract.exchange or "SMART",
            contract.currency or "USD",
        )
        cached = self._contract_cache.get(key)
        if cached is not None:
            return cached

        async with market_data_limiter:
            qualified = await self._ib.qualifyContractsAsync(contract)

        if qualified:
            self._contract_cache.put(key, qualified[0])
            return qualified[0]
        raise ValueError(f"Could not qualify contract: {contract.symbol}")

    async def _qualify_stocks(self, symbols: list[str]) -> list[Contract]:
        """Qualify multiple stock contracts, batching cache misses."""
        results: list[Contract] = []
        to_qualify: list[tuple[int, Contract]] = []

        for i, sym in enumerate(symbols):
            key = ContractCache.make_key(sym)
            cached = self._contract_cache.get(key)
            if cached is not None:
                results.append(cached)
            else:
                contract = Stock(sym, "SMART", "USD")
                to_qualify.append((i, contract))
                results.append(contract)  # placeholder

        if to_qualify:
            contracts = [c for _, c in to_qualify]
            async with market_data_limiter:
                qualified = await self._ib.qualifyContractsAsync(*contracts)
            for (i, _), q in zip(to_qualify, qualified):
                key = ContractCache.make_key(q.symbol)
                self._contract_cache.put(key, q)
                results[i] = q

        return results

    # ── Data methods ───────────────────────────────────────────────────────

    async def get_quote(self, symbols: list[str]) -> list[dict[str, Any]]:
        """Get current price snapshots for a list of symbols."""
        self._ensure_market_data_type()
        contracts = await self._qualify_stocks(symbols)

        async with market_data_limiter:
            tickers = await self._ib.reqTickersAsync(*contracts)

        result = []
        for contract, ticker in zip(contracts, tickers):
            data = ticker_to_dict(ticker)
            data["symbol"] = contract.symbol
            data["exchange"] = contract.exchange
            data["currency"] = contract.currency
            result.append(data)

        self._response_cache.put("quote:" + ",".join(symbols), result)
        return result

    async def get_historical_bars(
        self,
        symbol: str,
        duration: str = "1 M",
        bar_size: str = "1 day",
        what_to_show: str = "TRADES",
        use_rth: bool = True,
    ) -> list[dict[str, Any]]:
        """Get OHLCV bars for a symbol."""
        contract = await self._qualify(Stock(symbol, "SMART", "USD"))

        async with historical_data_limiter:
            bars = await self._ib.reqHistoricalDataAsync(
                contract,
                endDateTime="",
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow=what_to_show,
                useRTH=use_rth,
            )

        result = [
            {
                "date": str(bar.date),
                "open": clean_nan(bar.open),
                "high": clean_nan(bar.high),
                "low": clean_nan(bar.low),
                "close": clean_nan(bar.close),
                "volume": clean_nan(bar.volume),
                "average": clean_nan(bar.average),
                "bar_count": bar.barCount,
            }
            for bar in bars
        ]
        self._response_cache.put(f"bars:{symbol}:{duration}:{bar_size}", result)
        return result

    async def get_positions(self) -> list[dict[str, Any]]:
        """Get all portfolio positions with P&L."""
        positions = self._ib.positions()
        portfolio = self._ib.portfolio()

        # Build lookup from portfolio items (has market value and P&L)
        pf_lookup: dict[int, Any] = {}
        for item in portfolio:
            pf_lookup[item.contract.conId] = item

        # Get NLV for weight calculation
        nlv = await self._get_nlv()

        result = []
        for pos in positions:
            con = pos.contract
            pf_item = pf_lookup.get(con.conId)
            market_value = clean_nan(pf_item.marketValue) if pf_item else None
            unrealized_pnl = clean_nan(pf_item.unrealizedPNL) if pf_item else None
            market_price = clean_nan(pf_item.marketPrice) if pf_item else None

            weight_pct = None
            if market_value is not None and nlv and nlv > 0:
                weight_pct = round(market_value / nlv * 100, 2)

            pnl_pct = None
            if unrealized_pnl is not None and pos.avgCost and pos.avgCost > 0 and pos.position:
                cost_basis = abs(float(pos.position)) * pos.avgCost
                if cost_basis > 0:
                    pnl_pct = round(unrealized_pnl / cost_basis * 100, 2)

            result.append({
                "symbol": con.localSymbol or con.symbol,
                "sec_type": con.secType,
                "exchange": con.exchange,
                "currency": con.currency,
                "quantity": float(pos.position),
                "avg_cost": round(pos.avgCost, 4),
                "market_price": market_price,
                "market_value": market_value,
                "unrealized_pnl": unrealized_pnl,
                "unrealized_pnl_pct": pnl_pct,
                "weight_pct": weight_pct,
                "con_id": con.conId,
            })

        result.sort(key=lambda x: abs(x.get("market_value") or 0), reverse=True)
        self._response_cache.put("positions", result)
        return result

    async def get_account_summary(self) -> dict[str, Any]:
        """Get account summary: NLV, cash, margin, buying power, P&L."""
        summary_items = await self._ib.accountSummaryAsync()

        tags_of_interest = {
            "NetLiquidation", "TotalCashValue", "BuyingPower",
            "GrossPositionValue", "MaintMarginReq", "AvailableFunds",
            "ExcessLiquidity", "Cushion", "UnrealizedPnL", "RealizedPnL",
        }

        result: dict[str, Any] = {}
        for item in summary_items:
            if item.tag in tags_of_interest:
                val = item.value
                try:
                    val = round(float(val), 2)
                except (ValueError, TypeError):
                    pass
                key = item.tag
                if item.currency:
                    key = f"{item.tag}_{item.currency}"
                result[key] = val

        result["accounts"] = list(self._ib.managedAccounts())
        self._response_cache.put("account_summary", result)
        return result

    async def get_option_chain(self, symbol: str, exchange: str = "") -> dict[str, Any]:
        """Get available option expirations and strikes for a symbol."""
        contract = await self._qualify(Stock(symbol, "SMART", "USD"))

        async with market_data_limiter:
            chains = await self._ib.reqSecDefOptParamsAsync(
                contract.symbol, exchange, contract.secType, contract.conId,
            )

        result: list[dict[str, Any]] = []
        for chain in chains:
            result.append({
                "exchange": chain.exchange,
                "trading_class": chain.tradingClass,
                "multiplier": chain.multiplier,
                "expirations": sorted(chain.expirations),
                "strikes": sorted(chain.strikes),
            })

        return {"symbol": symbol, "con_id": contract.conId, "chains": result}

    async def search_contracts(self, pattern: str) -> list[dict[str, Any]]:
        """Fuzzy search for contracts by name or symbol."""
        async with market_data_limiter:
            descriptions = await self._ib.reqMatchingSymbolsAsync(pattern)

        return [
            {
                "symbol": d.contract.symbol,
                "sec_type": d.contract.secType,
                "primary_exchange": d.contract.primaryExchange,
                "currency": d.contract.currency,
                "description": getattr(d, "contractDescription", ""),
                "derivative_types": list(d.derivativeSecTypes) if d.derivativeSecTypes else [],
            }
            for d in (descriptions or [])
        ]

    async def get_fx_rate(self, pair: str) -> dict[str, Any]:
        """Get FX rate for a currency pair (e.g. 'EURUSD')."""
        contract = Forex(pair)
        contract = await self._qualify(contract)

        async with market_data_limiter:
            tickers = await self._ib.reqTickersAsync(contract)

        if not tickers:
            return {"pair": pair, "error": "No data returned"}

        ticker = tickers[0]
        bid = clean_nan(ticker.bid)
        ask = clean_nan(ticker.ask)
        midpoint = round((bid + ask) / 2, 6) if bid is not None and ask is not None else clean_nan(ticker.last)

        return {
            "pair": pair,
            "bid": bid,
            "ask": ask,
            "last": clean_nan(ticker.last),
            "midpoint": midpoint,
        }

    async def get_connection_status(self) -> dict[str, Any]:
        """Get gateway connection health info."""
        market_open = _is_market_open()
        return {
            "connected": self.is_connected,
            "host": self._config.host,
            "port": self._config.port,
            "client_id": self._config.client_id,
            "accounts": list(self._ib.managedAccounts()) if self.is_connected else [],
            "server_version": self._ib.client.serverVersion() if self.is_connected else None,
            "market_data_type": self._config.market_data_type,
            "market_open": market_open,
            "contract_cache_size": self._contract_cache.size,
            "last_error": self._last_error,
        }

    # ── Helpers ─────────────────────────────────────────────────────────────

    async def _get_nlv(self) -> float | None:
        """Get Net Liquidation Value for weight calculations."""
        try:
            summary = await self._ib.accountSummaryAsync()
            for item in summary:
                if item.tag == "NetLiquidation" and item.currency == "USD":
                    return float(item.value)
        except Exception:
            pass
        return None

    def _ensure_market_data_type(self) -> None:
        """Switch market data type based on market hours."""
        if self.is_connected:
            mdt = 1 if _is_market_open() else self._config.market_data_type
            self._ib.reqMarketDataType(mdt)
