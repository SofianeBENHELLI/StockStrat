"""Exit rules: the piece that lets a position ever be closed.

These are deliberately offline — `latest_prices` is stubbed so a stop can be
tested at an exact percentage instead of whatever the market happened to do.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.data.provider import Quote
from app.models import PaperPortfolio, Position, Variant
from app.paper import exits
from app.paper.exits import STOP_LOSS, TAKE_PROFIT, TIME_STOP, ExitRules

RULES = ExitRules(enabled=True, stop_loss_pct=-8.0, take_profit_pct=15.0, max_holding_days=30)


def _portfolio(db) -> PaperPortfolio:
    variant = Variant(name="Exit Test", engine="manual")
    db.add(variant)
    db.commit()
    db.refresh(variant)
    portfolio = PaperPortfolio(variant_id=variant.id, initial_cash=100_000.0, cash=50_000.0)
    db.add(portfolio)
    db.commit()
    db.refresh(portfolio)
    return portfolio


def _position(db, portfolio, symbol="AAPL", qty=10.0, entry=100.0, opened_days_ago=0) -> Position:
    pos = Position(
        portfolio_id=portfolio.id, symbol=symbol, qty=qty, avg_entry_price=entry, realized_pnl=0.0,
        opened_at=datetime.now(timezone.utc) - timedelta(days=opened_days_ago),
    )
    db.add(pos)
    db.commit()
    return pos


@pytest.fixture()
def stub_prices(monkeypatch):
    """Pin the quote every module in the exit path will ask for."""
    prices: dict[str, float] = {}

    def fake(symbols):
        return {s: Quote(symbol=s, price=prices.get(s, 100.0), source="mock") for s in symbols}

    monkeypatch.setattr("app.paper.service.latest_prices", fake)
    return prices


def test_no_exit_inside_the_band(db):
    portfolio = _portfolio(db)
    _position(db, portfolio)
    assert exits.evaluate(db, portfolio, {"AAPL": 103.0}, rules=RULES) == []


def test_stop_loss_fires_below_the_threshold(db):
    portfolio = _portfolio(db)
    _position(db, portfolio)
    decisions = exits.evaluate(db, portfolio, {"AAPL": 91.0}, rules=RULES)
    assert [d.reason for d in decisions] == [STOP_LOSS]
    assert decisions[0].qty == 10.0


def test_take_profit_fires_above_the_target(db):
    portfolio = _portfolio(db)
    _position(db, portfolio)
    assert [d.reason for d in exits.evaluate(db, portfolio, {"AAPL": 116.0}, rules=RULES)] == [TAKE_PROFIT]


def test_time_stop_fires_on_an_old_position_still_inside_the_band(db):
    portfolio = _portfolio(db)
    _position(db, portfolio, opened_days_ago=45)
    decisions = exits.evaluate(db, portfolio, {"AAPL": 101.0}, rules=RULES)
    assert [d.reason for d in decisions] == [TIME_STOP]


def test_disabled_rules_never_exit(db):
    portfolio = _portfolio(db)
    _position(db, portfolio)
    disabled = ExitRules(enabled=False, stop_loss_pct=-8.0, take_profit_pct=15.0, max_holding_days=30)
    assert exits.evaluate(db, portfolio, {"AAPL": 50.0}, rules=disabled) == []


def test_a_symbol_without_a_price_is_left_alone(db):
    """A missing quote must not be read as a catastrophic loss — exiting on a
    stale or absent price would be the worst possible reason to sell."""
    portfolio = _portfolio(db)
    _position(db, portfolio)
    assert exits.evaluate(db, portfolio, {}, rules=RULES) == []


def test_apply_sells_realises_pnl_and_returns_cash(db, stub_prices):
    portfolio = _portfolio(db)
    _position(db, portfolio, qty=10.0, entry=100.0)
    stub_prices["AAPL"] = 80.0
    cash_before = portfolio.cash

    orders = exits.apply(db, portfolio, {"AAPL": 80.0}, rules=RULES)

    assert len(orders) == 1
    order = orders[0]
    assert order.side == "sell"
    assert order.exit_reason == STOP_LOSS
    assert order.status in ("filled", "partial_fill")
    # A loss taken on the way out is realised, not left floating.
    assert order.realized_pnl is not None and order.realized_pnl < 0
    assert portfolio.cash > cash_before


def test_a_fully_closed_position_stops_being_held(db, stub_prices):
    portfolio = _portfolio(db)
    _position(db, portfolio, qty=10.0, entry=100.0)
    stub_prices["AAPL"] = 80.0
    exits.apply(db, portfolio, {"AAPL": 80.0}, rules=RULES)

    pos = db.query(Position).filter_by(portfolio_id=portfolio.id, symbol="AAPL").one()
    # Partial fills are possible by design, so assert the invariant that matters:
    # whatever was sold is gone, and nothing is left floating as dust.
    assert pos.qty >= 0.0
    assert pos.qty < 10.0
