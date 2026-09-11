"""Market signals computed from real OHLCV price history — the 'clean data first'
half of the shared signals library. Every value here is derived arithmetic on
actual prices/volume, never invented. See app/signals/proxy.py for the signal
families the app has no real feed for yet (news, macro, options chain,
fundamentals) — those are clearly tagged is_proxy=True and must never be
presented as equivalent to these."""
from __future__ import annotations

import numpy as np
import pandas as pd


def market_signals(df: pd.DataFrame) -> dict:
    """df: OHLCV indexed by date, ascending. Returns a flat dict of real,
    price-derived signals — empty/omitted keys mean not enough history yet."""
    close, volume = df["close"], df["volume"]
    out: dict = {"source": "real"}

    if len(close) >= 21:
        out["momentum_20d_pct"] = float((close.iloc[-1] / close.iloc[-21] - 1) * 100)
    if len(close) >= 64:
        out["momentum_63d_pct"] = float((close.iloc[-1] / close.iloc[-64] - 1) * 100)
    if len(close) >= 127:
        out["momentum_126d_pct"] = float((close.iloc[-1] / close.iloc[-127] - 1) * 100)
    if len(close) >= 51:
        sma50 = close.rolling(50).mean().iloc[-1]
        out["price_above_sma50"] = bool(close.iloc[-1] > sma50)
    if len(close) >= 201:
        sma200 = close.rolling(200).mean().iloc[-1]
        out["price_above_sma200"] = bool(close.iloc[-1] > sma200)
    if len(close) >= 15:
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean().iloc[-1]
        loss = (-delta.clip(upper=0)).rolling(14).mean().iloc[-1]
        out["rsi_14"] = float(100 - 100 / (1 + gain / loss)) if loss else 100.0
    if len(close) >= 31:
        realized_vol = close.pct_change().rolling(30).std() * np.sqrt(252)
        out["realized_vol_30d_pct"] = float(realized_vol.iloc[-1] * 100)
        # percentile of today's vol vs its own trailing history: a real, if rough,
        # stand-in for IV rank until an actual options chain is wired up.
        hist = realized_vol.dropna()
        if len(hist) >= 30:
            out["realized_vol_percentile"] = float((hist <= hist.iloc[-1]).mean() * 100)
    if len(close) >= 21:
        high_20d = close.rolling(20).max().iloc[-1]
        out["breakout_20d"] = bool(close.iloc[-1] >= high_20d * 0.999)
    if len(volume) >= 21:
        avg_vol_20d = volume.rolling(20).mean().iloc[-1]
        out["volume_ratio_vs_20d_avg"] = float(volume.iloc[-1] / avg_vol_20d) if avg_vol_20d else 1.0
    if len(close) >= 2:
        prev_close = close.iloc[-2]
        out["gap_pct"] = float((df["open"].iloc[-1] / prev_close - 1) * 100)

    return out


def relative_strength_pct(symbol_close: pd.Series, benchmark_close: pd.Series, window: int = 63) -> float | None:
    if len(symbol_close) < window or len(benchmark_close) < window:
        return None
    sym_ret = symbol_close.iloc[-1] / symbol_close.iloc[-window] - 1
    bench_ret = benchmark_close.iloc[-1] / benchmark_close.iloc[-window] - 1
    return float((sym_ret - bench_ret) * 100)


def basket_momentum_pct(closes: list[pd.Series], window: int = 63) -> float | None:
    """Average momentum across a basket of symbols — used as a real proxy for
    'sector/theme momentum' when there's no dedicated sector index feed."""
    vals = []
    for c in closes:
        if len(c) >= window:
            vals.append(float(c.iloc[-1] / c.iloc[-window] - 1) * 100)
    return float(np.mean(vals)) if vals else None
