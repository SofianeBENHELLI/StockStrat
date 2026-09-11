"""The background monitor pass.

Tested through `run_pass` directly rather than through the asyncio loop: the
loop only handles scheduling, and everything worth asserting — polling, exits,
snapshots, failure isolation — lives in the pass.
"""
from __future__ import annotations

import pytest

from app.data.provider import Quote
from app.models import PaperPortfolio, PortfolioSnapshot, Position, Variant
from app.monitor import scheduler
from app.paper.service import submit_order


@pytest.fixture()
def stub_prices(monkeypatch):
    prices = {"AAPL": 100.0}

    def fake(symbols):
        return {s: Quote(symbol=s, price=prices[s], source="mock") for s in symbols if s in prices}

    for target in ("app.paper.service.latest_prices", "app.monitor.scheduler.latest_prices"):
        monkeypatch.setattr(target, fake)
    return prices


def _variant(db, name="Monitored", cash=100_000.0) -> Variant:
    variant = Variant(name=name, engine="manual", status="active")
    db.add(variant)
    db.commit()
    db.refresh(variant)
    db.add(PaperPortfolio(variant_id=variant.id, initial_cash=cash, cash=cash))
    db.commit()
    db.refresh(variant)
    return variant


def test_a_pass_snapshots_equity_for_every_active_variant(db, stub_prices):
    """Snapshots are not bookkeeping: Sharpe, Sortino and drawdown are computed
    from their spacing, so a pass that records nothing makes those numbers wrong."""
    _variant(db, "A")
    _variant(db, "B")

    result = scheduler.run_pass(db)

    assert result["portfolios"] == 2
    assert result["errors"] == []
    assert db.query(PortfolioSnapshot).count() == 2


def test_a_pass_resolves_a_resting_order(db, stub_prices):
    variant = _variant(db)
    order = submit_order(db, portfolio=variant.portfolio, symbol="AAPL", side="buy", qty=10,
                         order_type="limit", limit_price=80.0, max_loss=1_000.0)
    assert order.status == "open"

    stub_prices["AAPL"] = 75.0
    result = scheduler.run_pass(db)

    db.refresh(order)
    assert order.status in ("filled", "partial_fill")
    assert result["results"][0]["orders_resolved"][0]["id"] == order.id


def test_a_pass_closes_a_position_that_breached_the_stop(db, stub_prices):
    variant = _variant(db)
    submit_order(db, portfolio=variant.portfolio, symbol="AAPL", side="buy", qty=10, max_loss=2_000.0)

    stub_prices["AAPL"] = 70.0                      # -30%, well through the -8% stop
    result = scheduler.run_pass(db)

    exits = result["results"][0]["exits"]
    assert [e["reason"] for e in exits] == ["stop_loss"]
    assert db.query(Position).filter_by(portfolio_id=variant.portfolio.id).one().qty < 10.0


def test_killed_variants_are_not_monitored(db, stub_prices):
    variant = _variant(db)
    variant.status = "killed"
    db.commit()
    assert scheduler.run_pass(db)["portfolios"] == 0


def test_one_failing_portfolio_does_not_stop_the_others(db, stub_prices, monkeypatch):
    """Failure isolation — the reason each portfolio gets its own try/rollback."""
    _variant(db, "Healthy")
    broken = _variant(db, "Broken")

    original = scheduler._portfolio_pass

    def flaky(session, variant):
        if variant.id == broken.id:
            raise RuntimeError("provider exploded")
        return original(session, variant)

    monkeypatch.setattr(scheduler, "_portfolio_pass", flaky)
    result = scheduler.run_pass(db)

    assert result["portfolios"] == 1
    assert [e["variant"] for e in result["errors"]] == ["Broken"]
