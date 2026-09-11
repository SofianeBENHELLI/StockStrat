"""Historical training examples for phase 4's ranking model: for every symbol in
the universe, slide a point-in-time window over its price history and pair the
factors `app.ml.features.compute_factors` would have produced on that day with
the REAL forward return that actually happened. There's no feed for "what the
proxy families' value would have predicted historically" beyond their own
deterministic formula, so this evaluates them exactly as an honest backtest
would: on whether that formula's output happens to correlate with real forward
returns, nothing more."""
from __future__ import annotations

import pandas as pd

from app.data.provider import price_history
from app.ml.features import FACTOR_NAMES, compute_factors

TRADING_DAYS_PER_YEAR = 252
MIN_LOOKBACK_ROWS = 201  # matches compute_factors' sma200 floor


def build_training_examples(
    universe: list[str], years: int = 6, sample_every: int = 20, horizon_days: int = 20,
) -> pd.DataFrame:
    """`sample_every` defaults to `horizon_days` so consecutive forward-return
    windows for the same symbol don't overlap — sampling every 5 days with a
    20-day horizon looked like it 4x'd the sample count, but 75%-overlapping
    windows share almost all their return, so the true independent sample size
    was a fraction of what `n_samples` reported (and inflated confidence in
    whatever sign the regression happened to land on)."""
    total_days = years * TRADING_DAYS_PER_YEAR + horizon_days + 30
    histories = price_history(universe, days=total_days)
    rows: list[dict] = []

    for symbol in universe:
        hist = histories.get(symbol)
        if hist is None:
            continue
        df = hist.df
        last_usable = len(df) - horizon_days
        for i in range(MIN_LOOKBACK_ROWS, last_usable, sample_every):
            as_of = df.index[i].date()
            factors = compute_factors(symbol, df.iloc[: i + 1], as_of)
            if factors is None:
                continue
            label = float(df["close"].iloc[i + horizon_days] / df["close"].iloc[i] - 1) * 100
            row = {f.name: f.value for f in factors}
            row.update({"symbol": symbol, "as_of": as_of, "label": label})
            rows.append(row)

    return pd.DataFrame(rows, columns=["symbol", "as_of", *FACTOR_NAMES, "label"])
