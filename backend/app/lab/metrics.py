"""Results in the units the owner judges by: dollars won, dollars of worst dip.

Percentages and ratios are kept alongside, because they are how two budgets of
different size are compared — but the headline is always money: "+2 340 $,
pire creux −1 100 $".
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def drawdown(equity: pd.Series) -> dict:
    peak = equity.cummax()
    dd_usd = equity - peak
    trough_day = dd_usd.idxmin()
    peak_day = equity.loc[:trough_day].idxmax()
    recovered = equity.loc[trough_day:]
    back = recovered[recovered >= equity.loc[peak_day]]
    return {
        "max_drawdown_usd": round(float(dd_usd.min()), 2),
        "max_drawdown_pct": round(float((equity / peak - 1).min() * 100), 2),
        "peak_day": peak_day.date().isoformat(),
        "trough_day": trough_day.date().isoformat(),
        "recovered_day": back.index[0].date().isoformat() if len(back) else None,
    }


def _perf(equity: pd.Series, budget: float) -> dict:
    final = float(equity.iloc[-1])
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1 / 365.25)
    daily = equity.pct_change().dropna()
    vol = float(daily.std() * math.sqrt(TRADING_DAYS)) if len(daily) > 1 else 0.0
    sharpe = float(daily.mean() / daily.std() * math.sqrt(TRADING_DAYS)) if len(daily) > 1 and daily.std() > 0 else 0.0
    return {
        "final_equity": round(final, 2),
        "pnl_usd": round(final - budget, 2),
        "pnl_pct": round((final / budget - 1) * 100, 2),
        "cagr_pct": round(((final / budget) ** (1 / years) - 1) * 100, 2) if final > 0 else -100.0,
        "volatility_pct": round(vol * 100, 2),
        "sharpe": round(sharpe, 2),
        **drawdown(equity),
    }


def yearly(equity: pd.Series, bench: pd.Series, budget: float) -> list[dict]:
    out = []
    prev_e, prev_b = budget, budget
    for year, grp in equity.groupby(equity.index.year):
        e = float(grp.iloc[-1])
        b = float(bench.loc[grp.index].iloc[-1])
        out.append({"year": int(year), "pnl_usd": round(e - prev_e, 2),
                    "pnl_pct": round((e / prev_e - 1) * 100, 2),
                    "benchmark_pnl_pct": round((b / prev_b - 1) * 100, 2)})
        prev_e, prev_b = e, b
    return out


def trade_stats(fills) -> dict:
    exits = [f for f in fills if f.side == "sell" and f.pnl is not None]
    wins = [f.pnl for f in exits if f.pnl > 0]
    losses = [f.pnl for f in exits if f.pnl <= 0]
    gross_win, gross_loss = sum(wins), -sum(losses)
    return {
        "n_buys": sum(1 for f in fills if f.side == "buy"),
        "n_sells": len(exits),
        "hit_rate_pct": round(len(wins) / len(exits) * 100, 1) if exits else None,
        "avg_win_usd": round(float(np.mean(wins)), 2) if wins else None,
        "avg_loss_usd": round(float(np.mean(losses)), 2) if losses else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "avg_hold_days": round(float(np.mean([f.held_days for f in exits])), 1) if exits else None,
        "best_trade_usd": round(max(f.pnl for f in exits), 2) if exits else None,
        "worst_trade_usd": round(min(f.pnl for f in exits), 2) if exits else None,
    }


def exit_reasons(fills) -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in fills:
        if f.side == "sell":
            key = f.reason.split(" (")[0]
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def summarize(r) -> dict:
    perf = _perf(r.equity, r.budget)
    bench = _perf(r.benchmark, r.budget)
    plac = _perf(r.placebo, r.budget)
    return {
        **perf,
        "exposure_pct": round(float(r.invested.mean() * 100), 1),
        "trades": trade_stats(r.fills),
        "exit_reasons": exit_reasons(r.fills),
        "benchmark": {"symbol": "SPY", **{k: bench[k] for k in (
            "final_equity", "pnl_usd", "pnl_pct", "cagr_pct", "sharpe", "max_drawdown_usd", "max_drawdown_pct")}},
        "vs_benchmark_usd": round(perf["pnl_usd"] - bench["pnl_usd"], 2),
        "placebo": {k: plac[k] for k in ("final_equity", "pnl_usd", "pnl_pct", "cagr_pct", "max_drawdown_usd",
                                          "max_drawdown_pct")},
        "vs_placebo_usd": round(perf["pnl_usd"] - plac["pnl_usd"], 2),
        "return_over_drawdown": (round(perf["pnl_usd"] / -perf["max_drawdown_usd"], 2)
                                 if perf["max_drawdown_usd"] < 0 else None),
        "yearly": yearly(r.equity, r.benchmark, r.budget),
        "diagnostics": r.diagnostics,
    }
