"""Real macro regime signal for the ML engine's `macro` factor — same market
families PortfolioVirtualTwin's macro module already tracks (VIX, 10Y yield,
gold, oil, dollar), fetched via yfinance so no API key is needed. Replaces the
calendar-hash proxy in app/signals/proxy.py::macro_backdrop with something
genuinely time-varying and historically fetchable, which is what phase 4's
walk-forward training actually needs; falls back to that same proxy hash if
the real fetch fails entirely — never silently blend real and mock, same rule
the price feed follows in app/data/provider.py."""
from __future__ import annotations

import threading
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

MACRO_TICKERS = {"vix": "^VIX", "rate10y": "^TNX", "gold": "GC=F", "oil": "CL=F", "dollar": "DX-Y.NYB"}
FETCH_DAYS = 1700  # comfortably covers app/ml/dataset.py's 6-year training window


def _fetch_raw(days: int = FETCH_DAYS) -> dict[str, pd.Series] | None:
    import yfinance as yf

    try:
        raw = yf.download(list(MACRO_TICKERS.values()), period=f"{days}d", group_by="ticker",
                           auto_adjust=True, progress=False, threads=True)
    except Exception:
        return None
    if raw is None or raw.empty:
        return None
    out: dict[str, pd.Series] = {}
    for name, ticker in MACRO_TICKERS.items():
        try:
            df = raw[ticker] if hasattr(raw.columns, "levels") else raw
            close = df["Close"].dropna()
            if not close.empty:
                out[name] = close
        except (KeyError, IndexError):
            continue
    # all-or-nothing: a partial real composite (missing one family) would silently
    # under-weight whatever failed to fetch — fall back to the full proxy instead.
    return out if len(out) == len(MACRO_TICKERS) else None


def _norm(series: pd.Series, lo: float, hi: float) -> pd.Series:
    return ((series - lo) / (hi - lo)).clip(0, 1)


def _regime_score_series(raw: dict[str, pd.Series]) -> pd.Series:
    """0..1 per date, higher = more 'supportive' — same meaning as the proxy's
    regime_score. Point-in-time: every rolling/pct_change stat at date t only
    ever looks at data up to and including t, never beyond."""
    idx = raw["vix"].index
    for series in raw.values():
        idx = idx.intersection(series.index)
    idx = idx.sort_values()

    vix = raw["vix"].reindex(idx)
    rate = raw["rate10y"].reindex(idx)
    gold = raw["gold"].reindex(idx)
    oil = raw["oil"].reindex(idx)
    dollar = raw["dollar"].reindex(idx)

    vix_pctile = vix.rolling(252, min_periods=60).apply(lambda w: float((w <= w.iloc[-1]).mean()), raw=False)
    rate_chg = rate.pct_change(63)
    gold_chg = gold.pct_change(63)
    oil_chg = oil.pct_change(63)
    dollar_chg = dollar.pct_change(63)

    supportive = (
        (1 - vix_pctile).fillna(0.5) * 0.35
        + (1 - _norm(rate_chg, -0.15, 0.15)).fillna(0.5) * 0.25
        + (1 - _norm(gold_chg, -0.15, 0.15)).fillna(0.5) * 0.15
        + (1 - _norm(oil_chg, -0.30, 0.30)).fillna(0.5) * 0.10
        + (1 - _norm(dollar_chg, -0.10, 0.10)).fillna(0.5) * 0.15
    )
    return supportive.clip(0, 1).round(3)


_lock = threading.Lock()
_cache: dict | None = None  # {"series": pd.Series, "fetched_at": datetime}
_cache_ttl_seconds = 3600  # one shared series, not per-symbol — cheap to keep fresh-ish


def _regime_series() -> pd.Series | None:
    global _cache
    now = datetime.now(timezone.utc)
    with _lock:
        if _cache is not None and (now - _cache["fetched_at"]).total_seconds() < _cache_ttl_seconds:
            return _cache["series"]
    raw = _fetch_raw()
    if raw is None:
        return None
    series = _regime_score_series(raw)
    with _lock:
        _cache = {"series": series, "fetched_at": now}
    return series


def macro_backdrop(as_of: date) -> dict:
    series = _regime_series()
    if series is not None:
        eligible = series[series.index.date <= as_of]
        if not eligible.empty:
            value = float(eligible.iloc[-1])
            if not np.isnan(value):
                return {"source": "real", "regime_score": value}
    from app.signals.proxy import macro_backdrop as proxy_macro_backdrop
    return proxy_macro_backdrop(as_of)
