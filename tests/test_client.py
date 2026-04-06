"""Tests for IBKRClient."""

import pytest

from tests.conftest import (
    FakeContext,
    MockAccountValue,
    MockBar,
    MockContract,
    MockContractDescription,
    MockOptionChain,
    MockPortfolioItem,
    MockPosition,
    MockTicker,
    make_mock_ib,
)


class TestConnectionStatus:
    @pytest.mark.asyncio
    async def test_connected(self, client):
        status = await client.get_connection_status()
        assert status["connected"] is True
        assert status["accounts"] == ["U1234567"]
        assert status["client_id"] == 10

    @pytest.mark.asyncio
    async def test_disconnected(self, config, contract_cache, response_cache):
        from ibkr_mcp.client import IBKRClient
        c = IBKRClient(config, contract_cache, response_cache)
        c._ib = make_mock_ib(connected=False)
        status = await c.get_connection_status()
        assert status["connected"] is False
        assert status["accounts"] == []


class TestGetQuote:
    @pytest.mark.asyncio
    async def test_single_symbol(self, client):
        result = await client.get_quote(["AAPL"])
        assert len(result) == 1
        assert result[0]["last"] == 150.00
        assert result[0]["change"] == 2.0

    @pytest.mark.asyncio
    async def test_nan_handling(self, client):
        nan_ticker = MockTicker(bid=float("nan"), ask=float("nan"), last=float("nan"))
        client._ib.reqTickersAsync.return_value = [nan_ticker]
        result = await client.get_quote(["AAPL"])
        assert result[0]["bid"] is None
        assert result[0]["last"] is None


class TestGetHistoricalBars:
    @pytest.mark.asyncio
    async def test_returns_bars(self, client):
        result = await client.get_historical_bars("AAPL")
        assert len(result) == 2
        assert result[0]["close"] == 150.00
        assert result[1]["date"] == "2026-04-02"


class TestGetPositions:
    @pytest.mark.asyncio
    async def test_returns_positions(self, client):
        result = await client.get_positions()
        assert len(result) == 1
        assert result[0]["symbol"] == "AAPL"
        assert result[0]["quantity"] == 100.0
        assert result[0]["unrealized_pnl"] == 2000.00

    @pytest.mark.asyncio
    async def test_empty_portfolio(self, client):
        client._ib.positions.return_value = []
        client._ib.portfolio.return_value = []
        result = await client.get_positions()
        assert result == []


class TestGetAccountSummary:
    @pytest.mark.asyncio
    async def test_extracts_tags(self, client):
        result = await client.get_account_summary()
        assert "NetLiquidation_USD" in result
        assert result["NetLiquidation_USD"] == 50000.00
        assert "accounts" in result


class TestGetOptionChain:
    @pytest.mark.asyncio
    async def test_returns_chain(self, client):
        result = await client.get_option_chain("AAPL")
        assert result["symbol"] == "AAPL"
        assert len(result["chains"]) == 1
        chain = result["chains"][0]
        assert "20260618" in chain["expirations"]
        assert 150.0 in chain["strikes"]


class TestSearchContracts:
    @pytest.mark.asyncio
    async def test_search(self, client):
        result = await client.search_contracts("Apple")
        assert len(result) == 1
        assert result[0]["symbol"] == "AAPL"


class TestReconnect:
    @pytest.mark.asyncio
    async def test_reconnect_creates_fresh_ib_instance(self, config, contract_cache, response_cache):
        """After disconnect, reconnect loop should create a new IB() instance
        to avoid stale internal state from the dead connection."""
        from unittest.mock import patch, MagicMock, AsyncMock
        from ibkr_mcp.client import IBKRClient

        c = IBKRClient(config, contract_cache, response_cache)
        old_ib = c._ib

        # Simulate a disconnect then one reconnect iteration
        c._reconnecting = True

        # Patch IB constructor to track fresh instance creation
        fresh_ib = make_mock_ib(connected=True)
        with patch("ibkr_mcp.client.IB", return_value=fresh_ib):
            # Patch sleep to not actually wait
            with patch("asyncio.sleep", new_callable=AsyncMock):
                # Run one iteration: the loop checks is_connected, sleeps,
                # disconnects old, creates new IB, connects, then checks again
                # We need is_connected to return False first, then True after connect
                call_count = 0

                def connected_side_effect():
                    nonlocal call_count
                    call_count += 1
                    # First two calls (loop condition + _ensure_market_data_type): False
                    # After connect succeeds: True
                    return call_count > 2

                fresh_ib.isConnected.side_effect = connected_side_effect

                await c._reconnect_loop()

        # Verify: old IB was replaced with fresh instance
        assert c._ib is not old_ib
        assert c._ib is fresh_ib
        # Verify: disconnect event handler re-attached
        fresh_ib.disconnectedEvent.__iadd__.assert_called()


class TestContractCaching:
    @pytest.mark.asyncio
    async def test_cache_hit(self, client):
        # First call qualifies
        await client.get_quote(["AAPL"])
        call_count_1 = client._ib.qualifyContractsAsync.call_count

        # Second call should use cache
        await client.get_quote(["AAPL"])
        call_count_2 = client._ib.qualifyContractsAsync.call_count

        assert call_count_2 == call_count_1  # no additional qualify call
