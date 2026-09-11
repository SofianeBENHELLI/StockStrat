from __future__ import annotations

from app.models import PaperPortfolio, Variant
from app.paper.broker import simulate_fill
from app.paper.service import OrderRejected, submit_order


def _make_portfolio(db) -> PaperPortfolio:
    variant = Variant(name="Test Variant", engine="manual")
    db.add(variant)
    db.commit()
    db.refresh(variant)
    portfolio = PaperPortfolio(variant_id=variant.id, initial_cash=100_000.0, cash=100_000.0)
    db.add(portfolio)
    db.commit()
    db.refresh(portfolio)
    return portfolio


def test_market_buy_requires_max_loss(db):
    portfolio = _make_portfolio(db)
    try:
        submit_order(db, portfolio=portfolio, symbol="AAPL", side="buy", qty=1)
        assert False, "expected OrderRejected"
    except OrderRejected as exc:
        assert "max_loss" in str(exc)


def test_market_buy_fills_and_updates_cash(db):
    portfolio = _make_portfolio(db)
    order = submit_order(db, portfolio=portfolio, symbol="AAPL", side="buy", qty=1, max_loss=50.0)
    assert order.status in ("filled", "partial_fill")
    assert order.filled_avg_price is not None
    assert portfolio.cash < 100_000.0


def test_kill_switch_blocks_orders(db):
    from app.paper.service import set_kill_switch

    portfolio = _make_portfolio(db)
    set_kill_switch(db, True, "test freeze")
    try:
        submit_order(db, portfolio=portfolio, symbol="AAPL", side="buy", qty=1, max_loss=50.0)
        assert False, "expected OrderRejected"
    except OrderRejected as exc:
        assert "kill switch" in str(exc)


def test_simulate_fill_applies_slippage_and_spread():
    result = simulate_fill(symbol="AAPL", side="buy", qty=10, order_type="market",
                           limit_price=None, market_price=100.0, order_id=1)
    assert result.status in ("filled", "partial_fill")
    assert result.filled_avg_price > 100.0  # buy fills worse than mid due to spread+slippage
    assert result.spread_bps > 0
    assert result.slippage_bps > 0


def test_simulate_fill_rejects_without_market_price():
    result = simulate_fill(symbol="AAPL", side="buy", qty=10, order_type="market",
                           limit_price=None, market_price=None, order_id=1)
    assert result.status == "rejected"
