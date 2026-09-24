"""End-to-end check against the real Alpaca paper account, with one share.

Runs the production code path — pre-trade checks, broker, polling,
settlement, journal, reconciliation — on a dedicated test ledger, so it can
be run at any time without touching the models' books:

    buy 1 share -> wait for the fill -> reconcile -> sell it -> wait -> reconcile

During the regular session it uses marketable limit orders; outside it, the
same orders flagged for extended hours (Alpaca's pre/after-market sessions),
so it also works in the evening. The test ledger is kept out of every screen
(stage "archived") but IS included in reconciliation, which is the point.

    cd backend && .venv/bin/python scripts/smoke_e2e.py [SYMBOL]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.core.db import SessionLocal, init_db  # noqa: E402
from app.lab import data as market  # noqa: E402
from app.lab.runner import journal, market_clock  # noqa: E402
from app.models import PaperPortfolio, Position, Variant  # noqa: E402
from app.paper import venue  # noqa: E402
from app.paper.service import open_orders, poll_open_orders, submit_order  # noqa: E402

NAME = "Test de bout en bout (1 action)"
BUDGET = 150.0


def ledger(db):
    v = db.scalar(select(Variant).where(Variant.name == NAME))
    if v is None:
        v = Variant(name=NAME, engine="stratege", stage="archived", budget=BUDGET,
                    description="Portefeuille technique du test de bout en bout (scripts/smoke_e2e.py).")
        db.add(v)
        db.commit()
        db.add(PaperPortfolio(variant_id=v.id, initial_cash=BUDGET, cash=BUDGET, broker="alpaca_paper"))
        db.commit()
        db.refresh(v)
    return v


def wait_fill(db, v, order, timeout=120) -> None:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        poll_open_orders(db, v.portfolio)
        db.refresh(order)
        if order.status != "open":
            return
        time.sleep(3)


def step(label, ok, detail=""):
    print(f"  {'OK ' if ok else 'ÉCHEC'}  {label}{' — ' + detail if detail else ''}")
    return ok


def main(symbol: str = "F") -> int:
    init_db()
    db = SessionLocal()
    clock = market_clock(db)
    extended = not clock.get("is_open")
    v = ledger(db)
    print(f"Test de bout en bout sur {symbol} — {'séance prolongée' if extended else 'séance régulière'}")

    price = market.latest_prices([symbol]).get(symbol)
    if not step("prix Alpaca en direct", price is not None, f"{price} $"):
        return 1

    buy = submit_order(db, portfolio=v.portfolio, symbol=symbol, side="buy", qty=1, order_type="limit",
                       limit_price=price * 1.004, max_loss=price, rationale="test de bout en bout",
                       extended_hours=extended)
    step("ordre d'achat accepté par Alpaca", buy.broker_order_id is not None and buy.status != "rejected",
         f"{buy.status}, {buy.broker_order_id}")
    wait_fill(db, v, buy)
    if not step("achat exécuté et réglé dans la sous-comptabilité", buy.status == "filled",
                f"{buy.filled_qty} @ {buy.filled_avg_price}"):
        return 1
    journal(db, v, "fill", f"Test : achat {symbol} exécuté @ {buy.filled_avg_price}")

    rec = venue.reconcile(db)
    step("réconciliation après achat", rec["ok"], rec["detail"])

    held = db.scalar(select(Position.qty).where(Position.portfolio_id == v.portfolio.id, Position.symbol == symbol))
    sell_price = (market.latest_prices([symbol]).get(symbol) or price) * 0.996
    sell = submit_order(db, portfolio=v.portfolio, symbol=symbol, side="sell", qty=held, order_type="limit",
                        limit_price=sell_price, rationale="test de bout en bout — revente",
                        extended_hours=extended)
    wait_fill(db, v, sell)
    if not step("vente exécutée, P&L réalisé", sell.status == "filled",
                f"{sell.filled_qty} @ {sell.filled_avg_price}, P&L {sell.realized_pnl:+.4f} $" if sell.realized_pnl is not None else sell.status):
        return 1
    journal(db, v, "fill", f"Test : revente {symbol} @ {sell.filled_avg_price}, P&L {sell.realized_pnl:+.2f} $")

    rec = venue.reconcile(db)
    step("réconciliation après revente", rec["ok"], rec["detail"])
    db.refresh(v.portfolio)
    step("aucun ordre resté ouvert", not open_orders(db, v.portfolio))
    print(f"  cash du portefeuille de test : {v.portfolio.cash:.2f} $ (départ {BUDGET:.2f} $)")
    return 0 if rec["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(*(sys.argv[1:2] or [])))
