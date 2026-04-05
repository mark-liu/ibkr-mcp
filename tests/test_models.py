"""Tests for Pydantic input models."""

import pytest
from pydantic import ValidationError

from ibkr_mcp.models import (
    ContractSearchInput,
    FxRateInput,
    HistoricalBarsInput,
    OptionChainInput,
    QuoteInput,
)


class TestQuoteInput:
    def test_single_symbol(self):
        q = QuoteInput(symbols="aapl")
        assert q.symbol_list == ["AAPL"]

    def test_multiple_comma(self):
        q = QuoteInput(symbols="aapl, msft, spy")
        assert q.symbol_list == ["AAPL", "MSFT", "SPY"]

    def test_multiple_space(self):
        q = QuoteInput(symbols="AAPL MSFT SPY")
        assert q.symbol_list == ["AAPL", "MSFT", "SPY"]

    def test_empty_raises(self):
        with pytest.raises(ValidationError):
            QuoteInput(symbols="   ")

    def test_too_many_raises(self):
        with pytest.raises(ValidationError):
            QuoteInput(symbols=" ".join([f"SYM{i}" for i in range(21)]))


class TestHistoricalBarsInput:
    def test_defaults(self):
        h = HistoricalBarsInput(symbol="aapl")
        assert h.symbol == "AAPL"
        assert h.duration == "1 M"
        assert h.bar_size == "1 day"

    def test_valid_duration(self):
        h = HistoricalBarsInput(symbol="SPY", duration="5 D")
        assert h.duration == "5 D"

    def test_invalid_duration(self):
        with pytest.raises(ValidationError):
            HistoricalBarsInput(symbol="SPY", duration="invalid")

    def test_invalid_bar_size(self):
        with pytest.raises(ValidationError):
            HistoricalBarsInput(symbol="SPY", bar_size="2 day")

    def test_invalid_what_to_show(self):
        with pytest.raises(ValidationError):
            HistoricalBarsInput(symbol="SPY", what_to_show="INVALID")


class TestOptionChainInput:
    def test_normalises_symbol(self):
        o = OptionChainInput(symbol="  fcx  ")
        assert o.symbol == "FCX"


class TestContractSearchInput:
    def test_valid(self):
        c = ContractSearchInput(pattern="Apple")
        assert c.pattern == "Apple"

    def test_empty_raises(self):
        with pytest.raises(ValidationError):
            ContractSearchInput(pattern="   ")


class TestFxRateInput:
    def test_standard(self):
        f = FxRateInput(pair="EURUSD")
        assert f.pair == "EURUSD"

    def test_with_slash(self):
        f = FxRateInput(pair="EUR/USD")
        assert f.pair == "EURUSD"

    def test_with_dot(self):
        f = FxRateInput(pair="AUD.USD")
        assert f.pair == "AUDUSD"

    def test_lowercase(self):
        f = FxRateInput(pair="audusd")
        assert f.pair == "AUDUSD"

    def test_invalid_length(self):
        with pytest.raises(ValidationError):
            FxRateInput(pair="EUR")

    def test_invalid_chars(self):
        with pytest.raises(ValidationError):
            FxRateInput(pair="EUR123")
