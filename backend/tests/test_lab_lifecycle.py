"""A model's whole life, offline: lab -> paper -> daily decisions -> retired.

Runs on the in-process simulator with a synthetic market, so it proves the
wiring — ledger, orders, journal, scheduler windows, allocation cap — without
touching Alpaca. The Alpaca-specific translation is covered in test_alpaca.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core import settings_store
from app.data.provider import Quote
from app.lab import runner, service
from app.lab.universe import BENCHMARK, THEMES
from app.models import LabEvent, PaperOrder, PortfolioSnapshot, Position, Variant
from app.monitor import scheduler
from tests.lab_fixtures import trending_market

PANEL = trending_market(sorted(THEMES) + [BENCHMARK])


@pytest.fixture()
def market(db, monkeypatch):
    last = PANEL.close.iloc[-1]
    prices = {s: float(p) for s, p in last.items()}
    clock = {"is_open": False, "next_close": None}

    monkeypatch.setattr("app.lab.runner.load_for", lambda profile, params: PANEL)
    monkeypatch.setattr("app.lab.data.live_bars", lambda symbols: {})
    monkeypatch.setattr("app.lab.data.latest_prices", lambda symbols: {s: prices[s] for s in symbols if s in prices})
    monkeypatch.setattr("app.paper.service.latest_prices",
                        lambda symbols: {s: Quote(s, prices[s], "mock") for s in symbols if s in prices})
    monkeypatch.setattr("app.lab.runner.market_clock", lambda _db: dict(clock))
    monkeypatch.setattr("app.paper.venue.reconcile", lambda _db, *a: {"ok": True, "detail": "stub", "differences": []})
    settings_store.apply_updates(db, {"execution.default_broker": "sim"})
    return {"prices": prices, "clock": clock}


def _stratege(db) -> Variant:
    service.seed_defaults(db)
    return next(m for m in service.models(db) if m.engine == "stratege" and m.variant_key == "A")


def test_seeding_creates_three_variants_per_profile_once(db):
    assert len(service.seed_defaults(db)) == 9
    assert service.seed_defaults(db) == []
    assert sorted({m.engine for m in service.models(db)}) == ["flambeur", "matheux", "stratege"]


def test_promotion_opens_a_ledger_and_trades_immediately(db, market):
    m = _stratege(db)
    out = service.promote(db, m, 10_000)

    assert m.stage == "paper" and m.portfolio.initial_cash == 10_000
    held = db.query(Position).filter(Position.portfolio_id == m.portfolio.id, Position.qty > 0).all()
    assert len(held) == 3                          # top 3 themes, the variant's default
    assert m.portfolio.cash < 200                  # the budget is deployed, not left idle
    assert m.portfolio.cash >= 0                   # and never overdrawn
    kinds = {e.kind for e in db.query(LabEvent).filter(LabEvent.variant_id == m.id)}
    assert {"promote", "order", "decision"} <= kinds
    assert out["plan"]["rebalance"] is True


def test_parameters_are_frozen_once_in_paper_but_a_clone_is_free(db, market):
    m = _stratege(db)
    service.promote(db, m)
    with pytest.raises(ValueError, match="figés"):
        service.update_model(db, m, params={"top_k": 2})
    clone = service.clone_model(db, m)
    service.update_model(db, clone, params={"top_k": 2})
    assert clone.stage == "lab" and clone.parent_variant_id == m.id


def test_the_allocation_cap_prevents_borrowing(db, market):
    settings_store.apply_updates(db, {"execution.max_total_allocation": 15_000})
    service.seed_defaults(db)
    first, second = service.models(db)[:2]
    service.promote(db, first, 10_000)
    with pytest.raises(ValueError, match="plafond"):
        service.promote(db, second, 10_000)


def test_no_decision_while_an_order_is_still_pending(db, market):
    m = _stratege(db)
    service.promote(db, m)
    db.add(PaperOrder(portfolio_id=m.portfolio.id, symbol="XLE", side="buy", qty=1, status="open",
                      broker="sim", broker_order_id="sim-x", order_type="limit", limit_price=0.01))
    db.commit()
    p = runner.decide_and_execute(db, m)
    assert p.skipped and "attente" in p.skipped


def test_the_scheduler_decides_only_in_the_window_before_the_close(db, market):
    m = _stratege(db)
    service.promote(db, m)
    m.last_decision_on = None
    db.commit()

    market["clock"].update(is_open=True,
                           next_close=(datetime.now(timezone.utc) + timedelta(hours=3)).isoformat())
    assert scheduler.run_pass(db)["decided"] == []

    market["clock"].update(next_close=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat())
    result = scheduler.run_pass(db)
    assert [d["model"] for d in result["decided"]] == [m.name]
    assert db.query(PortfolioSnapshot).filter_by(portfolio_id=m.portfolio.id).count() >= 1

    # once per session: a second pass in the same window does nothing
    assert scheduler.run_pass(db)["decided"] == []


def test_retiring_liquidates_everything(db, market):
    m = _stratege(db)
    service.promote(db, m)
    service.retire(db, m)
    assert m.stage == "retired"
    assert db.query(Position).filter(Position.portfolio_id == m.portfolio.id, Position.qty > 0).count() == 0
    assert m.portfolio.cash > 9_000


def test_the_overview_reports_dollars(db, market):
    m = _stratege(db)
    service.promote(db, m)
    card = service.serialize(db, m)
    assert set(card["paper"]) >= {"equity", "pnl_usd", "today_usd", "max_drawdown_usd", "positions"}
    assert card["paper"]["equity"] == pytest.approx(10_000, rel=0.02)


def test_a_skipped_decision_is_journalled_once_not_every_minute(db, market):
    m = _stratege(db)
    service.promote(db, m)
    db.add(PaperOrder(portfolio_id=m.portfolio.id, symbol="XLE", side="buy", qty=1, status="open",
                      broker="sim", broker_order_id="sim-x", order_type="limit", limit_price=0.01))
    db.commit()
    for _ in range(5):
        runner.decide_and_execute(db, m)
    skipped = db.query(LabEvent).filter(LabEvent.variant_id == m.id, LabEvent.message.like("%décision reportée%"))
    assert skipped.count() == 1


def test_a_reconciliation_gap_is_reported_only_if_it_persists(db, market, monkeypatch):
    service.seed_defaults(db)
    answers = iter([{"ok": False, "detail": "1 écart", "differences": []},
                    {"ok": True, "detail": "ok", "differences": []},
                    {"ok": False, "detail": "1 écart", "differences": []},
                    {"ok": False, "detail": "1 écart", "differences": []}])
    monkeypatch.setattr("app.paper.venue.reconcile", lambda _db, *a: next(answers))
    scheduler.STATE.reconcile_failures = 0
    scheduler.STATE.last_reconcile_at = None
    errors = lambda: db.query(LabEvent).filter(LabEvent.message.like("Réconciliation%")).count()  # noqa: E731

    scheduler.run_pass(db)          # transient gap
    scheduler.run_pass(db)          # gone at the re-check: nothing reported
    assert errors() == 0
    scheduler.STATE.last_reconcile_at = None
    scheduler.run_pass(db)          # a gap again...
    scheduler.run_pass(db)          # ...that survives the re-check
    assert errors() == 1
