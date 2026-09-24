"""The standing safety stop kept at the broker.

Exercised on the simulator (which rests and triggers stop orders like a
venue) by letting the safety module treat it as one. The Alpaca translation of
a stop order is the same code path as any other order.
"""
from __future__ import annotations

import pytest

from app.core import settings_store
from app.data.provider import Quote
from app.models import PaperOrder, PaperPortfolio, Position, Variant
from app.paper import safety
from app.paper.service import open_orders, poll_open_orders, set_kill_switch, submit_order


@pytest.fixture()
def venue(db, monkeypatch):
    prices = {"AAA": 100.0}
    monkeypatch.setattr("app.paper.service.latest_prices",
                        lambda symbols: {s: Quote(s, prices[s], "mock") for s in symbols if s in prices})
    monkeypatch.setattr(safety, "VENUE_BROKERS", ("sim",))
    settings_store.apply_updates(db, {"execution.safety_stop_pct": 25})
    variant = Variant(name="Protégé", engine="stratege", stage="paper")
    db.add(variant)
    db.commit()
    portfolio = PaperPortfolio(variant_id=variant.id, initial_cash=10_000, cash=10_000, broker="sim")
    db.add(portfolio)
    db.commit()
    db.refresh(portfolio)
    return {"prices": prices, "portfolio": portfolio}


def _stops(db, portfolio):
    return open_orders(db, portfolio, purpose="safety_stop")


def test_a_buy_gets_a_stop_on_its_whole_shares(db, venue):
    p = venue["portfolio"]
    submit_order(db, portfolio=p, symbol="AAA", side="buy", qty=10.5, max_loss=500)
    [stop] = _stops(db, p)
    held = db.query(Position).filter_by(portfolio_id=p.id, symbol="AAA").one()
    assert stop.qty == 10                                   # whole shares only
    assert stop.stop_price == pytest.approx(round(held.avg_entry_price * 0.75, 2))
    assert stop.time_in_force == "gtc" and stop.order_type == "stop"


def test_the_stop_is_protection_not_a_pending_decision(db, venue):
    p = venue["portfolio"]
    submit_order(db, portfolio=p, symbol="AAA", side="buy", qty=10, max_loss=500)
    assert open_orders(db, p) == []                         # the runner's view: nothing pending
    assert len(open_orders(db, p, purpose=None)) == 1


def test_selling_withdraws_the_stop_then_repositions_it_on_what_remains(db, venue):
    p = venue["portfolio"]
    submit_order(db, portfolio=p, symbol="AAA", side="buy", qty=10.5, max_loss=500)
    first = _stops(db, p)[0]
    submit_order(db, portfolio=p, symbol="AAA", side="sell", qty=5)
    db.refresh(first)
    assert first.status == "cancelled"
    [second] = _stops(db, p)
    assert second.qty == 5                                   # 5.5 held -> 5 whole shares


def test_a_full_exit_leaves_no_stop_behind(db, venue):
    p = venue["portfolio"]
    submit_order(db, portfolio=p, symbol="AAA", side="buy", qty=10, max_loss=500)
    held = db.query(Position).filter_by(portfolio_id=p.id, symbol="AAA").one().qty
    submit_order(db, portfolio=p, symbol="AAA", side="sell", qty=held)
    assert _stops(db, p) == []


def test_a_crash_triggers_the_stop_and_realises_the_loss(db, venue):
    p = venue["portfolio"]
    submit_order(db, portfolio=p, symbol="AAA", side="buy", qty=10.5, max_loss=500)
    venue["prices"]["AAA"] = 60.0                            # -40%, through the 25% floor
    changed = poll_open_orders(db, p)
    [fired] = [o for o in changed if o.purpose == "safety_stop"]
    assert fired.status == "filled" and fired.realized_pnl < 0
    pos = db.query(Position).filter_by(portfolio_id=p.id, symbol="AAA").one()
    assert pos.qty == pytest.approx(0.5)                     # the fraction was never covered
    assert _stops(db, p) == []                               # nothing left to protect in whole shares


def test_the_kill_switch_does_not_block_protection(db, venue):
    p = venue["portfolio"]
    submit_order(db, portfolio=p, symbol="AAA", side="buy", qty=4, max_loss=500)
    set_kill_switch(db, True, "gel")
    safety.release(db, p, "AAA")
    assert safety.ensure(db, p, "AAA") is not None


def test_zero_disables_it(db, venue):
    settings_store.apply_updates(db, {"execution.safety_stop_pct": 0})
    p = venue["portfolio"]
    submit_order(db, portfolio=p, symbol="AAA", side="buy", qty=10, max_loss=500)
    assert _stops(db, p) == []
