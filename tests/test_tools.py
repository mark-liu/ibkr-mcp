"""Tests for MCP tool functions."""

import json

import pytest

from ibkr_mcp.tools.market import ibkr_quote, ibkr_historical_bars, ibkr_fx_rate
from ibkr_mcp.tools.account import ibkr_positions, ibkr_account_summary
from ibkr_mcp.tools.options import ibkr_option_chain
from ibkr_mcp.tools.search import ibkr_contract_search
from ibkr_mcp.tools.status import ibkr_connection_status
from tests.conftest import MockTicker


class TestToolQuote:
    @pytest.mark.asyncio
    async def test_returns_json(self, ctx):
        result = await ibkr_quote("AAPL", ctx)
        data = json.loads(result)
        assert isinstance(data, list)
        assert data[0]["last"] == 150.00

    @pytest.mark.asyncio
    async def test_multiple_symbols(self, ctx):
        ctx.lifespan_context["client"]._ib.reqTickersAsync.return_value = [
            MockTicker(), MockTicker(last=300.0, close=295.0),
        ]
        result = await ibkr_quote("AAPL MSFT", ctx)
        data = json.loads(result)
        assert len(data) == 2


class TestToolHistoricalBars:
    @pytest.mark.asyncio
    async def test_returns_bars(self, ctx):
        result = await ibkr_historical_bars("AAPL", ctx=ctx)
        data = json.loads(result)
        assert len(data) == 2
        assert "close" in data[0]


class TestToolFxRate:
    @pytest.mark.asyncio
    async def test_returns_rate(self, ctx):
        result = await ibkr_fx_rate("EURUSD", ctx)
        data = json.loads(result)
        assert data["pair"] == "EURUSD"


class TestToolPositions:
    @pytest.mark.asyncio
    async def test_returns_positions(self, ctx):
        result = await ibkr_positions(ctx)
        data = json.loads(result)
        assert isinstance(data, list)
        assert data[0]["symbol"] == "AAPL"


class TestToolAccountSummary:
    @pytest.mark.asyncio
    async def test_returns_summary(self, ctx):
        result = await ibkr_account_summary(ctx)
        data = json.loads(result)
        assert "NetLiquidation_USD" in data


class TestToolOptionChain:
    @pytest.mark.asyncio
    async def test_returns_chain(self, ctx):
        result = await ibkr_option_chain("AAPL", ctx=ctx)
        data = json.loads(result)
        assert data["symbol"] == "AAPL"
        assert len(data["chains"]) > 0


class TestToolContractSearch:
    @pytest.mark.asyncio
    async def test_search(self, ctx):
        result = await ibkr_contract_search("Apple", ctx)
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["symbol"] == "AAPL"


class TestToolConnectionStatus:
    @pytest.mark.asyncio
    async def test_status(self, ctx):
        result = await ibkr_connection_status(ctx)
        data = json.loads(result)
        assert data["connected"] is True
