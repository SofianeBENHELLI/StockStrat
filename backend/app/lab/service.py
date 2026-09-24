"""Lab operations: the model's life from definition to paper and back.

Stages: `lab` -> `paper` -> `retired`. Parameters are frozen once a model is in
paper — changing them would make its live record the record of a different
model. To try other parameters on a running idea, clone it; the clone starts
in the lab with a fresh history.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import settings_store
from app.lab import runner
from app.lab.engine import backtest
from app.lab.profiles import PROFILES, get_profile
from app.lab.universe import label
from app.models import (
    BacktestRun, LabEvent, PaperOrder, PaperPortfolio, PortfolioSnapshot, Position, Variant,
)
from app.paper.brokers import BrokerUnavailable
from app.paper.service import broker_for, cancel_order, open_orders, submit_order, OrderRejected

LAB_STAGES = ("lab", "paper", "retired")
DEFAULT_BUDGET = 10_000.0
MAX_TRADES_STORED = 400


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    """SQLite hands datetimes back without a timezone even though they were
    written in UTC. Serialised as-is, a browser reads them as local time and
    every "il y a…" is off by the UTC offset. Say it's UTC, explicitly."""
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


# ------------------------------------------------------------------ models --

def models(db: Session, stages=LAB_STAGES) -> list[Variant]:
    return list(db.scalars(select(Variant).where(Variant.stage.in_(stages)).order_by(Variant.id)))


def seed_defaults(db: Session) -> list[Variant]:
    """The three variants of each profile, once. Idempotent."""
    if models(db):
        return []
    created = []
    for profile in PROFILES.values():
        for v in profile.variants():
            m = Variant(name=v.name, engine=profile.key, variant_key=v.key, description=v.description,
                        params=v.params, budget=DEFAULT_BUDGET, stage="lab")
            db.add(m)
            created.append(m)
    db.commit()
    runner.journal(db, None, "info", f"{len(created)} modèles créés (3 variantes × 3 profils).")
    return created


def create_model(db: Session, profile_key: str, name: str, params: dict, budget: float,
                 description: str = "", parent: Variant | None = None) -> Variant:
    profile = get_profile(profile_key)
    profile.resolve(params)  # validate now, not at the first backtest
    m = Variant(name=name.strip() or profile.label, engine=profile.key, description=description,
                params=params, budget=budget, stage="lab",
                parent_variant_id=parent.id if parent else None)
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def update_model(db: Session, m: Variant, *, name: str | None = None, params: dict | None = None,
                 budget: float | None = None, description: str | None = None) -> Variant:
    if params is not None and m.stage != "lab":
        raise ValueError("un modèle en paper a des paramètres figés : clonez-le pour en essayer d'autres")
    if params is not None:
        get_profile(m.engine).resolve(params)
        m.params = params
    if budget is not None:
        if m.stage != "lab":
            raise ValueError("le budget d'un modèle en paper est fixé à son déploiement")
        if budget < 100:
            raise ValueError("budget minimum : 100 $")
        m.budget = budget
    if name is not None:
        m.name = name.strip() or m.name
    if description is not None:
        m.description = description
    db.commit()
    db.refresh(m)
    return m


def clone_model(db: Session, m: Variant) -> Variant:
    return create_model(db, m.engine, f"{m.name} (copie)", dict(m.params or {}), m.budget,
                        m.description, parent=m)


# --------------------------------------------------------------- backtests --

def _store(db: Session, m: Variant, result) -> BacktestRun:
    s = result.summary()
    idx = result.equity.index
    run = BacktestRun(
        variant_id=m.id, params=result.params, start=result.start.isoformat(), end=result.end.isoformat(),
        budget=result.budget, summary=s,
        series={
            "dates": [d.date().isoformat() for d in idx],
            "equity": [round(float(x), 2) for x in result.equity],
            "benchmark": [round(float(x), 2) for x in result.benchmark.reindex(idx).ffill()],
            "placebo": [round(float(x), 2) for x in result.placebo.reindex(idx).ffill()],
        },
        trades=[{"day": f.day.date().isoformat(), "symbol": f.symbol, "label": label(f.symbol), "side": f.side,
                 "qty": round(f.qty, 4), "price": round(f.price, 4), "reason": f.reason,
                 "pnl": round(f.pnl, 2) if f.pnl is not None else None, "held_days": f.held_days}
                for f in result.fills[-MAX_TRADES_STORED:]],
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def run_backtest(db: Session, m: Variant, start: date, end: date | None = None,
                 budget: float | None = None) -> BacktestRun:
    result = backtest(m.engine, m.params, start, end, budget or m.budget)
    return _store(db, m, result)


def latest_backtest(db: Session, m: Variant) -> BacktestRun | None:
    return db.scalar(select(BacktestRun).where(BacktestRun.variant_id == m.id)
                     .order_by(BacktestRun.created_at.desc()).limit(1))


# ------------------------------------------------------------------- paper --

def allocated(db: Session) -> float:
    return sum(m.budget for m in models(db, ("paper",)))


def promote(db: Session, m: Variant, budget: float | None = None) -> dict:
    if m.stage != "lab":
        raise ValueError(f"« {m.name} » est déjà {m.stage} ; seul un modèle du labo peut être déployé")
    if m.portfolio is not None:
        raise ValueError("ce modèle a déjà un historique paper — clonez-le pour le redéployer")
    budget = float(budget or m.budget)
    cap = float(settings_store.resolve(db, "execution.max_total_allocation"))
    used = allocated(db)
    if used + budget > cap:
        raise ValueError(f"plafond d'allocation atteint : {used:,.0f} $ déjà alloués + {budget:,.0f} $ "
                         f"> {cap:,.0f} $ (Administration → Exécution)")
    broker = str(settings_store.resolve(db, "execution.default_broker"))
    if broker == "alpaca_paper":
        broker_for(db, broker)  # raises BrokerUnavailable when not configured

    db.add(PaperPortfolio(variant_id=m.id, initial_cash=budget, cash=budget, broker=broker))
    m.budget, m.stage, m.promoted_at = budget, "paper", utcnow()
    m.last_decision_on = m.last_rebalance_on = None
    db.commit()
    db.refresh(m)
    runner.journal(db, m, "promote", f"Déployé en paper avec {budget:,.0f} $ ({broker}).",
                   data={"budget": budget, "broker": broker})

    clock = runner.market_clock(db)
    p = runner.decide_and_execute(db, m, force_rebalance=True, market_open=bool(clock.get("is_open")))
    if not clock.get("is_open"):
        runner.journal(db, m, "info", "Marché fermé : les ordres sont en file et s'exécuteront à l'ouverture.")
    return {"model": serialize(db, m), "plan": p.as_dict(), "market_open": bool(clock.get("is_open"))}


def retire(db: Session, m: Variant) -> dict:
    if m.stage != "paper":
        raise ValueError("seul un modèle en paper peut être retiré")
    for o in open_orders(db, m.portfolio):
        try:
            cancel_order(db, o)
        except OrderRejected:
            pass
    sold = []
    for pos in db.scalars(select(Position).where(Position.portfolio_id == m.portfolio.id, Position.qty > 0)):
        try:
            submit_order(db, portfolio=m.portfolio, symbol=pos.symbol, side="sell", qty=pos.qty,
                         rationale="retrait du modèle", exit_reason="retrait")
            sold.append(pos.symbol)
        except OrderRejected as exc:
            runner.journal(db, m, "error", f"Liquidation {pos.symbol} refusée : {exc}", pos.symbol)
    m.stage = "retired"
    db.commit()
    runner.journal(db, m, "promote", f"Retiré du paper — {len(sold)} position(s) liquidée(s).")
    return serialize(db, m)


# ---------------------------------------------------------------- read side --

def _snapshots(db: Session, portfolio: PaperPortfolio) -> list[PortfolioSnapshot]:
    return list(db.scalars(select(PortfolioSnapshot).where(PortfolioSnapshot.portfolio_id == portfolio.id)
                           .order_by(PortfolioSnapshot.as_of)))


def paper_stats(db: Session, m: Variant, prices: dict[str, float] | None = None) -> dict:
    p = m.portfolio
    mtm = runner.mark_to_market(db, m, prices)
    snaps = _snapshots(db, p)
    curve = [s.equity for s in snaps] + [mtm["equity"]]
    peak, worst = p.initial_cash, 0.0
    for e in [p.initial_cash, *curve]:
        peak = max(peak, e)
        worst = min(worst, e - peak)
    today = runner.session_date().date()
    before_today = [s for s in snaps if s.as_of.date() < today]
    ref = before_today[-1].equity if before_today else p.initial_cash
    return {
        "equity": round(mtm["equity"], 2), "cash": round(mtm["cash"], 2), "invested": round(mtm["invested"], 2),
        "pnl_usd": round(mtm["equity"] - p.initial_cash, 2),
        "pnl_pct": round((mtm["equity"] / p.initial_cash - 1) * 100, 2),
        "today_usd": round(mtm["equity"] - ref, 2),
        "max_drawdown_usd": round(worst, 2),
        "positions": db.query(Position).filter(Position.portfolio_id == p.id, Position.qty > 0).count(),
        "open_orders": len(open_orders(db, p)),
        "broker": p.broker,
        "sparkline": [round(e, 2) for e in curve[-120:]],
    }


def serialize(db: Session, m: Variant, with_backtest: bool = True, prices: dict | None = None) -> dict:
    profile = get_profile(m.engine)
    out = {
        "id": m.id, "name": m.name, "profile": m.engine, "profile_label": profile.label,
        "variant_key": m.variant_key, "description": m.description, "params": m.params or {},
        "resolved_params": profile.resolve(m.params), "budget": m.budget, "stage": m.stage,
        "parent_id": m.parent_variant_id, "cadence": profile.cadence,
        "promoted_at": iso(m.promoted_at),
        "last_decision_on": m.last_decision_on, "created_at": iso(m.created_at),
    }
    if with_backtest:
        bt = latest_backtest(db, m)
        out["backtest"] = ({"id": bt.id, "start": bt.start, "end": bt.end, "budget": bt.budget,
                            "created_at": iso(bt.created_at),
                            **{k: bt.summary.get(k) for k in (
                                "pnl_usd", "pnl_pct", "max_drawdown_usd", "max_drawdown_pct", "cagr_pct", "sharpe",
                                "vs_benchmark_usd", "vs_placebo_usd", "return_over_drawdown", "exposure_pct")},
                            "n_sells": (bt.summary.get("trades") or {}).get("n_sells"),
                            "hit_rate_pct": (bt.summary.get("trades") or {}).get("hit_rate_pct"),
                            "benchmark_pnl_usd": (bt.summary.get("benchmark") or {}).get("pnl_usd"),
                            "placebo_pnl_usd": (bt.summary.get("placebo") or {}).get("pnl_usd"),
                            "spark": bt.series["equity"][:: max(1, len(bt.series["equity"]) // 80)]}
                           if bt else None)
    if m.portfolio is not None and m.stage in ("paper", "retired"):
        out["paper"] = paper_stats(db, m, prices)
    return out


def events(db: Session, m: Variant | None = None, limit: int = 60) -> list[dict]:
    q = select(LabEvent).order_by(LabEvent.created_at.desc()).limit(limit)
    if m is not None:
        q = q.where(LabEvent.variant_id == m.id)
    names = {v.id: v.name for v in db.scalars(select(Variant))}
    return [{"id": e.id, "model_id": e.variant_id, "model": names.get(e.variant_id), "kind": e.kind,
             "symbol": e.symbol, "message": e.message, "data": e.data, "at": iso(e.created_at)}
            for e in db.scalars(q)]


def orders(db: Session, m: Variant, limit: int = 100) -> list[dict]:
    if m.portfolio is None:
        return []
    rows = db.scalars(select(PaperOrder).where(PaperOrder.portfolio_id == m.portfolio.id)
                      .order_by(PaperOrder.created_at.desc()).limit(limit))
    return [{"id": o.id, "symbol": o.symbol, "label": label(o.symbol), "side": o.side, "qty": o.qty,
             "status": o.status, "filled_qty": o.filled_qty, "filled_avg_price": o.filled_avg_price,
             "requested_price": o.requested_price, "realized_pnl": o.realized_pnl, "reason": o.rationale,
             "broker": o.broker, "broker_order_id": o.broker_order_id, "created_at": iso(o.created_at)}
            for o in rows]


def equity_curve(db: Session, m: Variant) -> dict:
    """Paper equity, one point per session (the last snapshot of each day)."""
    if m.portfolio is None:
        return {"dates": [], "equity": []}
    by_day: dict[str, float] = {}
    for s in _snapshots(db, m.portfolio):
        by_day[s.as_of.date().isoformat()] = s.equity
    return {"dates": list(by_day), "equity": [round(v, 2) for v in by_day.values()]}


def replay(db: Session, m: Variant) -> dict:
    """The backtest re-run over exactly the period the model has been in paper.

    Same parameters, same budget, same days: the gap between this curve and the
    paper curve is execution and data — not the model — because the model code
    is literally the same."""
    if m.promoted_at is None:
        return {"available": False, "reason": "pas encore déployé"}
    start = m.promoted_at.date()
    try:
        r = backtest(m.engine, m.params, start, None, m.portfolio.initial_cash)
    except ValueError:
        return {"available": False, "reason": "il faut au moins deux séances complètes depuis le déploiement"}
    s = r.summary()
    return {"available": True, "dates": [d.date().isoformat() for d in r.equity.index],
            "equity": [round(float(x), 2) for x in r.equity], "pnl_usd": s["pnl_usd"],
            "max_drawdown_usd": s["max_drawdown_usd"]}
