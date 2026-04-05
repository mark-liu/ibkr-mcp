"""Tests for utility functions."""

from ibkr_mcp.utils import clean_nan, ticker_to_dict


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
