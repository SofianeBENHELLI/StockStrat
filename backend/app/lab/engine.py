"""The engine: turns a profile's decisions into orders, and replays history.

`plan()` is the heart and is shared. Given the book (cash + holdings) and a
day, it returns the orders that should be placed: risk exits first, then the
profile's own exits, then the rebalance or new entries, scaled to the cash that
will actually be available. The backtest applies those orders at the close; the
paper runner (app/lab/runner.py) sends the very same list to Alpaca. There is
no second implementation of the trading logic anywhere.

Execution model, stated once so nobody has to reverse-engineer it:

- Decisions use the close of day t and execute at that same close. Live, the
  runner acts shortly before the closing bell, on the last trade — the closest
  real-world equivalent.
- Every buy pays and every sell loses `cost_bps` (spread + slippage). No
  commission: Alpaca charges none.
- Fractional shares are allowed (Alpaca supports them on these symbols).
- Stops are evaluated on the close, once a day, identically in paper.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from app.lab import data as market
from app.lab.indicators import month_starts
from app.lab.profiles import Decision, Profile, get_profile
from app.lab.universe import BENCHMARK

REBALANCE_BAND = 0.25   # leave a held line alone unless it is 25% off its target weight
MIN_ORDER_USD = 5.0     # below this an order costs more attention than it is worth


@dataclass
class Holding:
    symbol: str
    qty: float
    entry_price: float     # cost basis per share, costs included
    entry_i: int           # row of the entry day in the panel
    peak: float            # highest close since entry — for the trailing stop


@dataclass
class Book:
    cash: float
    holdings: dict[str, Holding] = field(default_factory=dict)

    def market_value(self, prices: pd.Series) -> float:
        total = 0.0
        for s, h in self.holdings.items():
            p = prices.get(s)
            total += h.qty * (p if p is not None and not math.isnan(p) else h.entry_price)
        return total

    def equity(self, prices: pd.Series) -> float:
        return self.cash + self.market_value(prices)


@dataclass
class Order:
    symbol: str
    side: str              # buy | sell
    qty: float
    price: float           # reference price the order was sized at
    reason: str
    note: str = ""

    @property
    def notional(self) -> float:
        return self.qty * self.price


def _price(prices: pd.Series, sym: str) -> float | None:
    p = prices.get(sym)
    return None if p is None or math.isnan(p) or p <= 0 else float(p)


def risk_exits(book: Book, prices: pd.Series, i: int, params: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for sym, h in book.holdings.items():
        p = _price(prices, sym)
        if p is None:
            continue           # no price today: never exit on a missing number
        change = (p / h.entry_price - 1) * 100
        from_peak = (p / max(h.peak, p) - 1) * 100
        if params["stop_loss_pct"] > 0 and change <= -params["stop_loss_pct"]:
            out[sym] = f"stop de perte ({change:+.1f} %)"
        elif params["trailing_stop_pct"] > 0 and from_peak <= -params["trailing_stop_pct"]:
            out[sym] = f"stop suiveur ({from_peak:+.1f} % depuis le plus haut)"
        elif params["take_profit_pct"] > 0 and change >= params["take_profit_pct"]:
            out[sym] = f"prise de bénéfice ({change:+.1f} %)"
        elif params["max_hold_days"] > 0 and i - h.entry_i >= params["max_hold_days"]:
            out[sym] = f"durée maximale ({i - h.entry_i} séances)"
    return out


def plan(profile: Profile, ctx, i: int, book: Book, params: dict, rebalance: bool) -> tuple[list[Order], Decision]:
    prices = ctx.close.iloc[i]
    decision = profile.decide(ctx, i, book, params, rebalance)
    exits = risk_exits(book, prices, i, params)
    for sym, reason in decision.exits.items():
        exits.setdefault(sym, reason)

    orders: list[Order] = []
    cash = book.cash
    for sym, reason in exits.items():
        p = _price(prices, sym)
        if p is None:
            continue
        h = book.holdings[sym]
        orders.append(Order(sym, "sell", h.qty, p, reason))
        cash += h.qty * p

    staying = {s: h for s, h in book.holdings.items() if s not in exits}
    equity = book.equity(prices)
    buys: list[tuple[str, float, str]] = []

    if decision.targets is not None:
        for sym, h in staying.items():
            if sym not in decision.targets:
                p = _price(prices, sym)
                if p is not None:
                    orders.append(Order(sym, "sell", h.qty, p, "sort du portefeuille cible",
                                        decision.notes.get(sym, "")))
                    cash += h.qty * p
        for sym, weight in decision.targets.items():
            if sym in exits:
                continue
            p = _price(prices, sym)
            if p is None:
                continue
            target = weight * equity
            current = staying[sym].qty * p if sym in staying else 0.0
            if sym in staying and abs(current - target) <= REBALANCE_BAND * target:
                continue
            if current > target:
                qty = (current - target) / p
                orders.append(Order(sym, "sell", qty, p, "allègement", decision.notes.get(sym, "")))
                cash += qty * p
            else:
                buys.append((sym, target - current, "renforcement" if sym in staying else "entrée"))

    for sym, weight in decision.entries.items():
        if sym not in staying and sym not in exits:
            buys.append((sym, weight * equity, "entrée"))

    cost = params["cost_bps"] / 10_000
    wanted = sum(v for _, v, _ in buys)
    scale = min(1.0, (cash / (1 + cost)) / wanted) if wanted > 0 else 1.0
    for sym, value, reason in buys:
        value *= scale
        p = _price(prices, sym)
        if p is None or value < MIN_ORDER_USD:
            continue
        orders.append(Order(sym, "buy", value / p, p, reason, decision.notes.get(sym, "")))
    return orders, decision


# ---------------------------------------------------------------- backtest --

@dataclass
class Fill:
    day: pd.Timestamp
    symbol: str
    side: str
    qty: float
    price: float
    reason: str
    pnl: float | None = None        # realised on sells
    held_days: int | None = None


def execute_at_close(book: Book, orders: list[Order], i: int, day: pd.Timestamp, cost: float,
                     fills: list[Fill], dates: pd.DatetimeIndex) -> None:
    for o in orders:
        if o.side != "sell":
            continue
        h = book.holdings.get(o.symbol)
        if h is None:
            continue
        qty = min(o.qty, h.qty)
        proceeds = qty * o.price * (1 - cost)
        pnl = proceeds - qty * h.entry_price
        book.cash += proceeds
        h.qty -= qty
        fills.append(Fill(day, o.symbol, "sell", qty, o.price, o.reason, pnl, i - h.entry_i))
        if h.qty <= 1e-9:
            del book.holdings[o.symbol]
    for o in orders:
        if o.side != "buy":
            continue
        spend = o.qty * o.price * (1 + cost)
        qty = o.qty
        if spend > book.cash:
            qty = book.cash / (o.price * (1 + cost))
            spend = book.cash
        if qty <= 0:
            continue
        book.cash -= spend
        basis = o.price * (1 + cost)
        h = book.holdings.get(o.symbol)
        if h is None:
            book.holdings[o.symbol] = Holding(o.symbol, qty, basis, i, o.price)
        else:
            h.entry_price = (h.entry_price * h.qty + basis * qty) / (h.qty + qty)
            h.qty += qty
        fills.append(Fill(day, o.symbol, "buy", qty, o.price, o.reason))


def rebalance_rows(profile: Profile, dates: pd.DatetimeIndex) -> set[int]:
    return set(range(len(dates))) if profile.cadence == "daily" else set(month_starts(dates))


@dataclass
class BacktestResult:
    profile: str
    params: dict
    start: date
    end: date
    budget: float
    equity: pd.Series
    invested: pd.Series
    benchmark: pd.Series
    placebo: pd.Series
    fills: list[Fill]
    diagnostics: dict

    def summary(self) -> dict:
        from app.lab.metrics import summarize
        return summarize(self)


def load_for(profile: Profile, params: dict) -> market.Panel:
    return market.load_panel(sorted(set(profile.universe(params)) | {BENCHMARK}), profile.data_name)


def backtest(profile_key: str, raw_params: dict | None, start: date, end: date | None = None,
             budget: float = 10_000.0, panel: market.Panel | None = None) -> BacktestResult:
    profile = get_profile(profile_key)
    params = profile.resolve(raw_params)
    panel = panel if panel is not None else load_for(profile, params)
    if end is not None:
        panel = panel.until(pd.Timestamp(end))
    ctx = profile.prepare(panel.select(profile.universe(params)), params)
    dates = ctx.close.index
    start_i = int(dates.searchsorted(pd.Timestamp(start)))
    if start_i >= len(dates) - 1:
        raise ValueError("période trop courte : aucune séance après la date de début")
    rebal = rebalance_rows(profile, dates)
    cost = params["cost_bps"] / 10_000

    book = Book(cash=budget)
    fills: list[Fill] = []
    equity, invested = [], []
    for i in range(start_i, len(dates)):
        prices = ctx.close.iloc[i]
        for sym, h in book.holdings.items():
            p = _price(prices, sym)
            if p is not None:
                h.peak = max(h.peak, p)
        orders, _ = plan(profile, ctx, i, book, params, rebalance=(i == start_i or i in rebal))
        execute_at_close(book, orders, i, dates[i], cost, fills, dates)
        eq = book.equity(prices)
        equity.append(eq)
        invested.append(0.0 if eq <= 0 else 1 - book.cash / eq)

    idx = dates[start_i:]
    bench_close = panel.close[BENCHMARK].loc[idx[0]:idx[-1]].ffill()
    bench = budget * bench_close / bench_close.iloc[0]
    diag = profile.diagnostics(ctx, params) if hasattr(profile, "diagnostics") else {}
    return BacktestResult(profile.key, params, idx[0].date(), idx[-1].date(), budget,
                          pd.Series(equity, index=idx), pd.Series(invested, index=idx), bench,
                          placebo(ctx.close, idx, budget), fills, diag)


def placebo(close: pd.DataFrame, idx: pd.DatetimeIndex, budget: float) -> pd.Series:
    """The profile's whole universe, bought in equal parts on day one and held.

    This is the control that tells skill from luck. A model trading today's
    mega-caps over ten years inherits their survivorship for free: anything
    picked from that list tends to beat the index. If a model does not beat
    this placebo — the same list with no decisions at all — its results are the
    universe talking, not the model."""
    window = close.loc[idx[0]:idx[-1]]
    start = window.iloc[0]
    alive = start[start.notna() & (start > 0)].index
    growth = (window[alive].ffill() / start[alive]).mean(axis=1)
    return budget * growth
