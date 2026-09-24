"""The feedback loop: measured execution cost and the paper/backtest gap."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.lab import feedback
from app.models import PaperOrder, PaperPortfolio, Variant

IN_SESSION = datetime(2026, 9, 25, 19, 50, tzinfo=timezone.utc)    # 15:50 New York
OVERNIGHT = datetime(2026, 9, 24, 21, 35, tzinfo=timezone.utc)     # 17:35 New York


def _model(db, cost_bps=5):
    v = Variant(name="Mesuré", engine="stratege", stage="paper", params={"cost_bps": cost_bps})
    db.add(v)
    db.commit()
    db.add(PaperPortfolio(variant_id=v.id, initial_cash=10_000, cash=0, broker="alpaca_paper"))
    db.commit()
    db.refresh(v)
    return v


def _fill(db, v, side, requested, filled, at, qty=10):
    db.add(PaperOrder(portfolio_id=v.portfolio.id, symbol="XLE", side=side, qty=qty, status="filled",
                      filled_qty=qty, requested_price=requested, filled_avg_price=filled, broker="alpaca_paper",
                      purpose="trade", created_at=at.replace(tzinfo=None)))
    db.commit()


def test_cost_is_positive_when_the_fill_is_worse_on_either_side(db):
    v = _model(db)
    _fill(db, v, "buy", 100.0, 100.10, IN_SESSION)    # paid 10 bp more
    _fill(db, v, "sell", 100.0, 99.95, IN_SESSION)    # received 5 bp less
    c = feedback.execution_costs(db)["in_session"]
    assert c["n"] == 2 and c["avg_bps"] == pytest.approx(7.5)
    assert c["cost_usd"] == pytest.approx(1.5, abs=0.01)


def test_overnight_orders_are_kept_apart(db):
    """Their gap includes the overnight move: real, but not an execution cost."""
    v = _model(db)
    _fill(db, v, "buy", 100.0, 103.0, OVERNIGHT)       # opened 3% higher
    c = feedback.execution_costs(db)
    assert c["in_session"]["n"] == 0 and c["overnight"]["n"] == 1
    assert c["overnight"]["avg_bps"] == pytest.approx(300)


def test_session_boundaries():
    assert feedback.in_session(datetime(2026, 9, 25, 13, 30, tzinfo=timezone.utc))      # 9:30 NY
    assert not feedback.in_session(datetime(2026, 9, 25, 13, 29, tzinfo=timezone.utc))
    assert not feedback.in_session(datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc))   # 16:00 NY
    assert not feedback.in_session(datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc))   # Saturday


def test_a_higher_measured_cost_suggests_re_backtesting_with_it(db):
    v = _model(db, cost_bps=5)
    for _ in range(12):
        _fill(db, v, "buy", 100.0, 100.12, IN_SESSION)   # 12 bp each
    f = feedback.model_feedback(db, v)
    action = [s for s in f["suggestions"] if s["level"] == "action"]
    assert action and action[0]["suggested_cost_bps"] == 12


def test_too_few_fills_says_so_instead_of_guessing(db):
    v = _model(db)
    _fill(db, v, "buy", 100.0, 100.5, IN_SESSION)
    f = feedback.model_feedback(db, v)
    assert all(s["level"] == "info" for s in f["suggestions"])
    assert "il en faut au moins" in f["suggestions"][0]["text"]


def test_the_gap_is_split_into_execution_and_the_rest(db):
    v = _model(db)
    _fill(db, v, "buy", 100.0, 100.20, IN_SESSION, qty=50)          # 10 $ of execution cost
    f = feedback.model_feedback(db, v, replay={"available": True, "pnl_usd": 300.0}, paper_pnl=-10.0)
    g = f["gap"]
    assert g["gap_usd"] == -310 and g["execution_usd"] == pytest.approx(-10, abs=0.1)
    assert g["other_usd"] == pytest.approx(-300, abs=0.1)
    assert any(s["level"] == "warning" for s in f["suggestions"])


def test_the_simulator_is_not_measured(db):
    v = _model(db)
    v.portfolio.broker = "sim"
    db.add(PaperOrder(portfolio_id=v.portfolio.id, symbol="XLE", side="buy", qty=1, status="filled", filled_qty=1,
                      requested_price=100, filled_avg_price=101, broker="sim", purpose="trade",
                      created_at=IN_SESSION.replace(tzinfo=None)))
    db.commit()
    assert feedback.execution_costs(db)["in_session"]["n"] == 0
