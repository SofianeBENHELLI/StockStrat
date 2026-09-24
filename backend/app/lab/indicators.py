"""Vectorised indicators over a dates x symbols frame. Pure, no look-ahead:
every value at row t uses rows <= t only."""
from __future__ import annotations

import pandas as pd


def sma(close: pd.DataFrame, n: int) -> pd.DataFrame:
    return close.rolling(n, min_periods=n).mean()


def rsi(close: pd.DataFrame, n: int) -> pd.DataFrame:
    """Wilder's RSI. A flat stretch with no losses reads 100, not NaN."""
    delta = close.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    avg_up = up.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    avg_down = down.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = avg_up / avg_down.where(avg_down > 0)
    out = 100 - 100 / (1 + rs)
    return out.where(avg_down > 0, 100.0).where(avg_up.notna())


def ret(close: pd.DataFrame, n: int) -> pd.DataFrame:
    return close / close.shift(n) - 1


def rolling_high(high: pd.DataFrame, n: int) -> pd.DataFrame:
    """Highest high of the n sessions BEFORE today — so a breakout is today
    closing above a level that was known this morning."""
    return high.shift(1).rolling(n, min_periods=n).max()


def volume_ratio(volume: pd.DataFrame, n: int) -> pd.DataFrame:
    """Today's volume against the average of the n sessions before it."""
    return volume / volume.shift(1).rolling(n, min_periods=n).mean()


def month_starts(dates: pd.DatetimeIndex) -> list[int]:
    """Row positions of the first session of each month."""
    idx = [0]
    for i in range(1, len(dates)):
        if dates[i].month != dates[i - 1].month:
            idx.append(i)
    return idx
