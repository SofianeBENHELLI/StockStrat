"""The shared engine: costs, exits, cash, cadence — and no look-ahead.

A backtest can be wrong in ways that make it look *better*, and nothing on the
screen would show it. These tests pin the behaviours that would silently
flatter a result: free trades, stops that never fire, cash that appears from
nowhere, and a model that peeks at the future.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.lab import engine
from app.lab.engine import Book, Holding, backtest, plan
from app.lab.metrics import drawdown
from app.lab.profiles import Decision, Profile, get_profile
from app.lab.profiles.base import risk_params
from app.lab.universe import BENCHMARK
from tests.lab_fixtures import path, random_market


# -- a scripted profile: buys on day 0, optionally sells on a given row --------

@dataclass
class Ctx:
    close: pd.DataFrame


class Scripted(Profile):
    key = "scripted"
    label = "Scripted"
    cadence = "daily"

    def __init__(self, sell_on: int | None = None, weight: float = 1.0, symbol: str = "AAA"):
        self.sell_on, self.weight, self.symbol = sell_on, weight, symbol

    def params(self):
        return risk_params(stop=0, trailing=0, take_profit=0, max_hold=0)

    def universe(self, params):
        return [self.symbol]

    def prepare(self, panel, params):
        return Ctx(close=panel.close)

    def decide(self, ctx, i, book, params, rebalance):
        d = Decision()
        if i == self.sell_on and self.symbol in book.holdings:
            d.exits[self.symbol] = "script"
        elif not book.holdings and self.sell_on is None or (i == 0 and not book.holdings):
            d.entries[self.symbol] = self.weight
        return d


def run(profile: Profile, panel, params: dict | None = None, budget: float = 10_000.0):
    p = profile.resolve(params or {})
    ctx = profile.prepare(panel, p)
    book = Book(cash=budget)
    fills = []
    equity = []
    for i in range(len(ctx.close.index)):
        prices = ctx.close.iloc[i]
        for h in book.holdings.values():
            h.peak = max(h.peak, float(prices[h.symbol]))
        orders, _ = plan(profile, ctx, i, book, p, rebalance=True)
        engine.execute_at_close(book, orders, i, ctx.close.index[i], p["cost_bps"] / 1e4, fills, ctx.close.index)
        equity.append(book.equity(prices))
    return book, fills, equity


def test_costs_are_paid_on_the_way_in_and_on_the_way_out():
    book, fills, _ = run(Scripted(sell_on=3), path([100, 100, 100, 100, 100]), {"cost_bps": 50})
    # flat price, one round trip at 0.5% each way: lose exactly ~1%
    assert book.cash == pytest.approx(10_000 * (1 - 0.005) / (1 + 0.005), rel=1e-9)
    assert [f.side for f in fills] == ["buy", "sell"]
    assert fills[1].pnl < 0


def test_stop_loss_fires_on_the_close_that_breaches_it():
    profile = Scripted()
    book, fills, _ = run(profile, path([100, 99, 95, 91, 90, 120]), {"stop_loss_pct": 8, "cost_bps": 0})
    sells = [f for f in fills if f.side == "sell"]
    assert len(sells) == 1 and sells[0].price == 91 and "stop de perte" in sells[0].reason


def test_trailing_stop_follows_the_peak_even_above_the_entry_price():
    """+30% then -15% from the top is still +10.5% on entry — a fixed stop never
    fires, the trailing one must."""
    profile = Scripted(sell_on=None)
    _, fills, _ = run(profile, path([100, 110, 130, 120, 110.5, 105]),
                      {"trailing_stop_pct": 15, "cost_bps": 0})
    sell = next(f for f in fills if f.side == "sell")
    assert sell.price == 110.5 and "stop suiveur" in sell.reason
    assert sell.pnl > 0


def test_a_missing_price_never_triggers_an_exit():
    book = Book(cash=0, holdings={"AAA": Holding("AAA", 10, 100, 0, 100)})
    prices = pd.Series({"AAA": float("nan")})
    assert engine.risk_exits(book, prices, 5, {"stop_loss_pct": 1, "trailing_stop_pct": 1,
                                               "take_profit_pct": 1, "max_hold_days": 1}) == {}


def test_buys_never_spend_more_than_the_cash_available():
    """Two entries at 100% weight each: the second must be scaled, not financed
    by an overdraft."""
    class Greedy(Scripted):
        def universe(self, params):
            return ["AAA", "BBB"]

        def decide(self, ctx, i, book, params, rebalance):
            return Decision(entries={"AAA": 1.0, "BBB": 1.0}) if i == 0 else Decision()

    book, fills, _ = run(Greedy(), path([100] * 3, extra={"BBB": [50] * 3}), {"cost_bps": 10})
    assert book.cash >= -1e-9
    spent = sum(f.qty * f.price for f in fills if f.side == "buy")
    assert spent <= 10_000


def test_the_rebalance_band_does_not_churn_small_drifts():
    ctx = Ctx(close=pd.DataFrame({"AAA": [100.0, 104.0]}, index=pd.bdate_range("2021-01-04", periods=2)))

    class Holder(Scripted):
        def decide(self, ctx, i, book, params, rebalance):
            return Decision(targets={"AAA": 1.0})

    p = Holder().resolve({})
    book = Book(cash=0, holdings={"AAA": Holding("AAA", 100, 100, 0, 100)})
    orders, _ = plan(Holder(), ctx, 1, book, p, rebalance=True)
    assert orders == []           # 4% drift is inside the 25% band


def test_monthly_profiles_rebalance_only_on_the_first_session_of_a_month():
    dates = pd.bdate_range("2021-01-04", "2021-04-30")
    rows = engine.rebalance_rows(get_profile("stratege"), dates)
    assert sorted(dates[i].strftime("%m-%d") for i in rows) == ["01-04", "02-01", "03-01", "04-01"]
    assert len(engine.rebalance_rows(get_profile("flambeur"), dates)) == len(dates)


# -- drawdown in dollars -------------------------------------------------------

def test_drawdown_is_reported_in_dollars_from_the_running_peak():
    eq = pd.Series([10_000, 12_000, 9_000, 11_000, 13_000],
                   index=pd.bdate_range("2021-01-04", periods=5))
    dd = drawdown(eq)
    assert dd["max_drawdown_usd"] == -3_000
    assert dd["max_drawdown_pct"] == pytest.approx(-25.0)
    assert dd["recovered_day"] == eq.index[4].date().isoformat()


# -- the real profiles on a synthetic market -----------------------------------

@pytest.mark.parametrize("key", ["flambeur", "matheux", "stratege"])
def test_every_default_variant_runs_end_to_end_and_ends_on_the_last_session(key):
    profile = get_profile(key)
    symbols = sorted(set(profile.universe(profile.defaults())) | {BENCHMARK})[:25] + [BENCHMARK]
    panel = random_market(sorted(set(symbols)), days=1100)
    for v in profile.variants():
        r = backtest(key, v.params, date(2022, 1, 3), panel=panel)
        s = r.summary()
        assert r.equity.index[-1] == panel.dates[-1]
        assert s["final_equity"] > 0
        assert s["max_drawdown_usd"] <= 0
        assert r.placebo.iloc[0] == pytest.approx(r.budget)


def test_the_placebo_is_the_universe_held_in_equal_parts():
    panel = path([100, 110, 121], extra={"BBB": [100, 90, 81], BENCHMARK: [100, 100, 100]})
    idx = panel.dates
    placebo = engine.placebo(panel.close[["AAA", "BBB"]], idx, 10_000)
    assert list(placebo.round(6)) == [10_000, 10_000, 10_100]


# -- no look-ahead in the learned model ---------------------------------------

def test_the_model_cannot_see_outcomes_that_were_unknown_on_the_decision_day():
    """Poison every label that would only be known after day i. If the
    prediction at i changes, the model was reading the future."""
    matheux = get_profile("matheux")
    params = matheux.resolve({"model": "ridge", "train_months": 12})
    names = [f"S{k:02d}" for k in range(30)] + [BENCHMARK]
    panel = random_market(names, days=700, seed=3)
    ctx = matheux.prepare(panel, params)
    i = ctx.month_starts[-3]

    clean = matheux._fit_predict(ctx, i, params).copy()

    poisoned = matheux.prepare(panel, params)
    h = params["horizon_days"]
    rng = np.random.default_rng(0)
    future = poisoned.label.index[i - h + 1:]
    poisoned.label.loc[future] = rng.normal(size=(len(future), poisoned.label.shape[1]))
    assert matheux._fit_predict(poisoned, i, params).equals(clean)


def test_the_model_refuses_to_guess_without_enough_history():
    matheux = get_profile("matheux")
    params = matheux.resolve({})
    panel = random_market([f"S{k:02d}" for k in range(20)] + [BENCHMARK], days=300)
    ctx = matheux.prepare(panel, params)
    assert matheux._fit_predict(ctx, ctx.month_starts[3], params) is None
