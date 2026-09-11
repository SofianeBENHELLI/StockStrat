"""Settlement: the one place an order becomes a position, cash movement and a
realised P&L number.

Extracted out of app/paper/service.py because there is now more than one way an
order can reach this point — an immediate fill at submit time, or the poller
settling an order that was resting. Two copies of weighted-average-entry and
realised-P&L arithmetic would drift, and a drift here is invisible until the
leaderboard is already wrong.

Idempotent by design: settling the same order twice is a real possibility once
a background loop and an HTTP request can both touch the same row, and the
failure mode (a position counted twice) is silent. `apply_fill` therefore
refuses to act on an order that already reached a terminal status and says so
via its return value rather than raising.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExecutionEvent, PaperOrder, PaperPortfolio, Position, utcnow

# Statuses from which no further settlement is possible.
TERMINAL = ("filled", "partial_fill", "cancelled", "rejected")


def log_event(db: Session, order_id: int | None, event: str, detail: dict) -> None:
    db.add(ExecutionEvent(order_id=order_id, event=event, detail=detail))
    db.commit()


def apply_fill(
    db: Session, *, portfolio: PaperPortfolio, order: PaperOrder,
    filled_qty: float, price: float, status: str = "filled",
) -> bool:
    """Settle `order` at `price`. Returns False if it was already settled.

    Cash is allowed to go negative here rather than refusing the fill. That
    looks wrong at first glance, and is deliberate: by this point the trade has
    *already happened* — the simulator has priced it, and a real venue would
    have executed it. Refusing settlement after execution would leave the book
    disagreeing with reality, which is the worse failure. Affordability is
    enforced before the broker is called (see service.submit_order); an
    overdraft reaching here means that check was wrong, so it is recorded as
    its own event instead of being hidden.
    """
    if order.status in TERMINAL:
        return False

    notional = filled_qty * price
    pos = db.scalar(select(Position).where(
        Position.portfolio_id == portfolio.id, Position.symbol == order.symbol))
    if pos is None:
        pos = Position(portfolio_id=portfolio.id, symbol=order.symbol,
                       qty=0.0, avg_entry_price=0.0, realized_pnl=0.0)
        db.add(pos)

    if order.side == "buy":
        # Re-opening a symbol that had been closed out restarts its clock. The
        # Position row is reused rather than recreated, so without this the
        # holding-period exit would measure from the *first* time this symbol
        # was ever bought and close a brand-new position on day one.
        if pos.qty == 0:
            pos.opened_at = utcnow()
        if notional > portfolio.cash:
            log_event(db, order.id, "cash_overdraft", {
                "needed": round(notional, 2), "available": round(portfolio.cash, 2),
                "note": "fill settled anyway — the pre-trade affordability check should have caught this",
            })
        new_qty = pos.qty + filled_qty
        pos.avg_entry_price = (pos.avg_entry_price * pos.qty + notional) / new_qty if new_qty else 0.0
        pos.qty = new_qty
        portfolio.cash -= notional
    else:
        close_qty = min(filled_qty, pos.qty)
        trade_pnl = (price - pos.avg_entry_price) * close_qty
        pos.realized_pnl += trade_pnl
        pos.qty -= close_qty
        # Kill float dust so a fully-closed position reads as exactly flat and
        # stops being valued as an open holding.
        if abs(pos.qty) < 1e-9:
            pos.qty = 0.0
        portfolio.cash += close_qty * price
        order.realized_pnl = trade_pnl

    order.status = status
    order.filled_qty = filled_qty
    order.filled_avg_price = price
    db.commit()
    return True
