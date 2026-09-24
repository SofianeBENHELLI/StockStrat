"""Notifications, the evening summary and the account-level exposure."""
from __future__ import annotations

from datetime import datetime, timezone

import json

import pytest

from app import notify
from app.core import settings_store
from app.lab import runner, service
from app.lab.exposure import account_exposure
from app.models import LabEvent, PaperOrder, PaperPortfolio, Position, Variant
from app.monitor import scheduler


@pytest.fixture()
def outbox(monkeypatch):
    sent = []
    def capture(url, data, headers):
        sent.append({"url": url, **json.loads(data.decode())} if url.startswith("https://ntfy") else {"url": url})
    monkeypatch.setattr(notify, "_post", capture)
    return sent


def _enable(db, **extra):
    settings_store.apply_updates(db, {"notify.enabled": True, "notify.ntfy_topic": "stockstrat-test", **extra})


def _model(db, name, holdings: dict[str, tuple[float, float]], stage="paper"):
    v = Variant(name=name, engine="matheux", stage=stage, budget=10_000)
    db.add(v)
    db.commit()
    p = PaperPortfolio(variant_id=v.id, initial_cash=10_000, cash=1_000, broker="sim")
    db.add(p)
    db.commit()
    for sym, (qty, price) in holdings.items():
        db.add(Position(portfolio_id=p.id, symbol=sym, qty=qty, avg_entry_price=price, realized_pnl=0))
    db.commit()
    db.refresh(v)
    return v


# -- notifications -------------------------------------------------------------

def test_nothing_is_sent_until_notifications_are_enabled(db, outbox):
    assert notify.send(db, "t", "m") == []
    assert outbox == []


def test_a_message_goes_to_the_ntfy_topic(db, outbox):
    _enable(db)
    assert notify.send(db, "Clôture du Stratège", "Corps", priority="high") == ["ntfy"]
    msg = outbox[0]
    assert msg["url"] == "https://ntfy.sh" and msg["topic"] == "stockstrat-test"
    assert msg["title"] == "Clôture du Stratège"          # UTF-8 intact
    assert msg["message"] == "Corps" and msg["priority"] == 4


def test_each_kind_can_be_switched_off(db, outbox):
    _enable(db)                                   # fills are off by default: too chatty
    assert notify.send(db, "t", "m", kind="fill") == []
    assert notify.send(db, "t", "m", kind="exit") == ["ntfy"]


def test_a_failing_channel_never_raises(db, monkeypatch):
    _enable(db)

    def boom(*a):
        raise OSError("réseau coupé")
    monkeypatch.setattr(notify, "_post", boom)
    assert notify.send(db, "t", "m") == []


def test_journal_errors_and_exits_reach_the_phone(db, outbox):
    _enable(db)
    v = _model(db, "Alerte", {})
    runner.journal(db, v, "decision", "rien de spécial")
    runner.journal(db, v, "error", "Décision impossible : données absentes")
    assert len(outbox) == 1 and "Décision impossible" in outbox[0]["message"]


def test_topics_are_long_and_random():
    a, b = notify.new_topic(), notify.new_topic()
    assert a != b and a.startswith("stockstrat-") and len(a) >= 30


# -- exposure ------------------------------------------------------------------

def test_exposure_adds_up_the_same_sector_across_models(db):
    _model(db, "A", {"SMH": (10, 300.0)})         # 3 000 $ semis
    _model(db, "B", {"NVDA": (10, 200.0)})        # 2 000 $ semis
    _model(db, "C", {"KO": (50, 60.0)})           # 3 000 $ consommation de base
    x = account_exposure(db, prices={"SMH": 300.0, "NVDA": 200.0, "KO": 60.0})
    semis = next(s for s in x["sectors"] if s["sector"] == "Semi-conducteurs")
    assert x["total"] == 8_000
    assert semis["value"] == 5_000 and semis["models"] == 2 and semis["pct"] == 62.5
    assert [a["sector"] for a in x["alerts"]] == ["Semi-conducteurs", "Consommation de base"]


def test_pending_buys_count_in_the_exposure(db):
    v = _model(db, "A", {})
    db.add(PaperOrder(portfolio_id=v.portfolio.id, symbol="XLE", side="buy", qty=10, status="open",
                      requested_price=50.0, purpose="trade"))
    db.commit()
    x = account_exposure(db, prices={})
    assert x["symbols"][0]["pending"] == 500 and x["symbols"][0]["sector"] == "Énergie"


# -- evening summary -----------------------------------------------------------

def test_the_evening_summary_is_sent_once_after_the_close(db, outbox, monkeypatch):
    _enable(db)
    v = _model(db, "Résumé", {"SMH": (10, 300.0)})
    monkeypatch.setattr("app.lab.data.latest_prices", lambda symbols: {"SMH": 310.0})
    after_close = datetime(2026, 9, 25, 20, 30, tzinfo=timezone.utc)      # 16:30 New York, a Friday
    v.last_decision_on = "2026-09-25"
    db.commit()

    scheduler._daily_summary(db, {"is_open": False}, now=after_close)
    scheduler._daily_summary(db, {"is_open": False}, now=after_close)
    summaries = [s for s in outbox if "clôture" in s["title"]]
    assert len(summaries) == 1
    assert "Résumé" in summaries[0]["message"] and "Concentration : Semi-conducteurs" in summaries[0]["message"]
    assert db.query(LabEvent).filter(LabEvent.kind == "summary").count() == 1


def test_no_summary_before_the_close_or_when_nothing_ran(db, outbox):
    _enable(db)
    v = _model(db, "Tôt", {})
    v.last_decision_on = "2026-09-25"
    db.commit()
    scheduler._daily_summary(db, {"is_open": False}, now=datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc))
    v.last_decision_on = "2026-09-24"
    db.commit()
    scheduler._daily_summary(db, {"is_open": False}, now=datetime(2026, 9, 25, 21, 0, tzinfo=timezone.utc))
    assert outbox == []
