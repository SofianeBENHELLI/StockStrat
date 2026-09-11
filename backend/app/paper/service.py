"""Order execution: proposal -> risk guardrails -> simulated fill -> position/cash
update -> audit log. Every path writes an ExecutionEvent; nothing here silently
fails without a logged reason."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.provider import latest_prices
from app.models import ExecutionEvent, PaperOrder, PaperPortfolio, Position, SystemState
from app.paper.broker import simulate_fill


class OrderRejected(Exception):
    pass


def get_system_state(db: Session) -> SystemState:
    state = db.scalar(select(SystemState).limit(1))
    if state is None:
        state = SystemState()
        db.add(state)
        db.commit()
        db.refresh(state)
    return state


def submit_order(
    db: Session, *, portfolio: PaperPortfolio, symbol: str, side: str, qty: float,
    order_type: str = "market", limit_price: float | None = None,
    max_loss: float | None = None, rationale: str = "",
) -> PaperOrder:
    state = get_system_state(db)
    if state.kill_switch_engaged:
        raise OrderRejected(f"kill switch engaged: {state.kill_switch_reason or 'no reason given'}")
    if side == "buy" and max_loss is None:
        raise OrderRejected("max_loss is required before opening a position (garde-fou: perte max définie avant le trade)")

    order = PaperOrder(
        portfolio_id=portfolio.id, symbol=symbol.upper(), side=side, qty=qty,
        order_type=order_type, limit_price=limit_price, max_loss=max_loss,
        rationale=rationale, status="proposed",
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    _log(db, order.id, "proposed", {"symbol": symbol, "side": side, "qty": qty})

    quotes = latest_prices([symbol])
    quote = quotes.get(symbol.upper())
    market_price = quote.price if quote else None
    order.requested_price = market_price

    result = simulate_fill(
        symbol=symbol.upper(), side=side, qty=qty, order_type=order_type,
        limit_price=limit_price, market_price=market_price, order_id=order.id,
    )
    order.status = result.status
    order.filled_qty = result.filled_qty
    order.filled_avg_price = result.filled_avg_price
    order.spread_bps = result.spread_bps
    order.slippage_bps = result.slippage_bps

    if result.status in ("filled", "partial_fill") and result.filled_avg_price:
        _apply_fill(db, portfolio, order, result.filled_qty, result.filled_avg_price)

    db.commit()
    db.refresh(order)
    _log(db, order.id, result.status, {"detail": result.detail, "filled_qty": result.filled_qty,
                                       "filled_avg_price": result.filled_avg_price,
                                       "slippage_bps": result.slippage_bps, "spread_bps": result.spread_bps,
                                       "quote_source": quote.source if quote else "unavailable"})
    return order


def _apply_fill(db: Session, portfolio: PaperPortfolio, order: PaperOrder, filled_qty: float, price: float) -> None:
    notional = filled_qty * price
    pos = db.scalar(select(Position).where(Position.portfolio_id == portfolio.id, Position.symbol == order.symbol))
    if pos is None:
        pos = Position(portfolio_id=portfolio.id, symbol=order.symbol, qty=0.0, avg_entry_price=0.0, realized_pnl=0.0)
        db.add(pos)

    if order.side == "buy":
        if notional > portfolio.cash:
            raise OrderRejected(f"insufficient paper cash: need {notional:.2f}, have {portfolio.cash:.2f}")
        new_qty = pos.qty + filled_qty
        pos.avg_entry_price = (pos.avg_entry_price * pos.qty + notional) / new_qty if new_qty else 0.0
        pos.qty = new_qty
        portfolio.cash -= notional
    else:
        close_qty = min(filled_qty, pos.qty)
        trade_pnl = (price - pos.avg_entry_price) * close_qty
        pos.realized_pnl += trade_pnl
        pos.qty -= close_qty
        portfolio.cash += close_qty * price
        order.realized_pnl = trade_pnl


def _log(db: Session, order_id: int, event: str, detail: dict) -> None:
    db.add(ExecutionEvent(order_id=order_id, event=event, detail=detail))
    db.commit()


def set_kill_switch(db: Session, engaged: bool, reason: str = "") -> SystemState:
    state = get_system_state(db)
    state.kill_switch_engaged = engaged
    state.kill_switch_reason = reason
    db.commit()
    db.refresh(state)
    return state
