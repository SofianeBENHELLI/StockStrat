from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.data.provider import latest_prices
from app.models import DecisionLog, PaperOrder, PaperPortfolio, Position, Variant
from app.paper import accounting, exits, venue
from app.paper.brokers import BrokerUnavailable
from app.paper.service import OrderRejected, cancel_order, poll_open_orders, submit_order

router = APIRouter(prefix="/api/variants", tags=["variants"])


class CreateVariant(BaseModel):
    name: str
    engine: str = "manual"
    description: str = ""
    initial_cash: float = 100_000.0


class VariantOut(BaseModel):
    id: int
    name: str
    engine: str
    description: str
    status: str
    portfolio_id: int


class OrderIn(BaseModel):
    symbol: str
    side: str = Field(pattern="^(buy|sell)$")
    qty: float = Field(gt=0)
    order_type: str = Field(default="market", pattern="^(market|limit)$")
    limit_price: float | None = None
    max_loss: float | None = None
    rationale: str = ""


@router.post("", response_model=VariantOut)
def create_variant(body: CreateVariant, db: Session = Depends(get_db)) -> VariantOut:
    variant = Variant(name=body.name, engine=body.engine, description=body.description)
    db.add(variant)
    db.commit()
    db.refresh(variant)
    portfolio = PaperPortfolio(variant_id=variant.id, initial_cash=body.initial_cash, cash=body.initial_cash)
    db.add(portfolio)
    db.commit()
    db.refresh(portfolio)
    return VariantOut(id=variant.id, name=variant.name, engine=variant.engine,
                      description=variant.description, status=variant.status, portfolio_id=portfolio.id)


@router.get("", response_model=list[VariantOut])
def list_variants(db: Session = Depends(get_db)) -> list[VariantOut]:
    out = []
    for v in db.scalars(select(Variant)):
        if v.portfolio is None:
            continue
        out.append(VariantOut(id=v.id, name=v.name, engine=v.engine, description=v.description,
                              status=v.status, portfolio_id=v.portfolio.id))
    return out


@router.get("/{variant_id}/summary")
def variant_summary(variant_id: int, db: Session = Depends(get_db)) -> dict:
    variant, portfolio = _get_variant_portfolio(db, variant_id)
    symbols = list(db.scalars(select(PaperOrder.symbol).where(PaperOrder.portfolio_id == portfolio.id).distinct()))
    prices = {s: q.price for s, q in latest_prices(symbols).items()} if symbols else {}
    s = accounting.summary(db, portfolio, prices)
    s["variant"] = {"id": variant.id, "name": variant.name, "engine": variant.engine, "status": variant.status}
    return s


@router.get("/{variant_id}/equity-history")
def variant_equity_history(variant_id: int, db: Session = Depends(get_db)) -> dict:
    _, portfolio = _get_variant_portfolio(db, variant_id)
    return accounting.equity_history(db, portfolio)


@router.get("/{variant_id}/orders")
def variant_orders(variant_id: int, db: Session = Depends(get_db)) -> list[dict]:
    _, portfolio = _get_variant_portfolio(db, variant_id)
    orders = db.scalars(select(PaperOrder).where(PaperOrder.portfolio_id == portfolio.id)
                        .order_by(PaperOrder.created_at.desc())).all()
    return [{
        "id": o.id, "symbol": o.symbol, "side": o.side, "qty": o.qty, "order_type": o.order_type,
        "status": o.status, "filled_qty": o.filled_qty, "filled_avg_price": o.filled_avg_price,
        "requested_price": o.requested_price, "slippage_bps": o.slippage_bps, "spread_bps": o.spread_bps,
        "max_loss": o.max_loss, "rationale": o.rationale, "created_at": o.created_at.isoformat(),
        "broker": o.broker, "broker_order_id": o.broker_order_id, "exit_reason": o.exit_reason,
        "realized_pnl": o.realized_pnl,
    } for o in orders]


@router.post("/{variant_id}/orders")
def place_order(variant_id: int, body: OrderIn, db: Session = Depends(get_db)) -> dict:
    _, portfolio = _get_variant_portfolio(db, variant_id)
    try:
        order = submit_order(
            db, portfolio=portfolio, symbol=body.symbol, side=body.side, qty=body.qty,
            order_type=body.order_type, limit_price=body.limit_price,
            max_loss=body.max_loss, rationale=body.rationale,
        )
    except OrderRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "id": order.id, "status": order.status, "filled_qty": order.filled_qty,
        "filled_avg_price": order.filled_avg_price, "slippage_bps": order.slippage_bps,
        "spread_bps": order.spread_bps,
    }


@router.post("/{variant_id}/orders/{order_id}/cancel")
def cancel(variant_id: int, order_id: int, db: Session = Depends(get_db)) -> dict:
    _, portfolio = _get_variant_portfolio(db, variant_id)
    order = db.get(PaperOrder, order_id)
    if order is None or order.portfolio_id != portfolio.id:
        raise HTTPException(status_code=404, detail="order not found for this variant")
    try:
        order = cancel_order(db, order)
    except OrderRejected as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": order.id, "status": order.status}


@router.post("/{variant_id}/poll")
def poll(variant_id: int, db: Session = Depends(get_db)) -> dict:
    """Re-check this variant's resting orders against fresh prices."""
    _, portfolio = _get_variant_portfolio(db, variant_id)
    changed = poll_open_orders(db, portfolio)
    return {"resolved": [{"id": o.id, "symbol": o.symbol, "status": o.status} for o in changed]}


@router.get("/{variant_id}/exits")
def exit_preview(variant_id: int, db: Session = Depends(get_db)) -> dict:
    """What the exit rules would close right now, without closing anything.
    Lets the Administration panel's thresholds be judged before they fire."""
    _, portfolio = _get_variant_portfolio(db, variant_id)
    held = list(db.scalars(select(Position.symbol).where(
        Position.portfolio_id == portfolio.id, Position.qty > 0)))
    prices = {s: q.price for s, q in latest_prices(held).items()} if held else {}
    rules = exits.ExitRules.from_settings(db)
    return {
        "rules": {
            "enabled": rules.enabled, "stop_loss_pct": rules.stop_loss_pct,
            "take_profit_pct": rules.take_profit_pct, "max_holding_days": rules.max_holding_days,
        },
        "would_exit": [
            {"symbol": d.symbol, "qty": d.qty, "reason": d.reason, "detail": d.detail}
            for d in exits.evaluate(db, portfolio, prices, rules=rules)
        ],
    }


class BrokerAssignment(BaseModel):
    broker: str
    confirm: str = ""
    force: bool = False


@router.post("/{variant_id}/broker")
def set_variant_broker(variant_id: int, body: BrokerAssignment, db: Session = Depends(get_db)) -> dict:
    """Point one variant at a broker.

    Moving a variant ONTO an external venue requires the literal string
    "CONFIRM". Not security theatre: every other control in this app is
    reversible bookkeeping, while this one is the single action that makes
    orders leave the machine. A typed confirmation is the cheapest way to make
    that boundary impossible to cross by a mis-click.
    """
    variant, _ = _get_variant_portfolio(db, variant_id)
    if body.broker in venue.VENUE_BROKERS and body.confirm != "CONFIRM":
        raise HTTPException(
            status_code=428,
            detail=f"envoyer confirm=\"CONFIRM\" pour router « {variant.name} » vers {body.broker}",
        )
    try:
        portfolio = venue.promote(db, variant, body.broker, force=body.force)
    except BrokerUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"variant_id": variant.id, "name": variant.name, "broker": portfolio.broker}


@router.get("/{variant_id}/decisions")
def variant_decisions(variant_id: int, db: Session = Depends(get_db)) -> list[dict]:
    _get_variant_portfolio(db, variant_id)
    logs = db.scalars(select(DecisionLog).where(DecisionLog.variant_id == variant_id)
                      .order_by(DecisionLog.created_at.desc())).all()
    return [{
        "id": d.id, "order_id": d.order_id, "symbol": d.symbol, "action": d.action,
        "structure": d.structure, "engine": d.engine, "confidence": d.confidence, "score": d.score,
        "rationale": d.rationale, "invalidation": d.invalidation, "main_risk": d.main_risk,
        "factors": d.factors, "explanation_source": d.explanation_source,
        "created_at": d.created_at.isoformat(),
    } for d in logs]


@router.post("/{variant_id}/snapshot")
def snapshot(variant_id: int, db: Session = Depends(get_db)) -> dict:
    _, portfolio = _get_variant_portfolio(db, variant_id)
    symbols = list(db.scalars(select(PaperOrder.symbol).where(PaperOrder.portfolio_id == portfolio.id).distinct()))
    prices = {s: q.price for s, q in latest_prices(symbols).items()} if symbols else {}
    snap = accounting.take_snapshot(db, portfolio, prices)
    return {"id": snap.id, "equity": snap.equity, "as_of": snap.as_of.isoformat()}


def _get_variant_portfolio(db: Session, variant_id: int) -> tuple[Variant, PaperPortfolio]:
    variant = db.get(Variant, variant_id)
    if variant is None or variant.portfolio is None:
        raise HTTPException(status_code=404, detail="variant not found")
    return variant, variant.portfolio
