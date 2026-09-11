from __future__ import annotations

from datetime import date

import pandas as pd

from app.backtest import engine as bt
from app.backtest.portfolio import BtPortfolio
from app.data.provider import MockProvider
from app.data import macro as macro_module
from app.ml import inference as inference_module


def _mock_price_history(universe, days):
    return MockProvider().history(universe, days)


def test_truncate_histories_has_no_lookahead(monkeypatch):
    monkeypatch.setattr(bt, "price_history", _mock_price_history)
    histories = _mock_price_history(["TEST"], 60)
    df = histories["TEST"].df
    as_of = df.index[30].date()

    truncated_before = bt._truncate_histories(histories, as_of)
    close_before = truncated_before["TEST"].df["close"].copy()

    mutated = {"TEST": histories["TEST"]}
    mutated["TEST"].df.loc[df.index[31:], "close"] = 999_999.0  # mutate strictly after as_of
    truncated_after = bt._truncate_histories(mutated, as_of)

    pd.testing.assert_series_equal(close_before, truncated_after["TEST"].df["close"])
    assert truncated_after["TEST"].df.index.max().date() <= as_of


def test_apply_kill_scale_boosts_cash_only_once():
    portfolio = BtPortfolio(
        variant_id="ml-A", name="test", engine="ml", variant_key="A",
        initial_cash=100_000.0, cash=100_000.0, n_fills=5,
    )
    prices = {"AAPL": 100.0}
    portfolios = {"ml-A": portfolio}

    # first pass: equity above SCALE_THRESHOLD_PCT -> boosted once
    portfolio.positions["AAPL"] = _position_worth(portfolio, prices, pct_above_threshold=10.0)
    bt._apply_kill_scale(portfolios, prices)
    assert portfolio.scaled is True
    cash_after_first = portfolio.cash

    # still above threshold on a later call -> must NOT boost again (the bug this guards)
    bt._apply_kill_scale(portfolios, prices)
    assert portfolio.cash == cash_after_first


def test_apply_kill_scale_kills_on_large_loss():
    portfolio = BtPortfolio(
        variant_id="ml-B", name="test", engine="ml", variant_key="B",
        initial_cash=100_000.0, cash=50_000.0, n_fills=5,  # -50% -> well past KILL_THRESHOLD_PCT
    )
    bt._apply_kill_scale({"ml-B": portfolio}, {})
    assert portfolio.status == "killed"


def test_apply_kill_scale_does_nothing_below_min_trades():
    portfolio = BtPortfolio(
        variant_id="ml-C", name="test", engine="ml", variant_key="C",
        initial_cash=100_000.0, cash=200_000.0, n_fills=1,  # +100% but too few fills to judge
    )
    bt._apply_kill_scale({"ml-C": portfolio}, {})
    assert portfolio.status == "active"
    assert portfolio.scaled is False


def test_run_backtest_result_shape(monkeypatch):
    monkeypatch.setattr(bt, "price_history", _mock_price_history)
    monkeypatch.setattr(macro_module, "_fetch_raw", lambda days=macro_module.FETCH_DAYS: None)
    monkeypatch.setattr(inference_module, "model_metadata", lambda: None)

    result = bt.run_backtest(years=1, rebalance_every_days=25)  # coarse, fast

    assert result["years"] == 1
    assert len(result["results"]) == 14  # 4 casino + 5 ml + 5 economist
    assert "ml_insample_caveat" in result and result["ml_insample_caveat"]
    row = result["results"][0]
    for key in ("variant_id", "name", "engine", "status", "total_pnl_pct", "sharpe", "sortino",
                "max_drawdown_pct", "n_fills", "hit_rate_pct", "profit_factor", "equity_history"):
        assert key in row
    assert len(row["equity_history"]) > 0
    assert all(len(r["equity_history"]) == len(row["equity_history"]) for r in result["results"])
    # buy-only engine, same as live — see engine.py's module docstring
    assert all(r["hit_rate_pct"] is None and r["profit_factor"] is None for r in result["results"])


def _position_worth(portfolio: BtPortfolio, prices: dict, pct_above_threshold: float):
    """Build a position sized so `portfolio.cash + positions_value` (i.e.
    total equity) sits `pct_above_threshold` points above initial cash —
    keeps the test's math obvious to read."""
    from app.backtest.portfolio import BtPosition
    target_equity = portfolio.initial_cash * (1 + pct_above_threshold / 100)
    price = next(iter(prices.values()))
    qty = (target_equity - portfolio.cash) / price
    return BtPosition(symbol=next(iter(prices)), qty=qty, avg_entry_price=price)
