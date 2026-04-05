"""Caching for contracts and tool responses."""

from __future__ import annotations

import time
from typing import Any


class ContractCache:
    """Cache qualified IB contracts to avoid redundant API calls.

    Keys are formatted as "SEC_TYPE:SYMBOL:EXCHANGE:CURRENCY".
    """

    def __init__(self, ttl: int = 3600) -> None:
        self._ttl = ttl
        self._entries: dict[str, tuple[Any, float]] = {}

    @staticmethod
    def make_key(symbol: str, sec_type: str = "STK", exchange: str = "SMART", currency: str = "USD") -> str:
        return f"{sec_type}:{symbol}:{exchange}:{currency}"

    def get(self, key: str) -> Any | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        contract, ts = entry
        if time.monotonic() - ts > self._ttl:
            del self._entries[key]
            return None
        return contract

    def put(self, key: str, contract: Any) -> None:
        self._entries[key] = (contract, time.monotonic())

    def clear(self) -> None:
        self._entries.clear()

    @property
    def size(self) -> int:
        return len(self._entries)


class ResponseCache:
    """Short-TTL cache for graceful degradation when gateway is offline.

    Stores last-known-good responses so tools can return stale data
    instead of errors when the gateway disconnects temporarily.
    """

    def __init__(self, ttl: int = 120) -> None:
        self._ttl = ttl
        self._entries: dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> tuple[Any, bool] | None:
        """Return (data, is_stale) or None if not cached."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        data, ts = entry
        is_stale = time.monotonic() - ts > self._ttl
        return data, is_stale

    def put(self, key: str, data: Any) -> None:
        self._entries[key] = (data, time.monotonic())
