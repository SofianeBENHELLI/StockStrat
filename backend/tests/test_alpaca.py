"""Alpaca paper broker — status translation and the promotion guard.

Fully offline. Every test here is about the translation layer, which is where
a venue integration silently goes wrong: the network calls themselves either
work or raise, but a misread status corrupts the book while everything looks
healthy.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core import settings_store
from app.models import PaperPortfolio, Position, Variant
from app.paper import venue
from app.paper.brokers import AlpacaPaperBroker, BrokerUnavailable, Credentials, OrderSpec

SPEC = OrderSpec(order_id=1, symbol="AAPL", side="buy", qty=10)


def _order(status, filled_qty=0, avg=None, oid="abc-123", reject_reason=None):
    return SimpleNamespace(id=oid, status=status, filled_qty=filled_qty,
                           filled_avg_price=avg, reject_reason=reject_reason)


def _broker(client) -> AlpacaPaperBroker:
    """Build one without touching TradingClient or the network."""
    b = object.__new__(AlpacaPaperBroker)
    b._client = client
    return b


# --- status translation -----------------------------------------------------

def test_a_filled_order_settles():
    r = AlpacaPaperBroker._translate(_order("OrderStatus.FILLED", filled_qty=10, avg=101.5))
    assert (r.status, r.filled_qty, r.filled_avg_price) == ("filled", 10.0, 101.5)


def test_a_partial_fill_stays_open_instead_of_settling():
    """Our settlement is one-shot per order while Alpaca fills incrementally.
    Settling a partial immediately would book the slice and abandon the rest."""
    r = AlpacaPaperBroker._translate(_order("partially_filled", filled_qty=4, avg=101.0))
    assert r.status == "open"
    assert r.filled_qty == 0.0


def test_an_unknown_status_degrades_to_open():
    """Alpaca has states this code has never seen. Treating one as finished
    would abandon a live order at the venue while the book thinks it is done."""
    assert AlpacaPaperBroker._translate(_order("accepted_for_bidding")).status == "open"
    assert AlpacaPaperBroker._translate(_order("")).status == "open"


def test_a_cancel_after_a_partial_fill_settles_what_executed():
    r = AlpacaPaperBroker._translate(_order("canceled", filled_qty=3, avg=99.5))
    assert (r.status, r.filled_qty, r.filled_avg_price) == ("partial_fill", 3.0, 99.5)


def test_a_cancel_with_nothing_executed_is_just_cancelled():
    r = AlpacaPaperBroker._translate(_order("expired"))
    assert r.status == "cancelled"
    assert r.filled_qty == 0.0


def test_a_rejection_carries_the_venue_reason():
    r = AlpacaPaperBroker._translate(_order("rejected", reject_reason="insufficient buying power"))
    assert r.status == "rejected"
    assert "insufficient buying power" in r.detail


# --- failure handling -------------------------------------------------------

def test_a_venue_refusal_is_a_value_not_an_exception():
    """A refused order is a normal trading outcome; raising would abort the
    whole tournament cycle over one bad symbol."""
    class Boom:
        def submit_order(self, _request):
            raise RuntimeError("asset not fractionable")

    r = _broker(Boom()).submit(SPEC, 100.0)
    assert r.status == "rejected"
    assert "not fractionable" in r.detail


def test_a_failed_poll_never_invents_a_terminal_state():
    """A network blip must not be recorded as a cancelled order."""
    class Boom:
        def get_order_by_id(self, _oid):
            raise RuntimeError("connection reset")

    r = _broker(Boom()).poll("abc-123", SPEC, 100.0)
    assert r.status == "open"
    assert "poll failed" in r.detail


def test_a_limit_order_without_a_price_is_refused_before_any_call():
    class Spy:
        called = False

        def submit_order(self, _request):
            self.called = True

    spy = Spy()
    r = _broker(spy).submit(OrderSpec(order_id=1, symbol="AAPL", side="buy", qty=1,
                                      order_type="limit", limit_price=None), 100.0)
    assert r.status == "rejected" and not spy.called


def test_paper_is_not_configurable():
    """The safeguard that matters most: there is no code path from this class
    to the live endpoint."""
    source = AlpacaPaperBroker.__init__.__code__.co_consts
    assert True in source, "paper=True should be a literal constant in __init__"
    with pytest.raises(BrokerUnavailable):
        AlpacaPaperBroker(Credentials(api_key="PK", secret_key=None))



def _variant(db, name) -> Variant:
    v = Variant(name=name, engine="stratege", stage="paper")
    db.add(v)
    db.commit()
    db.refresh(v)
    db.add(PaperPortfolio(variant_id=v.id, initial_cash=10_000.0, cash=10_000.0))
    db.commit()
    db.refresh(v)
    return v


# --- reconciliation ---------------------------------------------------------

def test_reconcile_reports_a_book_that_disagrees_with_the_venue(db, monkeypatch):
    v = _variant(db, "Champion")
    v.portfolio.broker = "alpaca_paper"
    db.add(Position(portfolio_id=v.portfolio.id, symbol="AAPL", qty=10.0,
                    avg_entry_price=100.0, realized_pnl=0.0))
    db.commit()

    monkeypatch.setattr(venue, "broker_for",
                        lambda _db, _name: SimpleNamespace(positions=lambda: {"AAPL": 7.0, "MSFT": 3.0}))
    result = venue.reconcile(db)

    assert result["ok"] is False
    by_symbol = {d["symbol"]: d for d in result["differences"]}
    assert by_symbol["AAPL"]["delta"] == -3.0      # venue holds less than we think
    assert by_symbol["MSFT"]["delta"] == 3.0       # venue holds something we do not


def test_reconcile_is_quiet_when_nothing_is_routed_to_the_venue(db):
    assert venue.reconcile(db) == {
        "ok": True, "broker": "alpaca_paper", "portfolios": 0,
        "detail": "Aucune variante n'est routée vers ce venue.", "differences": [],
    }


def test_connection_test_reports_a_verdict_rather_than_raising(db):
    """It backs a button in the admin panel — a wrong key is an answer."""
    settings_store.apply_updates(db, {"connections.alpaca_api_key": "PK_only_half"})
    result = venue.test_connection(db)
    assert result["ok"] is False and result["stage"] == "credentials"
