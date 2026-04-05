"""Tests for contract and response caches."""

import time

from ibkr_mcp.cache import ContractCache, ResponseCache


class TestContractCache:
    def test_make_key(self):
        assert ContractCache.make_key("AAPL") == "STK:AAPL:SMART:USD"
        assert ContractCache.make_key("EURUSD", "CASH", "IDEALPRO", "USD") == "CASH:EURUSD:IDEALPRO:USD"

    def test_put_and_get(self):
        cache = ContractCache(ttl=3600)
        cache.put("STK:AAPL:SMART:USD", {"conId": 265598})
        assert cache.get("STK:AAPL:SMART:USD") == {"conId": 265598}

    def test_miss(self):
        cache = ContractCache(ttl=3600)
        assert cache.get("STK:MSFT:SMART:USD") is None

    def test_ttl_expiration(self):
        cache = ContractCache(ttl=0)  # instant expiry
        cache.put("STK:AAPL:SMART:USD", {"conId": 265598})
        time.sleep(0.01)
        assert cache.get("STK:AAPL:SMART:USD") is None

    def test_clear(self):
        cache = ContractCache(ttl=3600)
        cache.put("a", 1)
        cache.put("b", 2)
        assert cache.size == 2
        cache.clear()
        assert cache.size == 0

    def test_size(self):
        cache = ContractCache(ttl=3600)
        assert cache.size == 0
        cache.put("a", 1)
        assert cache.size == 1


class TestResponseCache:
    def test_put_and_get_fresh(self):
        cache = ResponseCache(ttl=3600)
        cache.put("positions", [{"symbol": "AAPL"}])
        result = cache.get("positions")
        assert result is not None
        data, is_stale = result
        assert data == [{"symbol": "AAPL"}]
        assert is_stale is False

    def test_stale_data(self):
        cache = ResponseCache(ttl=0)  # instant staleness
        cache.put("positions", [{"symbol": "AAPL"}])
        time.sleep(0.01)
        result = cache.get("positions")
        assert result is not None
        data, is_stale = result
        assert data == [{"symbol": "AAPL"}]
        assert is_stale is True

    def test_miss(self):
        cache = ResponseCache(ttl=3600)
        assert cache.get("nonexistent") is None
