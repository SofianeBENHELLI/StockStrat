"""Order lifecycle: pre-trade guardrails, the broker port, resting orders and
settlement.

The invariant under test throughout is ordering: every check that can refuse an
order runs *before* the broker is called, so an order can never execute and then
be refused by the book.
"""
from __future__ import annotations

import pytest

from app.core import settings_store
from app.data.provider import Quote
from app.models import PaperOrder, PaperPortfolio, Position, Variant
from app.paper import settlement
from app.paper.brokers import BrokerUnavailable, OrderSpec, SimBroker, available_brokers, get_broker
from app.paper.service import OrderRejected, cancel_order, poll_open_orders, submit_order


@pytest.fixture()
def stub_prices(monkeypatch):
    prices = {"AAPL": 100.0}

    def fake(symbols):
        return {s: Quote(symbol=s, price=prices[s], source="mock") for s in symbols if s in prices}

    monkeypatch.setattr("app.paper.service.latest_prices", fake)
    return prices


def _portfolio(db, cash=100_000.0) -> PaperPortfolio:
    variant = Variant(name="Execution Test", engine="manual")
    db.add(variant)
    db.commit()
    db.refresh(variant)
    portfolio = PaperPortfolio(variant_id=variant.id, initial_cash=cash, cash=cash)
    db.add(portfolio)
    db.commit()
    db.refresh(portfolio)
    return portfolio


# --- pre-trade guardrails ---------------------------------------------------

def test_cash_is_checked_before_the_broker_runs(db, stub_prices):
    """The regression this guards: affordability used to be checked while
    applying the fill, i.e. after execution."""
    portfolio = _portfolio(db, cash=500.0)
    with pytest.raises(OrderRejected, match="insufficient paper cash"):
        submit_order(db, portfolio=portfolio, symbol="AAPL", side="buy", qty=100, max_loss=10_000.0)

    assert portfolio.cash == 500.0
    assert db.query(PaperOrder).count() == 0      # nothing was recorded as attempted at a venue
    assert db.query(Position).count() == 0


def test_the_cash_check_leaves_room_for_spread_and_slippage(db, stub_prices):
    """Exactly-affordable at the last price is not affordable once the order
    fills at the far side of the spread."""
    portfolio = _portfolio(db, cash=1_000.0)
    with pytest.raises(OrderRejected, match="insufficient paper cash"):
        submit_order(db, portfolio=portfolio, symbol="AAPL", side="buy", qty=10, max_loss=1_000.0)


def test_per_order_notional_cap_is_enforced(db, stub_prices):
    portfolio = _portfolio(db)
    settings_store.apply_updates(db, {"execution.max_order_notional": 500.0})
    with pytest.raises(OrderRejected, match="per-order cap"):
        submit_order(db, portfolio=portfolio, symbol="AAPL", side="buy", qty=50, max_loss=5_000.0)


def test_cannot_sell_more_than_is_held(db, stub_prices):
    portfolio = _portfolio(db)
    with pytest.raises(OrderRejected, match="only 0 held"):
        submit_order(db, portfolio=portfolio, symbol="AAPL", side="sell", qty=5)


def test_missing_quote_is_refused_before_the_broker(db, stub_prices):
    portfolio = _portfolio(db)
    with pytest.raises(OrderRejected, match="no market price"):
        submit_order(db, portfolio=portfolio, symbol="NOPE", side="buy", qty=1, max_loss=100.0)


# --- resting orders ---------------------------------------------------------

def test_an_uncrossed_limit_rests_instead_of_being_rejected(db, stub_prices):
    portfolio = _portfolio(db)
    order = submit_order(db, portfolio=portfolio, symbol="AAPL", side="buy", qty=10,
                         order_type="limit", limit_price=80.0, max_loss=1_000.0)
    assert order.status == "open"
    assert order.filled_qty == 0.0
    assert portfolio.cash == 100_000.0       # nothing settled, so no cash moved


def test_polling_fills_a_resting_order_once_the_price_comes_to_it(db, stub_prices):
    portfolio = _portfolio(db)
    order = submit_order(db, portfolio=portfolio, symbol="AAPL", side="buy", qty=10,
                         order_type="limit", limit_price=80.0, max_loss=1_000.0)
    assert order.status == "open"

    stub_prices["AAPL"] = 75.0               # the limit is now marketable
    changed = poll_open_orders(db, portfolio)

    assert [o.id for o in changed] == [order.id]
    db.refresh(order)
    assert order.status in ("filled", "partial_fill")
    assert portfolio.cash < 100_000.0


def test_polling_leaves_an_order_that_still_has_not_crossed(db, stub_prices):
    portfolio = _portfolio(db)
    submit_order(db, portfolio=portfolio, symbol="AAPL", side="buy", qty=10,
                 order_type="limit", limit_price=80.0, max_loss=1_000.0)
    assert poll_open_orders(db, portfolio) == []


def test_cancelling_an_open_order(db, stub_prices):
    portfolio = _portfolio(db)
    order = submit_order(db, portfolio=portfolio, symbol="AAPL", side="buy", qty=10,
                         order_type="limit", limit_price=80.0, max_loss=1_000.0)
    cancel_order(db, order)
    assert order.status == "cancelled"
    assert poll_open_orders(db, portfolio) == []


def test_cancelling_a_filled_order_is_refused(db, stub_prices):
    portfolio = _portfolio(db)
    order = submit_order(db, portfolio=portfolio, symbol="AAPL", side="buy", qty=1, max_loss=200.0)
    with pytest.raises(OrderRejected, match="only an open order"):
        cancel_order(db, order)


# --- settlement -------------------------------------------------------------

def test_settling_the_same_order_twice_is_refused(db, stub_prices):
    """Two writers (an HTTP request and the monitor loop) can reach the same
    order. Double settlement would silently double a position."""
    portfolio = _portfolio(db)
    order = submit_order(db, portfolio=portfolio, symbol="AAPL", side="buy", qty=1, max_loss=200.0)
    qty_after_first = db.query(Position).filter_by(portfolio_id=portfolio.id).one().qty
    cash_after_first = portfolio.cash

    assert settlement.apply_fill(db, portfolio=portfolio, order=order,
                                 filled_qty=1, price=100.0) is False
    assert db.query(Position).filter_by(portfolio_id=portfolio.id).one().qty == qty_after_first
    assert portfolio.cash == cash_after_first


def test_reopening_a_closed_position_restarts_its_clock(db, stub_prices):
    """Position rows are reused, so the holding-period exit would otherwise
    measure from the first time the symbol was ever bought."""
    portfolio = _portfolio(db)
    submit_order(db, portfolio=portfolio, symbol="AAPL", side="buy", qty=10, max_loss=2_000.0)
    pos = db.query(Position).filter_by(portfolio_id=portfolio.id).one()
    first_opened = pos.opened_at

    submit_order(db, portfolio=portfolio, symbol="AAPL", side="sell", qty=pos.qty)
    db.refresh(pos)
    assert pos.qty == 0.0

    submit_order(db, portfolio=portfolio, symbol="AAPL", side="buy", qty=5, max_loss=1_000.0)
    db.refresh(pos)
    assert pos.opened_at >= first_opened


# --- the port itself --------------------------------------------------------

def test_sim_broker_reports_a_rejection_as_a_value_not_an_exception():
    result = SimBroker().submit(OrderSpec(order_id=1, symbol="AAPL", side="buy", qty=10), None)
    assert result.status == "rejected"
    assert "no market price" in result.detail


def test_unknown_broker_falls_back_to_the_simulator():
    """A stale broker name in the database must never stop a paper portfolio
    from trading."""
    assert get_broker("something-removed").name == "sim"


def test_alpaca_is_declared_but_refuses_to_construct():
    assert any(b["name"] == "alpaca_paper" and not b["available"] for b in available_brokers())
    with pytest.raises(BrokerUnavailable, match="phase 3"):
        get_broker("alpaca_paper")
