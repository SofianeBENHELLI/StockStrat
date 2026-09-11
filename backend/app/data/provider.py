"""Market data behind a small protocol so a paid feed can slot in later without
touching consumers. yfinance is the default; if it fails or is unavailable
(offline, rate-limited), a clearly-labeled deterministic mock feed takes over so
the rest of the app keeps working end-to-end. Every quote's `source` field says
which one produced it — the UI must never present mock data as real."""
from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

import numpy as np
import pandas as pd


@dataclass
class Quote:
    symbol: str
    price: float
    source: str  # "yfinance" | "mock"


@dataclass
class PriceHistory:
    symbol: str
    df: pd.DataFrame  # columns: open, high, low, close, volume — indexed by date
    source: str  # "yfinance" | "mock"


class MarketDataProvider(Protocol):
    def latest_prices(self, symbols: list[str]) -> dict[str, Quote]: ...


class YFinanceProvider:
    def latest_prices(self, symbols: list[str]) -> dict[str, Quote]:
        import yfinance as yf

        out: dict[str, Quote] = {}
        try:
            raw = yf.download(symbols, period="5d", group_by="ticker",
                              auto_adjust=True, progress=False, threads=True)
        except Exception:
            return out
        if raw is None or raw.empty:
            return out
        for sym in symbols:
            try:
                df = raw[sym] if hasattr(raw.columns, "levels") else raw
                close = df["Close"].dropna()
                if not close.empty:
                    out[sym] = Quote(symbol=sym, price=float(close.iloc[-1]), source="yfinance")
            except (KeyError, IndexError):
                continue
        return out

    def history(self, symbols: list[str], days: int) -> dict[str, PriceHistory]:
        import yfinance as yf

        out: dict[str, PriceHistory] = {}
        try:
            raw = yf.download(symbols, period=f"{days + 30}d", group_by="ticker",
                              auto_adjust=True, progress=False, threads=True)
        except Exception:
            return out
        if raw is None or raw.empty:
            return out
        for sym in symbols:
            try:
                df = raw[sym] if hasattr(raw.columns, "levels") else raw
                df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna()
                if not df.empty:
                    out[sym] = PriceHistory(symbol=sym, df=df.tail(days), source="yfinance")
            except (KeyError, IndexError):
                continue
        return out


class MockProvider:
    """Deterministic pseudo-price so the app is usable offline/without a data key.
    Never silently mixed with real quotes — always tagged source="mock"."""

    def latest_prices(self, symbols: list[str]) -> dict[str, Quote]:
        out: dict[str, Quote] = {}
        for sym in symbols:
            seed = int(hashlib.sha256(sym.encode()).hexdigest(), 16) % 10_000
            price = round(10 + (seed / 10_000) * 490, 2)  # deterministic $10-$500
            out[sym] = Quote(symbol=sym, price=price, source="mock")
        return out

    def history(self, symbols: list[str], days: int) -> dict[str, PriceHistory]:
        out: dict[str, PriceHistory] = {}
        for sym in symbols:
            seed = int(hashlib.sha256(sym.encode()).hexdigest(), 16) % (2**32)
            rng = np.random.default_rng(seed)
            start_price = 10 + (seed % 10_000) / 10_000 * 490
            rets = rng.normal(0.0003, 0.02, size=days)
            closes = start_price * np.cumprod(1 + rets)
            dates = pd.bdate_range(end=datetime.now(timezone.utc).date(), periods=days)
            volume = rng.integers(500_000, 5_000_000, size=days)
            df = pd.DataFrame({
                "open": closes * (1 - rng.normal(0, 0.003, size=days)),
                "high": closes * (1 + np.abs(rng.normal(0, 0.006, size=days))),
                "low": closes * (1 - np.abs(rng.normal(0, 0.006, size=days))),
                "close": closes,
                "volume": volume,
            }, index=dates)
            out[sym] = PriceHistory(symbol=sym, df=df, source="mock")
        return out


_lock = threading.Lock()
_cache: dict[str, tuple[Quote, datetime]] = {}
_cache_ttl_seconds = 60


def get_provider(name: str) -> MarketDataProvider:
    return YFinanceProvider() if name == "yfinance" else MockProvider()


def latest_prices(symbols: list[str], provider_name: str = "yfinance") -> dict[str, Quote]:
    """Real quotes first, mock fallback per-symbol for whatever yfinance couldn't serve."""
    now = datetime.now(timezone.utc)
    with _lock:
        fresh = {
            s: q for s, (q, ts) in _cache.items()
            if s in symbols and (now - ts).total_seconds() < _cache_ttl_seconds
        }
    missing = [s for s in symbols if s not in fresh]
    if missing and provider_name != "mock":
        fetched = YFinanceProvider().latest_prices(missing)
        with _lock:
            for sym, q in fetched.items():
                _cache[sym] = (q, now)
        fresh.update(fetched)
        missing = [s for s in missing if s not in fetched]
    if missing:
        fetched = MockProvider().latest_prices(missing)
        with _lock:
            for sym, q in fetched.items():
                _cache[sym] = (q, now)
        fresh.update(fetched)
    return fresh


_history_lock = threading.Lock()
_history_cache: dict[tuple[str, int], tuple[PriceHistory, datetime]] = {}
_history_cache_ttl_seconds = 900  # signals don't need second-level freshness


def price_history(symbols: list[str], days: int = 260) -> dict[str, PriceHistory]:
    """Real OHLCV first, mock random-walk fallback per-symbol for whatever
    yfinance couldn't serve (offline, rate-limited, delisted symbol...). Cached
    per (symbol, days) — not just symbol — so a caller asking for a longer
    window (phase 4's multi-year training fetch) never gets served a shorter
    window an earlier caller (e.g. the UI's 260-day idea generation) cached."""
    now = datetime.now(timezone.utc)
    with _history_lock:
        fresh = {
            s: h for (s, d), (h, ts) in _history_cache.items()
            if s in symbols and d == days and (now - ts).total_seconds() < _history_cache_ttl_seconds
        }
    missing = [s for s in symbols if s not in fresh]
    if missing:
        fetched = YFinanceProvider().history(missing, days)
        with _history_lock:
            for sym, h in fetched.items():
                _history_cache[(sym, days)] = (h, now)
        fresh.update(fetched)
        missing = [s for s in missing if s not in fetched]
    if missing:
        fetched = MockProvider().history(missing, days)
        with _history_lock:
            for sym, h in fetched.items():
                _history_cache[(sym, days)] = (h, now)
        fresh.update(fetched)
    return fresh
