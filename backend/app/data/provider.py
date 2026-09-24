"""Quotes for the order path (app/paper/service.py): Alpaca, or nothing.

The previous version fell back per symbol to a deterministic *mock* price when
the real feed failed. That is acceptable for a demo screen and unacceptable for
an order: a paper fill priced on an invented quote corrupts the very P&L the
lab exists to measure. So a missing quote now stays missing, and the pre-trade
check refuses the order with "no market price" — loud, and correct.

`mock` remains available only when selected explicitly (tests, offline demos).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass
class Quote:
    symbol: str
    price: float
    source: str  # "alpaca" | "mock"


class MockProvider:
    """Deterministic fake quotes. Never used unless explicitly selected."""

    def latest_prices(self, symbols: list[str]) -> dict[str, Quote]:
        out = {}
        for sym in symbols:
            h = int(hashlib.sha256(sym.encode()).hexdigest()[:8], 16)
            out[sym] = Quote(sym, round(20 + (h % 48000) / 100, 2), "mock")
        return out


def _provider_name() -> str:
    try:
        from app.core import settings_store
        from app.core.db import SessionLocal

        db = SessionLocal()
        try:
            return str(settings_store.resolve(db, "data.market_data_provider"))
        finally:
            db.close()
    except Exception:
        return "alpaca"


def latest_prices(symbols: list[str], provider_name: str | None = None) -> dict[str, Quote]:
    name = provider_name or _provider_name()
    if name == "mock":
        return MockProvider().latest_prices(symbols)
    from app.lab import data

    try:
        prices = data.latest_prices(list(symbols))
    except data.DataUnavailable:
        return {}
    return {s: Quote(s, p, "alpaca") for s, p in prices.items()}
