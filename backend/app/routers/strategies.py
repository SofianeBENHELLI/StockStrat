from __future__ import annotations

import math

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agent.explain import resolve_explanation
from app.core.db import get_db
from app.data.provider import latest_prices
from app.models import DecisionLog
from app.paper.service import OrderRejected, submit_order
from app.routers.variants import _get_variant_portfolio
from app.strategies import casino, economist, ml_baseline
from app.strategies.base import TradeIdea

router = APIRouter(prefix="/api/strategies", tags=["strategies"])

ENGINES = {"casino": casino, "ml": ml_baseline, "economist": economist}


def _idea_out(idea: TradeIdea) -> dict:
    return {
        "symbol": idea.symbol, "engine": idea.engine, "action": idea.action,
        "score": idea.score, "confidence": idea.confidence, "structure": idea.structure,
        "direction": idea.direction, "size_pct_of_equity": idea.size_pct_of_equity,
        "predicted_move_pct": idea.predicted_move_pct, "expected_vol_pct": idea.expected_vol_pct,
        "rationale": idea.rationale, "invalidation": idea.invalidation, "main_risk": idea.main_risk,
        "factors": idea.factor_breakdown(),
    }


@router.get("/{engine}/ideas")
def get_ideas(engine: str) -> list[dict]:
    module = ENGINES.get(engine)
    if module is None:
        raise HTTPException(status_code=404, detail=f"unknown engine '{engine}' (casino | ml | economist)")
    return [_idea_out(i) for i in module.generate_ideas()]


class ExecuteIn(BaseModel):
    variant_id: int


@router.post("/{engine}/ideas/{symbol}/execute")
def execute_idea(engine: str, symbol: str, body: ExecuteIn, db: Session = Depends(get_db)) -> dict:
    module = ENGINES.get(engine)
    if module is None:
        raise HTTPException(status_code=404, detail=f"unknown engine '{engine}' (casino | ml | economist)")

    idea = next((i for i in module.generate_ideas(limit=50) if i.symbol == symbol.upper()), None)
    if idea is None:
        raise HTTPException(status_code=404, detail=f"'{symbol}' is not currently a ranked idea for engine '{engine}'")

    variant, portfolio = _get_variant_portfolio(db, body.variant_id)
    quote = latest_prices([idea.symbol]).get(idea.symbol)
    if quote is None:
        raise HTTPException(status_code=400, detail=f"no price available for {idea.symbol}")

    budget = round(portfolio.cash * idea.size_pct_of_equity, 2)
    qty = math.floor(budget / quote.price)
    if qty < 1:
        raise HTTPException(status_code=400, detail=(
            f"sizing budget ({budget:.2f} = {idea.size_pct_of_equity * 100:.2f}% of cash) "
            f"buys less than 1 share of {idea.symbol} at {quote.price:.2f}"
        ))

    try:
        order = submit_order(
            db, portfolio=portfolio, symbol=idea.symbol, side="buy", qty=qty,
            max_loss=budget, rationale=f"[{engine}] {idea.structure}: {idea.rationale}",
        )
    except OrderRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    rationale, invalidation, main_risk, source = resolve_explanation(idea)
    db.add(DecisionLog(
        variant_id=variant.id, order_id=order.id, symbol=idea.symbol, action=idea.action,
        structure=idea.structure, engine=engine, confidence=idea.confidence, score=idea.score,
        main_risk=main_risk, rationale=rationale, invalidation=invalidation,
        factors=idea.factor_breakdown(), explanation_source=source,
    ))
    db.commit()

    return {
        "order_id": order.id, "order_status": order.status, "filled_qty": order.filled_qty,
        "filled_avg_price": order.filled_avg_price, "structure": idea.structure, "budget": budget,
    }
