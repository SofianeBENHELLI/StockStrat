"""HTTP surface of the lab: profiles, models, backtests, paper."""
from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import settings_store
from app.core.db import get_db
from app.lab import data as market
from app.lab import runner, service
from app.lab.profiles import PROFILES
from app.models import Position, Variant
from app.monitor import scheduler
from app.paper.brokers import BrokerUnavailable

router = APIRouter(prefix="/api/lab", tags=["lab"])


def _model(db: Session, model_id: int) -> Variant:
    m = db.get(Variant, model_id)
    if m is None or m.stage not in service.LAB_STAGES:
        raise HTTPException(404, "modèle introuvable")
    return m


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(409 if isinstance(exc, BrokerUnavailable) else 422, str(exc))


# --------------------------------------------------------------- profiles --

@router.get("/profiles")
def profiles() -> list[dict]:
    return [p.schema() for p in PROFILES.values()]


# ----------------------------------------------------------------- models --

class ModelIn(BaseModel):
    profile: str
    name: str = ""
    params: dict = Field(default_factory=dict)
    budget: float = service.DEFAULT_BUDGET
    description: str = ""


class ModelPatch(BaseModel):
    name: str | None = None
    params: dict | None = None
    budget: float | None = None
    description: str | None = None


@router.get("/models")
def list_models(db: Session = Depends(get_db)) -> list[dict]:
    return [service.serialize(db, m) for m in service.models(db)]


@router.post("/models/seed")
def seed(db: Session = Depends(get_db)) -> dict:
    created = service.seed_defaults(db)
    return {"created": len(created)}


@router.post("/models")
def create(body: ModelIn, db: Session = Depends(get_db)) -> dict:
    try:
        m = service.create_model(db, body.profile, body.name, body.params, body.budget, body.description)
    except ValueError as exc:
        raise _bad(exc)
    return service.serialize(db, m)


@router.get("/models/{model_id}")
def get_model(model_id: int, db: Session = Depends(get_db)) -> dict:
    m = _model(db, model_id)
    return {**service.serialize(db, m), "events": service.events(db, m, 80), "orders": service.orders(db, m)}


@router.patch("/models/{model_id}")
def patch_model(model_id: int, body: ModelPatch, db: Session = Depends(get_db)) -> dict:
    try:
        m = service.update_model(db, _model(db, model_id), **body.model_dump())
    except ValueError as exc:
        raise _bad(exc)
    return service.serialize(db, m)


@router.post("/models/{model_id}/clone")
def clone(model_id: int, db: Session = Depends(get_db)) -> dict:
    return service.serialize(db, service.clone_model(db, _model(db, model_id)))


@router.delete("/models/{model_id}")
def archive(model_id: int, db: Session = Depends(get_db)) -> dict:
    m = _model(db, model_id)
    if m.stage == "paper":
        raise HTTPException(409, "retirez d'abord le modèle du paper")
    m.stage = "archived"
    db.commit()
    return {"archived": m.id}


# -------------------------------------------------------------- backtests --

class BacktestIn(BaseModel):
    start: date = date(2019, 1, 1)
    end: date | None = None
    budget: float | None = None


@router.post("/models/{model_id}/backtest")
def run_backtest(model_id: int, body: BacktestIn, db: Session = Depends(get_db)) -> dict:
    m = _model(db, model_id)
    try:
        run = service.run_backtest(db, m, body.start, body.end, body.budget)
    except (ValueError, market.DataUnavailable) as exc:
        raise _bad(exc)
    return _run_payload(run)


@router.get("/models/{model_id}/backtest")
def latest(model_id: int, db: Session = Depends(get_db)) -> dict | None:
    run = service.latest_backtest(db, _model(db, model_id))
    return _run_payload(run) if run else None


@router.post("/backtest-all")
def backtest_all(body: BacktestIn, db: Session = Depends(get_db)) -> list[dict]:
    out = []
    for m in service.models(db, ("lab", "paper")):
        try:
            service.run_backtest(db, m, body.start, body.end, body.budget)
        except (ValueError, market.DataUnavailable) as exc:
            out.append({"id": m.id, "error": str(exc)})
            continue
        out.append(service.serialize(db, m))
    return out


def _run_payload(run) -> dict:
    return {"id": run.id, "model_id": run.variant_id, "params": run.params, "start": run.start, "end": run.end,
            "budget": run.budget, "summary": run.summary, "series": run.series, "trades": run.trades,
            "created_at": service.iso(run.created_at)}


# ------------------------------------------------------------------ paper --

class PromoteIn(BaseModel):
    budget: float | None = None


@router.get("/models/{model_id}/preview")
def preview(model_id: int, db: Session = Depends(get_db)) -> dict:
    """What the model would do right now — computed, never placed."""
    m = _model(db, model_id)
    try:
        return runner.evaluate(db, m, force_rebalance=m.stage == "lab").as_dict()
    except (ValueError, market.DataUnavailable) as exc:
        raise _bad(exc)


@router.post("/models/{model_id}/promote")
def promote(model_id: int, body: PromoteIn, db: Session = Depends(get_db)) -> dict:
    try:
        return service.promote(db, _model(db, model_id), body.budget)
    except (ValueError, BrokerUnavailable, market.DataUnavailable) as exc:
        raise _bad(exc)


@router.post("/models/{model_id}/run-now")
def run_now(model_id: int, db: Session = Depends(get_db)) -> dict:
    """Decide and execute immediately, outside the scheduled window."""
    m = _model(db, model_id)
    if m.stage != "paper":
        raise HTTPException(409, "seul un modèle en paper peut exécuter")
    clock = runner.market_clock(db)
    return runner.decide_and_execute(db, m, market_open=bool(clock.get("is_open"))).as_dict()


@router.post("/models/{model_id}/retire")
def retire(model_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        return service.retire(db, _model(db, model_id))
    except ValueError as exc:
        raise _bad(exc)


@router.get("/models/{model_id}/paper")
def paper_detail(model_id: int, db: Session = Depends(get_db)) -> dict:
    m = _model(db, model_id)
    if m.portfolio is None:
        raise HTTPException(404, "ce modèle n'a jamais été déployé")
    try:
        now = runner.evaluate(db, m).as_dict()
    except (ValueError, market.DataUnavailable) as exc:
        now = {"error": str(exc)}
    return {
        "model": service.serialize(db, m), "now": now, "curve": service.equity_curve(db, m),
        "replay": service.replay(db, m), "orders": service.orders(db, m), "events": service.events(db, m, 100),
    }


@router.get("/overview")
def overview(db: Session = Depends(get_db)) -> dict:
    """Everything the trading-floor dashboard shows, in one call."""
    paper = service.models(db, ("paper",))
    ids = [m.portfolio.id for m in paper if m.portfolio is not None]
    symbols = sorted(set(db.scalars(select(Position.symbol).where(
        Position.portfolio_id.in_(ids), Position.qty > 0)))) if ids else []
    try:
        prices = market.latest_prices(symbols) if symbols else {}
    except market.DataUnavailable:
        prices = {}
    cards = [service.serialize(db, m, prices=prices) for m in service.models(db)]
    live = [c for c in cards if c["stage"] == "paper"]
    return {
        "models": cards,
        "totals": {
            "allocated": round(sum(c["budget"] for c in live), 2),
            "equity": round(sum(c["paper"]["equity"] for c in live), 2),
            "pnl_usd": round(sum(c["paper"]["pnl_usd"] for c in live), 2),
            "today_usd": round(sum(c["paper"]["today_usd"] for c in live), 2),
            "cap": float(settings_store.resolve(db, "execution.max_total_allocation")),
        },
        "clock": runner.market_clock(db),
        "monitor": scheduler.status(),
        "events": service.events(db, None, 40),
        "now": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/monitor/run-once")
def monitor_run_once(force_decide: bool = False) -> dict:
    return scheduler.run_once(force_decide=force_decide)
