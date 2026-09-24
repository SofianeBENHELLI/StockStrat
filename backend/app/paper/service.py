"""Order execution: proposal -> pre-trade guardrails -> broker -> settlement ->
audit log. Every path writes an ExecutionEvent; nothing here fails silently.

This module is the only one allowed to import app/paper/brokers.py. Keeping the
broker import in one file is what makes "an order cannot reach a venue without
passing the guardrails" a property you can verify by reading a single function
instead of auditing every call site.

Order of operations matters and is the thing most easily got wrong: every
affordability and sizing check happens **before** `broker.submit`. The previous
version checked cash while applying the fill, i.e. after execution — harmless
against a simulator that can be told to forget, but against a real venue it
means the trade happened and the book refused to record it.
"""
from __future__ import annotations

import math

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import settings_store
from app.data.provider import latest_prices
from app.models import PaperOrder, PaperPortfolio, Position, SystemState
from app.paper import settlement
from app.paper.brokers import BrokerProtocol, Credentials, OrderSpec, get_broker

# Headroom on the pre-trade cash check. A fill is priced at the far side of the
# modelled spread (up to 40bps half-spread) plus slippage (up to ~25bps), so the
# cash actually needed exceeds qty x last price. Checking against the bare
# notional would let an order pass the check and then overdraw at settlement.
COST_BUFFER = 1.01

OPEN_STATUSES = ("open",)


class OrderRejected(Exception):
    """Refused before reaching the broker. The message is shown to the user."""


def broker_for(db: Session, name: str) -> BrokerProtocol:
    """Resolve a broker together with its credentials.

    Credentials are read here rather than inside the broker so that
    app/paper/brokers.py stays free of any database dependency and remains
    testable with nothing but a pair of strings."""
    credentials = Credentials(
        api_key=settings_store.resolve(db, "connections.alpaca_api_key"),
        secret_key=settings_store.resolve(db, "connections.alpaca_secret_key"),
    )
    return get_broker(name, credentials)


def get_system_state(db: Session) -> SystemState:
    state = db.scalar(select(SystemState).limit(1))
    if state is None:
        state = SystemState()
        db.add(state)
        db.commit()
        db.refresh(state)
    return state


def _held_qty(db: Session, portfolio: PaperPortfolio, symbol: str) -> float:
    pos = db.scalar(select(Position).where(
        Position.portfolio_id == portfolio.id, Position.symbol == symbol))
    return pos.qty if pos else 0.0


def _preflight(
    db: Session, *, portfolio: PaperPortfolio, symbol: str, side: str, qty: float,
    max_loss: float | None, market_price: float | None,
) -> None:
    """Every reason to refuse an order, evaluated before anything executes."""
    state = get_system_state(db)
    if state.kill_switch_engaged:
        raise OrderRejected(f"kill switch engaged: {state.kill_switch_reason or 'no reason given'}")
    if qty <= 0:
        raise OrderRejected("quantity must be positive")
    if side == "buy" and max_loss is None:
        raise OrderRejected(
            "max_loss is required before opening a position (garde-fou: perte max définie avant le trade)")
    if market_price is None or market_price <= 0:
        raise OrderRejected(f"no market price available for {symbol}")

    notional = qty * market_price
    cap = float(settings_store.resolve(db, "execution.max_order_notional"))
    if notional > cap:
        raise OrderRejected(
            f"order notional {notional:,.2f} exceeds the {cap:,.2f} per-order cap "
            f"(Administration → Execution)")

    if side == "buy":
        needed = notional * COST_BUFFER
        if needed > portfolio.cash:
            raise OrderRejected(
                f"insufficient paper cash: need ~{needed:,.2f} including spread and slippage, "
                f"have {portfolio.cash:,.2f}")
    else:
        held = _held_qty(db, portfolio, symbol)
        if qty > held:
            raise OrderRejected(f"cannot sell {qty:g} {symbol}: only {held:g} held")


def submit_order(
    db: Session, *, portfolio: PaperPortfolio, symbol: str, side: str, qty: float,
    order_type: str = "market", limit_price: float | None = None,
    max_loss: float | None = None, rationale: str = "", exit_reason: str | None = None,
    extended_hours: bool = False,
) -> PaperOrder:
    symbol = symbol.upper()
    quotes = latest_prices([symbol])
    quote = quotes.get(symbol)
    market_price = quote.price if quote else None

    _preflight(db, portfolio=portfolio, symbol=symbol, side=side, qty=qty,
               max_loss=max_loss, market_price=market_price)

    broker = broker_for(db, portfolio.broker)
    order = PaperOrder(
        portfolio_id=portfolio.id, symbol=symbol, side=side, qty=qty,
        order_type=order_type, limit_price=limit_price, max_loss=max_loss,
        rationale=rationale, exit_reason=exit_reason, status="proposed",
        broker=broker.name, requested_price=market_price,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    settlement.log_event(db, order.id, "proposed", {
        "symbol": symbol, "side": side, "qty": qty, "broker": broker.name,
        "exit_reason": exit_reason,
    })

    spec = OrderSpec(order_id=order.id, symbol=symbol, side=side, qty=qty,
                     order_type=order_type, limit_price=limit_price,
                     client_order_id=f"ss-m{portfolio.variant_id}-o{order.id}",
                     extended_hours=extended_hours)
    result = broker.submit(spec, market_price)

    order.broker_order_id = result.broker_order_id
    order.spread_bps = result.spread_bps
    order.slippage_bps = result.slippage_bps
    _resolve(db, portfolio, order, result, quote_source=quote.source if quote else "unavailable")
    db.refresh(order)
    return order


def _resolve(db: Session, portfolio: PaperPortfolio, order: PaperOrder, result, quote_source: str) -> None:
    """Turn a broker answer into database state, once."""
    if result.status in ("filled", "partial_fill") and result.filled_avg_price:
        settlement.apply_fill(db, portfolio=portfolio, order=order,
                              filled_qty=result.filled_qty, price=result.filled_avg_price,
                              status=result.status)
    else:
        order.status = result.status
        db.commit()

    settlement.log_event(db, order.id, result.status, {
        "detail": result.detail, "filled_qty": result.filled_qty,
        "filled_avg_price": result.filled_avg_price, "slippage_bps": result.slippage_bps,
        "spread_bps": result.spread_bps, "quote_source": quote_source,
        "broker_order_id": result.broker_order_id,
    })


def open_orders(db: Session, portfolio: PaperPortfolio) -> list[PaperOrder]:
    return list(db.scalars(select(PaperOrder).where(
        PaperOrder.portfolio_id == portfolio.id, PaperOrder.status.in_(OPEN_STATUSES))))


def poll_open_orders(db: Session, portfolio: PaperPortfolio) -> list[PaperOrder]:
    """Re-check every resting order against a fresh price. Called by the monitor
    loop; also safe to call by hand. Returns the orders whose status changed."""
    resting = open_orders(db, portfolio)
    if not resting:
        return []

    quotes = latest_prices(sorted({o.symbol for o in resting}))
    changed = []
    for order in resting:
        quote = quotes.get(order.symbol)
        market_price = quote.price if quote else None
        broker = broker_for(db, order.broker)
        spec = OrderSpec(order_id=order.id, symbol=order.symbol, side=order.side, qty=order.qty,
                         order_type=order.order_type, limit_price=order.limit_price)
        result = broker.poll(order.broker_order_id or "", spec, market_price)
        if result.status in OPEN_STATUSES:
            continue
        order.spread_bps = result.spread_bps
        order.slippage_bps = result.slippage_bps
        _resolve(db, portfolio, order, result, quote_source=quote.source if quote else "unavailable")
        changed.append(order)
    return changed


def cancel_order(db: Session, order: PaperOrder) -> PaperOrder:
    if order.status not in OPEN_STATUSES:
        raise OrderRejected(f"order {order.id} is {order.status}, only an open order can be cancelled")
    broker_for(db, order.broker).cancel(order.broker_order_id or "")
    order.status = "cancelled"
    db.commit()
    settlement.log_event(db, order.id, "cancelled", {"by": "user"})
    db.refresh(order)
    return order


def set_kill_switch(db: Session, engaged: bool, reason: str = "") -> SystemState:
    state = get_system_state(db)
    state.kill_switch_engaged = engaged
    state.kill_switch_reason = reason
    db.commit()
    db.refresh(state)
    return state


def affordable_qty(portfolio: PaperPortfolio, budget: float, price: float) -> int:
    """Whole shares that `budget` buys at `price`, leaving room for the spread
    and slippage the pre-trade check will insist on."""
    if price <= 0:
        return 0
    usable = min(budget, portfolio.cash) / COST_BUFFER
    return max(int(math.floor(usable / price)), 0)
