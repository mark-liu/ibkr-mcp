"""IBKR Gateway client with connection management, caching, and market hours detection."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import subprocess
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
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._last_error: str | None = None
        self._reconnect_failures: int = 0
        self._restart_lock = asyncio.Lock()

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
            await self._start_heartbeat()
            logger.info("Connected to IB Gateway at %s:%d (market data type: %d)",
                        self._config.host, self._config.port, mdt)
        except Exception as e:
            self._last_error = str(e)
            raise

    async def disconnect(self) -> None:
        """Disconnect and cancel any background tasks."""
        self._reconnecting = False
        # Detach event handler before disconnect to prevent _on_disconnect
        # from starting a reconnect loop during intentional shutdown.
        self._ib.disconnectedEvent -= self._on_disconnect
        await self._cancel_task(self._heartbeat_task)
        await self._cancel_task(self._reconnect_task)
        self._ib.disconnect()

    @staticmethod
    async def _cancel_task(task: asyncio.Task[None] | None) -> None:
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def start_reconnect(self) -> None:
        """Start the background reconnect loop (idempotent)."""
        self._reconnecting = True
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    def _on_disconnect(self) -> None:
        self._last_error = "Disconnected from IB Gateway"
        logger.warning("Disconnected from IB Gateway — starting reconnect loop")
        self.start_reconnect()

    async def _reconnect_loop(self) -> None:
        """Background loop that retries connection until successful.

        After `max_reconnect_before_restart` consecutive failures, escalate to
        a full Gateway kill+relaunch — handles the case where the app is stuck
        on a "reconnect" dialog after a long-running session.
        """
        while self._reconnecting and not self.is_connected:
            await asyncio.sleep(self._config.reconnect_interval)
            try:
                self._ib.disconnect()
                self._ib = IB()
                self._ib.disconnectedEvent += self._on_disconnect
                await self.connect()
                logger.info("Reconnected to IB Gateway")
                self._reconnect_failures = 0
            except Exception as e:
                self._reconnect_failures += 1
                logger.debug(
                    "Reconnect attempt %d failed: %s",
                    self._reconnect_failures, e,
                )
                # Either too many socket failures, or the app is alive but
                # wedged on a dialog (port 4001 closed while process alive).
                should_restart = (
                    self._reconnect_failures >= self._config.max_reconnect_before_restart
                    or (self._is_gateway_process_alive() and self._detect_stuck_ui())
                )
                if should_restart:
                    restarted = await self._restart_gateway(
                        reason=f"{self._reconnect_failures} failed reconnects",
                    )
                    if restarted:
                        self._reconnect_failures = 0

    # ── Heartbeat watchdog ───────────────────────────────────────────────

    async def _start_heartbeat(self) -> None:
        """Start the heartbeat watchdog (idempotent). Cancels any existing task."""
        await self._cancel_task(self._heartbeat_task)
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _heartbeat_loop(self) -> None:
        """Periodically verify the API connection is actually functional.

        Detects zombie Gateway: TCP socket alive but API unresponsive, or the
        app stuck on a "reconnect"-style dialog after a long uptime.
        On failure, kill+restart the Gateway app if the process is alive (stuck
        UI case) or otherwise fall through to the normal reconnect loop.
        """
        while True:
            await asyncio.sleep(self._config.heartbeat_interval)
            if not self._ib.isConnected():
                return  # exit loop; connect() will restart via _start_heartbeat()

            # Cheap UI probe even while API looks fine — catches the reconnect
            # dialog as soon as it appears, without waiting for the next hang.
            if self._detect_stuck_ui():
                logger.warning("Stuck UI dialog detected on live connection — restarting Gateway")
                self._last_error = "Stuck UI dialog detected"
                await self._restart_gateway(reason="stuck UI dialog")
                return

            try:
                await asyncio.wait_for(
                    self._ib.reqCurrentTimeAsync(),
                    timeout=10,
                )
            except Exception as e:
                logger.warning("Heartbeat failed (%s)", e)
                self._last_error = f"Heartbeat failed: {e}"
                # If the process is still alive, the app itself is wedged
                # (typically the reconnect dialog) — kill and restart it.
                if self._is_gateway_process_alive():
                    await self._restart_gateway(reason=f"heartbeat hang: {e}")
                    return
                self._ib.disconnect()
                # _on_disconnect handler will start the reconnect loop

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

    # ── Gateway app supervision (macOS) ────────────────────────────────────

    def _is_gateway_process_alive(self) -> bool:
        """True if the Java IB Gateway process is currently running."""
        try:
            result = subprocess.run(
                ["pgrep", "-f", self._config.gateway_process_name],
                capture_output=True, text=True, timeout=3,
            )
            return result.returncode == 0 and bool(result.stdout.strip())
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def _detect_stuck_ui(self) -> bool:
        """Detect the "reconnect"-style dialog that appears after long uptime.

        Heuristic: the Gateway process is alive and has an extra window whose
        name contains reconnect/connection/lost/disconnected, OR any dialog
        that isn't the normal main window and presents buttons. Uses
        AppleScript; silently returns False on any error (non-macOS, no
        accessibility permission, process not there, etc.).
        """
        if not self._is_gateway_process_alive():
            return False
        if not shutil.which("osascript"):
            return False

        process = self._config.gateway_process_name
        main_window = self._config.gateway_window_name
        script = f'''
        tell application "System Events"
            if not (exists process "{process}") then return "no-proc"
            tell process "{process}"
                try
                    set wlist to every window
                on error
                    return "no-win"
                end try
                repeat with w in wlist
                    try
                        set wname to name of w
                    on error
                        set wname to ""
                    end try
                    if wname is not "{main_window}" then
                        set lname to my toLower(wname)
                        if lname contains "reconnect" ¬
                            or lname contains "connection lost" ¬
                            or lname contains "disconnect" ¬
                            or lname contains "re-connect" ¬
                            or lname contains "reconnection" then
                            return "dialog:" & wname
                        end if
                        try
                            if (count of buttons of w) > 0 and wname is not "" then
                                return "dialog:" & wname
                            end if
                        end try
                    end if
                end repeat
            end tell
            return "ok"
        end tell

        on toLower(s)
            set lower to ""
            repeat with c in s
                set ci to id of c
                if ci ≥ 65 and ci ≤ 90 then
                    set lower to lower & (character id (ci + 32))
                else
                    set lower to lower & c
                end if
            end repeat
            return lower
        end toLower
        '''
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=5,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False
        out = (result.stdout or "").strip()
        if out.startswith("dialog:"):
            logger.warning("IB Gateway stuck dialog: %s", out[len("dialog:"):])
            return True
        return False

    async def _restart_gateway(self, reason: str) -> bool:
        """Kill the IB Gateway app and re-run the launch script.

        Serialised via a lock so heartbeat/reconnect loops don't race. Returns
        True if the relaunch script exited 0 and we believe the Gateway is up.
        """
        if self._restart_lock.locked():
            logger.info("Restart already in progress — skipping (reason=%s)", reason)
            return False

        async with self._restart_lock:
            logger.warning("Restarting IB Gateway app — reason: %s", reason)
            self._last_error = f"Restarting Gateway: {reason}"

            # Detach our socket first so ib_async doesn't fight the restart.
            try:
                self._ib.disconnect()
            except Exception:
                pass

            # Kill the Java process — SIGTERM then SIGKILL fallback.
            self._kill_gateway_process(signal.SIGTERM)
            for _ in range(10):
                if not self._is_gateway_process_alive():
                    break
                await asyncio.sleep(0.5)
            else:
                self._kill_gateway_process(signal.SIGKILL)
                await asyncio.sleep(1)

            script_path = self._config.gateway_restart_script
            if not os.path.isfile(script_path):
                logger.error("Gateway restart script not found: %s", script_path)
                self._last_error = f"Restart script missing: {script_path}"
                self.start_reconnect()
                return False

            try:
                proc = await asyncio.create_subprocess_exec(
                    "bash", script_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                try:
                    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=180)
                except asyncio.TimeoutError:
                    proc.kill()
                    logger.error("Gateway restart script timed out after 180s")
                    self._last_error = "Gateway restart script timed out"
                    self.start_reconnect()
                    return False
            except Exception as e:
                logger.error("Failed to run gateway restart script: %s", e)
                self._last_error = f"Restart script error: {e}"
                self.start_reconnect()
                return False

            if proc.returncode != 0:
                tail = (stdout or b"").decode(errors="replace").strip().splitlines()[-5:]
                logger.error(
                    "Gateway restart script exit %d: %s",
                    proc.returncode, " | ".join(tail),
                )
                self._last_error = f"Restart script exit {proc.returncode}"
                self.start_reconnect()
                return False

            logger.info("Gateway restart script completed — reconnecting client")
            # Fresh IB instance: the previous one is now in a terminal state.
            self._ib = IB()
            self._ib.disconnectedEvent += self._on_disconnect
            try:
                await self.connect()
                self._reconnect_failures = 0
                return True
            except Exception as e:
                logger.warning("Post-restart connect failed: %s — falling back to reconnect loop", e)
                self._last_error = f"Post-restart connect failed: {e}"
                self.start_reconnect()
                return False

    def _kill_gateway_process(self, sig: int) -> None:
        """Send `sig` to every matching Gateway process. Best-effort."""
        try:
            result = subprocess.run(
                ["pgrep", "-f", self._config.gateway_process_name],
                capture_output=True, text=True, timeout=3,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return
        for line in result.stdout.strip().splitlines():
            try:
                pid = int(line.strip())
                os.kill(pid, sig)
                logger.info("Sent %s to Gateway PID %d", sig.name if hasattr(sig, "name") else sig, pid)
            except (ProcessLookupError, PermissionError, ValueError):
                continue

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
                "avg_cost": round(v, 4) if (v := clean_nan(pos.avgCost)) is not None else None,
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
