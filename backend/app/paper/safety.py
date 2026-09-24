"""The standing safety stop: a catastrophe floor kept at the broker.

The models' own stops are evaluated once a day, on the close, by this app —
identical to the backtest, which is the point. But they only exist while the
app is running. If the machine is off during a crash, nothing sells.

So every position held at Alpaca also carries a resting stop order at the
broker, well below the models' own stops (25% under the entry price by
default, Administration → Exécution). It is not part of any strategy and in
normal times never fires; it is there for the day the app is down.

Two constraints shape it:

- **Whole shares only.** Alpaca rests a good-till-cancelled stop only on
  whole shares; fractional quantities accept day orders only, which would need
  re-posting every morning — by an app that, in the scenario this exists for,
  is not running. So the stop covers the whole-share part of each position and
  the fractional remainder stays under the models' close-based stops.
- **One account, many models.** The stop belongs to one model's sub-ledger and
  covers exactly that model's whole shares. Before that model sells the
  symbol, its stop is withdrawn — and the withdrawal confirmed at the broker,
  since shares reserved by a resting stop cannot be sold — and after the sale
  it is re-posted on what remains.
"""
from __future__ import annotations

import math
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import settings_store
from app.models import PaperOrder, PaperPortfolio, Position

VENUE_BROKERS = ("alpaca_paper",)
CANCEL_CONFIRM_SECONDS = 10


def _pct(db: Session) -> float:
    return float(settings_store.resolve(db, "execution.safety_stop_pct") or 0)


def _stop(db: Session, portfolio: PaperPortfolio, symbol: str) -> PaperOrder | None:
    return db.scalar(select(PaperOrder).where(
        PaperOrder.portfolio_id == portfolio.id, PaperOrder.symbol == symbol,
        PaperOrder.purpose == "safety_stop", PaperOrder.status == "open"))


def release(db: Session, portfolio: PaperPortfolio, symbol: str) -> None:
    """Withdraw this model's safety stop on `symbol` and wait until the broker
    confirms, so a sell that follows is not refused for reserved shares. If the
    stop fired in the meantime, it is settled like any fill."""
    from app.paper.service import OrderSpec, _resolve, broker_for  # local: circular import

    stop = _stop(db, portfolio, symbol)
    if stop is None:
        return
    broker = broker_for(db, stop.broker)
    broker.cancel(stop.broker_order_id or "")
    spec = OrderSpec(order_id=stop.id, symbol=stop.symbol, side=stop.side, qty=stop.qty,
                     order_type="stop", stop_price=stop.stop_price, time_in_force=stop.time_in_force or "gtc")
    deadline = time.monotonic() + CANCEL_CONFIRM_SECONDS
    while True:
        result = broker.poll(stop.broker_order_id or "", spec, None)
        # The simulator is stateless: it cannot report a cancellation, and has
        # no shares reserved to wait for. Only a real venue is worth waiting on.
        if result.status != "open" or broker.name == "sim":
            break
        if time.monotonic() > deadline:
            break
        time.sleep(0.5)
    if result.status in ("filled", "partial_fill"):
        _resolve(db, portfolio, stop, result, quote_source="broker")
    else:
        stop.status = "cancelled"
        db.commit()


def ensure(db: Session, portfolio: PaperPortfolio, symbol: str) -> PaperOrder | None:
    """Make the standing stop match what this model now holds: whole shares,
    `pct` below the average entry price. Re-posted only when it differs."""
    from app.paper.service import OrderRejected, submit_order  # local: circular import

    pct = _pct(db)
    if portfolio.broker not in VENUE_BROKERS or pct <= 0:
        return None
    pos = db.scalar(select(Position).where(Position.portfolio_id == portfolio.id, Position.symbol == symbol))
    whole = math.floor(pos.qty + 1e-9) if pos is not None else 0
    target_price = round(pos.avg_entry_price * (1 - pct / 100), 2) if whole >= 1 else None
    current = _stop(db, portfolio, symbol)
    if current is not None and whole >= 1 and current.qty == whole and \
            abs((current.stop_price or 0) - target_price) < 0.011:
        return current
    if current is not None:
        release(db, portfolio, symbol)
    if whole < 1:
        return None
    try:
        return submit_order(db, portfolio=portfolio, symbol=symbol, side="sell", qty=whole,
                            order_type="stop", stop_price=target_price, time_in_force="gtc",
                            purpose="safety_stop", exit_reason="stop de secours",
                            rationale=f"stop de secours à {pct:g} % sous le prix d'achat, tenu chez le broker")
    except OrderRejected:
        return None


def after_fill(db: Session, portfolio: PaperPortfolio, order: PaperOrder) -> None:
    """Called once per settled fill. A fill of the safety stop itself needs no
    new stop — the position it protected is gone or down to a fraction."""
    if order.purpose == "safety_stop":
        return
    ensure(db, portfolio, order.symbol)
