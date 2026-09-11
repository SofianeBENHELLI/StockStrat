"""Pure portfolio-math functions — no DB, no SQLAlchemy Session. Extracted out
of app/paper/accounting.py so the live dashboard and the historical backtest
engine (app/backtest/engine.py) compute Sharpe/Sortino/drawdown/hit-rate from
exactly the same formulas instead of two copies that could quietly drift
apart. Every function here takes plain lists/floats and returns plain
values — the DB-facing code in accounting.py is the only place that touches
ORM objects."""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np

TRADING_DAYS_PER_YEAR = 252


def max_drawdown_pct(equities: Sequence[float]) -> float:
    peak, max_dd = float("-inf"), 0.0
    for e in equities:
        peak = max(peak, e)
        if peak > 0:
            max_dd = max(max_dd, (peak - e) / peak * 100)
    return max_dd


def daily_returns(dated_equities: Sequence[tuple[str, float]]) -> list[float]:
    """`dated_equities` must already be chronologically ordered — (ISO date
    string, equity) pairs. Multiple entries on the same day collapse to the
    last one (a day's closing equity), matching how PortfolioSnapshot rows
    are read in accounting.py."""
    by_day: dict[str, float] = {}
    for day, equity in dated_equities:
        by_day[day] = equity
    vals = list(by_day.values())
    return [(vals[i] / vals[i - 1] - 1) for i in range(1, len(vals)) if vals[i - 1] > 0]


def volatility_pct(daily_rets: Sequence[float], periods_per_year: float = TRADING_DAYS_PER_YEAR) -> float:
    """`periods_per_year` must match how far apart each return in `daily_rets`
    actually is — 252 for true daily snapshots (the live dashboard), or
    252/rebalance_every_days for the backtest's coarser snapshots. Annualizing
    a 10-trading-day return with the daily sqrt(252) factor overstates Sharpe/
    vol by ~sqrt(10)x — caught via a backtest smoke test that showed Sharpe
    9.4 / Sortino 151 on ordinary large-cap momentum, nowhere close to real."""
    if len(daily_rets) < 2:
        return 0.0
    return float(np.std(daily_rets) * np.sqrt(periods_per_year) * 100)


def sharpe_ratio(daily_rets: Sequence[float], periods_per_year: float = TRADING_DAYS_PER_YEAR) -> float:
    if len(daily_rets) < 2 or np.std(daily_rets) == 0:
        return 0.0
    return float(np.mean(daily_rets) / np.std(daily_rets) * np.sqrt(periods_per_year))


def sortino_ratio(daily_rets: Sequence[float], periods_per_year: float = TRADING_DAYS_PER_YEAR) -> float:
    downside = [r for r in daily_rets if r < 0]
    if len(downside) < 2 or np.std(downside) == 0:
        return 0.0
    return float(np.mean(daily_rets) / np.std(downside) * np.sqrt(periods_per_year))


def hit_rate_pct(closed_trades_pnl: Sequence[float]) -> float | None:
    if not closed_trades_pnl:
        return None
    wins = [t for t in closed_trades_pnl if t > 0]
    return len(wins) / len(closed_trades_pnl) * 100


def avg_win_loss(closed_trades_pnl: Sequence[float]) -> tuple[float | None, float | None]:
    wins = [t for t in closed_trades_pnl if t > 0]
    losses = [t for t in closed_trades_pnl if t < 0]
    avg_win = (sum(wins) / len(wins)) if wins else None
    avg_loss = (sum(losses) / len(losses)) if losses else None
    return avg_win, avg_loss


def profit_factor(closed_trades_pnl: Sequence[float]) -> float | None:
    gross_profit = sum(t for t in closed_trades_pnl if t > 0)
    gross_loss = abs(sum(t for t in closed_trades_pnl if t < 0))
    # None (not Infinity) keeps this JSON-safe; not meaningful with zero losing trades anyway
    return (gross_profit / gross_loss) if gross_loss > 0 else None
