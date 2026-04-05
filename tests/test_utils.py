"""Tests for utility functions."""

import math

from ibkr_mcp.utils import clean_nan, clean_dict, ticker_to_dict, fmt_currency, fmt_pct


class TestCleanNan:
    def test_nan(self):
        assert clean_nan(float("nan")) is None

    def test_inf(self):
        assert clean_nan(float("inf")) is None
        assert clean_nan(float("-inf")) is None

    def test_normal_float(self):
        assert clean_nan(1.5) == 1.5

    def test_zero(self):
        assert clean_nan(0.0) == 0.0

    def test_negative(self):
        assert clean_nan(-42.5) == -42.5

    def test_non_float(self):
        assert clean_nan("hello") == "hello"
        assert clean_nan(None) is None
        assert clean_nan(42) == 42


class TestCleanDict:
    def test_cleans_nans(self):
        d = {"a": 1.0, "b": float("nan"), "c": "ok"}
        result = clean_dict(d)
        assert result == {"a": 1.0, "b": None, "c": "ok"}

    def test_nested(self):
        d = {"outer": {"inner": float("nan")}}
        result = clean_dict(d)
        assert result == {"outer": {"inner": None}}


class TestTickerToDict:
    def test_normal_ticker(self):
        class T:
            bid = 149.50
            ask = 150.50
            last = 150.00
            close = 148.00
            volume = 45_000_000.0

        result = ticker_to_dict(T())
        assert result["bid"] == 149.50
        assert result["last"] == 150.00
        assert result["change"] == 2.0
        assert abs(result["change_pct"] - 1.35) < 0.01

    def test_nan_fields(self):
        class T:
            bid = float("nan")
            ask = float("nan")
            last = float("nan")
            close = 148.00
            volume = 0

        result = ticker_to_dict(T())
        assert result["bid"] is None
        assert result["last"] is None
        assert result["change"] is None

    def test_zero_close(self):
        class T:
            bid = 1.0
            ask = 2.0
            last = 1.5
            close = 0
            volume = 100

        result = ticker_to_dict(T())
        assert result["change"] is None  # division by zero guarded


class TestFormatting:
    def test_fmt_currency(self):
        assert fmt_currency(1234.567) == "1,234.57 USD"
        assert fmt_currency(None) == "N/A"
        assert fmt_currency(42.1, "AUD") == "42.10 AUD"

    def test_fmt_pct(self):
        assert fmt_pct(2.345) == "+2.35%"
        assert fmt_pct(-1.5) == "-1.50%"
        assert fmt_pct(None) == "N/A"
