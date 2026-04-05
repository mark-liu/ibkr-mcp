"""Pydantic models for tool input validation."""

from __future__ import annotations

import re

from pydantic import BaseModel, field_validator

# IB-allowed bar sizes
VALID_BAR_SIZES = {
    "1 secs", "5 secs", "10 secs", "15 secs", "30 secs",
    "1 min", "2 mins", "3 mins", "5 mins", "10 mins", "15 mins", "20 mins", "30 mins",
    "1 hour", "2 hours", "3 hours", "4 hours", "8 hours",
    "1 day", "1 week", "1 month",
}

VALID_WHAT_TO_SHOW = {
    "TRADES", "MIDPOINT", "BID", "ASK", "BID_ASK",
    "HISTORICAL_VOLATILITY", "OPTION_IMPLIED_VOLATILITY",
}

_DURATION_RE = re.compile(r"^\d+ [SDWMY]$")


class QuoteInput(BaseModel):
    symbols: str  # comma or space separated

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, v: str) -> str:
        # Normalise separators and uppercase
        cleaned = re.sub(r"[,;\s]+", " ", v.strip()).upper()
        parts = cleaned.split()
        if not parts:
            raise ValueError("At least one symbol required")
        if len(parts) > 20:
            raise ValueError("Maximum 20 symbols per request")
        return " ".join(parts)

    @property
    def symbol_list(self) -> list[str]:
        return self.symbols.split()


class HistoricalBarsInput(BaseModel):
    symbol: str
    duration: str = "1 M"
    bar_size: str = "1 day"
    what_to_show: str = "TRADES"
    use_rth: bool = True

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("duration")
    @classmethod
    def validate_duration(cls, v: str) -> str:
        v = v.strip().upper()
        if not _DURATION_RE.match(v):
            raise ValueError(f"Duration must match format '{{N}} {{S|D|W|M|Y}}', got '{v}'")
        return v

    @field_validator("bar_size")
    @classmethod
    def validate_bar_size(cls, v: str) -> str:
        v = v.strip().lower()
        # Normalise to IB format
        if v not in VALID_BAR_SIZES:
            raise ValueError(f"Invalid bar size '{v}'. Valid: {sorted(VALID_BAR_SIZES)}")
        return v

    @field_validator("what_to_show")
    @classmethod
    def validate_what_to_show(cls, v: str) -> str:
        v = v.strip().upper()
        if v not in VALID_WHAT_TO_SHOW:
            raise ValueError(f"Invalid what_to_show '{v}'. Valid: {sorted(VALID_WHAT_TO_SHOW)}")
        return v


class OptionChainInput(BaseModel):
    symbol: str
    exchange: str = ""

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        return v.strip().upper()


class ContractSearchInput(BaseModel):
    pattern: str

    @field_validator("pattern")
    @classmethod
    def validate_pattern(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Search pattern cannot be empty")
        return v


class FxRateInput(BaseModel):
    pair: str  # e.g. "EURUSD" or "EUR/USD" or "EUR.USD"

    @field_validator("pair")
    @classmethod
    def validate_pair(cls, v: str) -> str:
        cleaned = re.sub(r"[/.\s-]", "", v.strip().upper())
        if len(cleaned) != 6 or not cleaned.isalpha():
            raise ValueError(f"FX pair must be 6 letters (e.g. EURUSD), got '{v}'")
        return cleaned
