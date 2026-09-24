"""Synthetic markets for lab tests: deterministic, offline, shaped on purpose."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.lab.data import Panel


def panel_from_close(close: pd.DataFrame, volume: float = 1_000_000.0) -> Panel:
    vol = pd.DataFrame(volume, index=close.index, columns=close.columns)
    return Panel(open=close.copy(), high=close * 1.005, low=close * 0.995, close=close.copy(), volume=vol)


def random_market(symbols: list[str], days: int = 900, seed: int = 7, start: str = "2020-01-01") -> Panel:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=days)
    drift = rng.normal(0.0004, 0.0003, len(symbols))
    rets = rng.normal(drift, 0.015, (days, len(symbols)))
    close = pd.DataFrame(100 * np.exp(np.cumsum(rets, axis=0)), index=idx, columns=symbols)
    return panel_from_close(close)


def path(values: list[float], symbol: str = "AAA", start: str = "2021-01-04",
         extra: dict[str, list[float]] | None = None) -> Panel:
    idx = pd.bdate_range(start, periods=len(values))
    cols = {symbol: values, **(extra or {})}
    return panel_from_close(pd.DataFrame(cols, index=idx, dtype=float))


def trending_market(symbols: list[str], days: int = 600, seed: int = 11, start: str = "2023-01-02") -> Panel:
    """Every symbol in a steady uptrend with mild noise — so trend-following
    profiles have something to hold and the lifecycle can be exercised."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=days)
    slopes = np.linspace(0.0006, 0.0016, len(symbols))
    t = np.arange(days)[:, None]
    close = 100 * np.exp(slopes * t + rng.normal(0, 0.004, (days, len(symbols))).cumsum(axis=0) * 0.2)
    return panel_from_close(pd.DataFrame(close, index=idx, columns=symbols))
