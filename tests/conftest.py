"""Shared test fixtures: mock IB client, fake contexts."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ibkr_mcp.cache import ContractCache, ResponseCache
from ibkr_mcp.client import IBKRClient
from ibkr_mcp.config import IBKRConfig


# ── Mock IB objects ────────────────────────────────────────────────────────

@dataclass
class MockContract:
    symbol: str = "AAPL"
    secType: str = "STK"
    exchange: str = "SMART"
    currency: str = "USD"
    conId: int = 265598
    localSymbol: str = ""
    primaryExchange: str = "NASDAQ"


@dataclass
class MockTicker:
    bid: float = 149.50
    ask: float = 150.50
    last: float = 150.00
    close: float = 148.00
    volume: float = 45_000_000.0


@dataclass
class MockPosition:
    contract: MockContract = field(default_factory=MockContract)
    position: float = 100.0
    avgCost: float = 130.00


@dataclass
class MockPortfolioItem:
    contract: MockContract = field(default_factory=MockContract)
    position: float = 100.0
    marketPrice: float = 150.00
    marketValue: float = 15000.00
    averageCost: float = 130.00
    unrealizedPNL: float = 2000.00
    realizedPNL: float = 0.0


@dataclass
class MockBar:
    date: str = "2026-04-01"
    open: float = 148.00
    high: float = 151.50
    low: float = 147.50
    close: float = 150.00
    volume: float = 42_000_000.0
    average: float = 149.50
    barCount: int = 85000


@dataclass
class MockAccountValue:
    tag: str = "NetLiquidation"
    value: str = "50000.00"
    currency: str = "USD"
    modelCode: str = ""


@dataclass
class MockOptionChain:
    exchange: str = "SMART"
    underlyingConId: int = 265598
    tradingClass: str = "AAPL"
    multiplier: str = "100"
    expirations: frozenset = field(default_factory=lambda: frozenset(["20260618", "20260717"]))
    strikes: frozenset = field(default_factory=lambda: frozenset([145.0, 150.0, 155.0, 160.0]))


@dataclass
class MockContractDescription:
    contract: MockContract = field(default_factory=MockContract)
    derivativeSecTypes: list = field(default_factory=lambda: ["OPT", "WAR"])
    contractDescription: str = "Apple Inc"


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def config():
    return IBKRConfig()


@pytest.fixture
def contract_cache():
    return ContractCache(ttl=3600)


@pytest.fixture
def response_cache():
    return ResponseCache(ttl=120)


def make_mock_ib(
    connected: bool = True,
    positions: list | None = None,
    portfolio: list | None = None,
    account_summary: list | None = None,
) -> MagicMock:
    """Create a mock IB instance with configurable responses."""
    ib = MagicMock()
    ib.isConnected.return_value = connected
    ib.managedAccounts.return_value = ["U1234567"]
    ib.client.serverVersion.return_value = 163

    # Positions
    ib.positions.return_value = positions or [MockPosition()]
    ib.portfolio.return_value = portfolio or [MockPortfolioItem()]

    # Async methods
    ib.connectAsync = AsyncMock()
    ib.qualifyContractsAsync = AsyncMock(
        side_effect=lambda *contracts: list(contracts)
    )
    ib.reqTickersAsync = AsyncMock(return_value=[MockTicker()])
    ib.reqHistoricalDataAsync = AsyncMock(
        return_value=[MockBar(), MockBar(date="2026-04-02", close=151.00)]
    )
    ib.accountSummaryAsync = AsyncMock(return_value=account_summary or [
        MockAccountValue("NetLiquidation", "50000.00", "USD"),
        MockAccountValue("TotalCashValue", "20000.00", "USD"),
        MockAccountValue("BuyingPower", "80000.00", "USD"),
        MockAccountValue("GrossPositionValue", "30000.00", "USD"),
        MockAccountValue("UnrealizedPnL", "2500.00", "USD"),
    ])
    ib.reqSecDefOptParamsAsync = AsyncMock(return_value=[MockOptionChain()])
    ib.reqMatchingSymbolsAsync = AsyncMock(
        return_value=[MockContractDescription()]
    )
    ib.reqMarketDataType = MagicMock()

    # Events
    ib.disconnectedEvent = MagicMock()
    ib.disconnectedEvent.__iadd__ = MagicMock(return_value=ib.disconnectedEvent)

    return ib


@pytest.fixture
def mock_ib():
    return make_mock_ib()


@pytest.fixture
def client(config, contract_cache, response_cache, mock_ib):
    """IBKRClient with mocked IB instance."""
    c = IBKRClient(config, contract_cache, response_cache)
    c._ib = mock_ib
    c._connected = True
    return c


# ── Fake MCP context ──────────────────────────────────────────────────────

class FakeRequestContext:
    def __init__(self, lifespan_context: dict):
        self.lifespan_context = lifespan_context


class FakeContext:
    def __init__(self, client: IBKRClient):
        self.request_context = FakeRequestContext({"client": client})


@pytest.fixture
def ctx(client):
    return FakeContext(client)
