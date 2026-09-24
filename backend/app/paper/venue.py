"""Operations against an external venue: connection checks and reconciliation.

Kept out of app/paper/service.py because none of it is on the order path — it
is what an operator runs to answer "is this connected?" and "does the venue
agree with our book?".

Reconciliation has no equivalent in the simulator and is the thing most easily
skipped when wiring a venue: the moment orders execute somewhere else, the
database stops being the source of truth and becomes a *claim* about it. An
unreconciled book is one that looks right on every dashboard while being wrong.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PaperPortfolio, Position, Variant
from app.paper.brokers import BrokerUnavailable
from app.paper.service import broker_for

VENUE_BROKERS = ("alpaca_paper",)


def test_connection(db: Session, broker_name: str = "alpaca_paper") -> dict:
    """Prove the credentials work, without ever echoing them.

    Returns a verdict rather than raising, because this is called by a button
    in the Administration panel and "the key is wrong" is an expected answer,
    not an error condition.
    """
    try:
        broker = broker_for(db, broker_name)
    except BrokerUnavailable as exc:
        return {"ok": False, "broker": broker_name, "stage": "credentials", "detail": str(exc)}

    try:
        account = broker.account()
    except Exception as exc:
        return {"ok": False, "broker": broker_name, "stage": "account",
                "detail": f"credentials were accepted by the client but the account call failed: {exc}"}

    try:
        clock = broker.clock()
    except Exception as exc:
        clock = {"error": str(exc)}

    return {
        "ok": True, "broker": broker_name, "stage": "connected",
        "account": account, "clock": clock,
        "detail": f"Connecté au compte paper {account.get('account_number') or '(sans numéro)'}.",
    }


def _venue_portfolios(db: Session) -> list[tuple[Variant, PaperPortfolio]]:
    rows = []
    for portfolio in db.scalars(select(PaperPortfolio).where(PaperPortfolio.broker.in_(VENUE_BROKERS))):
        variant = db.get(Variant, portfolio.variant_id)
        if variant is not None:
            rows.append((variant, portfolio))
    return rows


def reconcile(db: Session, broker_name: str = "alpaca_paper") -> dict:
    """Compare what we believe we hold against what the venue says we hold.

    Every model has its own sub-ledger inside the one shared Alpaca account.
    The broker only sees one pooled position per symbol, so the check is on
    the sum: all sub-ledgers together must hold exactly what Alpaca holds. Any
    difference means an order was filled, cancelled or partially executed
    without the ledger learning of it.
    """
    pairs = _venue_portfolios(db)
    if not pairs:
        return {"ok": True, "broker": broker_name, "portfolios": 0,
                "detail": "Aucune variante n'est routée vers ce venue.", "differences": []}

    try:
        broker = broker_for(db, broker_name)
        venue_positions = broker.positions()
    except BrokerUnavailable as exc:
        return {"ok": False, "broker": broker_name, "portfolios": len(pairs), "detail": str(exc),
                "differences": []}
    except Exception as exc:
        return {"ok": False, "broker": broker_name, "portfolios": len(pairs),
                "detail": f"impossible de lire les positions du venue: {exc}", "differences": []}

    local: dict[str, float] = {}
    for _variant, portfolio in pairs:
        for pos in db.scalars(select(Position).where(
                Position.portfolio_id == portfolio.id, Position.qty > 0)):
            local[pos.symbol] = local.get(pos.symbol, 0.0) + pos.qty

    differences = []
    for symbol in sorted(set(local) | set(venue_positions)):
        ours, theirs = local.get(symbol, 0.0), venue_positions.get(symbol, 0.0)
        if abs(ours - theirs) > 1e-6:
            differences.append({
                "symbol": symbol, "ours": round(ours, 6), "venue": round(theirs, 6),
                "delta": round(theirs - ours, 6),
            })

    return {
        "ok": not differences, "broker": broker_name, "portfolios": len(pairs),
        "netted": len(pairs) > 1,
        "detail": ("Le livre local et le venue concordent." if not differences
                   else f"{len(differences)} écart(s) entre le livre local et le venue."),
        "differences": differences,
    }

