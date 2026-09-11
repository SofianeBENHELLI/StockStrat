from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.data.provider import latest_prices
from app.models import CycleRun, PaperOrder, Variant
from app.paper import accounting
from app.tournament.cycle import run_cycle
from app.tournament.seed import seed_roster

router = APIRouter(prefix="/api/tournament", tags=["tournament"])


@router.post("/seed")
def seed(db: Session = Depends(get_db)) -> dict:
    created = seed_roster(db)
    return {"created": len(created), "variants": [v.name for v in created]}


@router.post("/cycle/run")
def trigger_cycle(db: Session = Depends(get_db)) -> dict:
    run = run_cycle(db)
    return {"id": run.id, "status": run.status, "summary": run.summary, "detail": run.detail}


@router.get("/cycles")
def list_cycles(db: Session = Depends(get_db)) -> list[dict]:
    runs = db.scalars(select(CycleRun).order_by(CycleRun.started_at.desc()).limit(30)).all()
    return [{
        "id": r.id, "status": r.status, "summary": r.summary,
        "started_at": r.started_at.isoformat(), "finished_at": r.finished_at.isoformat() if r.finished_at else None,
    } for r in runs]


@router.get("/cycles/{cycle_id}")
def get_cycle(cycle_id: int, db: Session = Depends(get_db)) -> dict:
    run = db.get(CycleRun, cycle_id)
    if run is None:
        raise HTTPException(status_code=404, detail="cycle not found")
    return {
        "id": run.id, "status": run.status, "summary": run.summary, "detail": run.detail,
        "started_at": run.started_at.isoformat(), "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


@router.get("/leaderboard")
def leaderboard(db: Session = Depends(get_db)) -> list[dict]:
    """Ranked by risk-adjusted performance (Sharpe once there's enough multi-day
    history; total_pnl_pct in the meantime — see tournament/cycle.py for why),
    never raw profit. Includes killed variants (marked, not hidden) so the
    tournament's history stays visible."""
    variants = db.scalars(select(Variant).order_by(Variant.engine, Variant.variant_key)).all()
    rows = []
    for v in variants:
        if v.portfolio is None:
            continue
        symbols = list(db.scalars(select(PaperOrder.symbol).where(PaperOrder.portfolio_id == v.portfolio.id).distinct()))
        prices = {s: q.price for s, q in latest_prices(symbols).items()} if symbols else {}
        s = accounting.summary(db, v.portfolio, prices)
        rows.append({
            "variant_id": v.id, "name": v.name, "engine": v.engine, "variant_key": v.variant_key,
            "status": v.status, "generation": v.generation, "parent_variant_id": v.parent_variant_id,
            "equity": s["equity"], "total_pnl_pct": s["total_pnl_pct"], "sharpe": s["sharpe"],
            "sortino": s["sortino"], "max_drawdown_pct": s["max_drawdown_pct"], "volatility_pct": s["volatility_pct"],
            "hit_rate_pct": s["hit_rate_pct"], "profit_factor": s["profit_factor"],
            "n_trades": s["n_trades"], "n_closed_trades": s["n_closed_trades"], "n_positions": s["n_positions"],
        })

    def rank_key(r: dict) -> tuple:
        # bootstrap: without enough closed trades / multi-day history, rank by
        # P&L; a real Sharpe (nonzero, enough trades) takes priority once it exists.
        # a variant that hasn't traded yet shows 0% P&L, which would otherwise
        # rank ABOVE variants that took real risk and are down a little on
        # slippage — that's not "performance", it's "no data yet". Push
        # untested variants below anything that has actually traded.
        has_traded = r["n_trades"] > 0
        has_real_sharpe = r["n_closed_trades"] >= 3 and r["sharpe"] != 0
        return (
            r["status"] == "killed", not has_traded, not has_real_sharpe,
            -(r["sharpe"] if has_real_sharpe else r["total_pnl_pct"]),
        )

    rows.sort(key=rank_key)
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    return rows
