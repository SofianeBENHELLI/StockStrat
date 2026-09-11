"""The background monitor: one loop, one pass per interval, over every active
variant's portfolio.

It does three things per portfolio, in this order:

1. **Poll resting orders** — a limit that has not crossed is re-evaluated
   against a fresh price. Without this, an `open` order would never resolve,
   because nothing else in the application revisits it.
2. **Apply exit rules** — the stop/target/clock from app/paper/exits.py. This
   is what makes exits *automatic* rather than something that only happens when
   a human triggers a cycle.
3. **Snapshot equity** — and this is the one that is easy to dismiss as
   bookkeeping. Sharpe, Sortino and max drawdown are computed from the spacing
   of snapshots (app/paper/metrics.py). Recording equity only when someone
   clicks "run a cycle" means the series is neither daily nor evenly spaced,
   and annualising it is simply wrong — the same class of error that produced a
   Sharpe of 9.4 in an early backtest draft. A fixed-interval snapshot is what
   makes the live equity curve honest.

Failure isolation is per portfolio: one symbol that cannot be priced must not
stop the other thirteen variants from being marked. Each portfolio is wrapped
in its own try/rollback and its error recorded in the pass result.

Scope: this is an in-process asyncio task, sized for a single-operator install.
It is deliberately not a job queue. If this ever needs to serve many users
concurrently, the loop moves to a worker process — nothing else here depends on
it living inside the API.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import settings_store
from app.core.db import SessionLocal
from app.data.provider import latest_prices
from app.models import PaperOrder, Position, Variant
from app.paper import accounting, exits
from app.paper.service import poll_open_orders

log = logging.getLogger("monitor")


@dataclass
class MonitorState:
    running: bool = False
    enabled: bool = True
    interval_seconds: int = 60
    last_run_at: str | None = None
    last_duration_ms: int | None = None
    last_error: str | None = None
    passes: int = 0
    last_result: dict = field(default_factory=dict)


STATE = MonitorState()
_task: asyncio.Task | None = None


def _portfolio_pass(db: Session, variant: Variant) -> dict:
    portfolio = variant.portfolio
    if portfolio is None:
        return {"variant": variant.name, "skipped": "no portfolio"}

    filled = poll_open_orders(db, portfolio)

    held = list(db.scalars(select(Position.symbol).where(
        Position.portfolio_id == portfolio.id, Position.qty > 0)))
    prices = {s: q.price for s, q in latest_prices(held).items()} if held else {}
    exited = exits.apply(db, portfolio, prices)

    # Value against every symbol the portfolio has ever touched, so a position
    # closed during this same pass is still priced correctly in the snapshot.
    symbols = list(db.scalars(select(PaperOrder.symbol).where(
        PaperOrder.portfolio_id == portfolio.id).distinct()))
    snapshot_prices = {s: q.price for s, q in latest_prices(symbols).items()} if symbols else {}
    snap = accounting.take_snapshot(db, portfolio, snapshot_prices)

    return {
        "variant": variant.name,
        "orders_resolved": [{"id": o.id, "symbol": o.symbol, "status": o.status} for o in filled],
        "exits": [{"symbol": o.symbol, "reason": o.exit_reason, "realized_pnl": o.realized_pnl}
                  for o in exited],
        "equity": snap.equity,
    }


def run_pass(db: Session) -> dict:
    """One full sweep. Synchronous and safe to call directly — the HTTP
    'run now' button and the loop share this exact code path."""
    started = datetime.now(timezone.utc)
    results, errors = [], []

    for variant in db.scalars(select(Variant).where(Variant.status == "active")):
        try:
            results.append(_portfolio_pass(db, variant))
        except Exception as exc:  # one bad portfolio must not end the pass
            db.rollback()
            log.warning("monitor pass failed for variant %s: %s", variant.name, exc)
            errors.append({"variant": variant.name, "error": str(exc)})

    duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    return {
        "ran_at": started.isoformat(), "duration_ms": duration_ms,
        "portfolios": len(results), "results": results, "errors": errors,
    }


def _record(result: dict) -> None:
    STATE.last_run_at = result["ran_at"]
    STATE.last_duration_ms = result["duration_ms"]
    STATE.last_error = result["errors"][0]["error"] if result["errors"] else None
    STATE.passes += 1
    STATE.last_result = {
        "portfolios": result["portfolios"],
        "orders_resolved": sum(len(r.get("orders_resolved", [])) for r in result["results"]),
        "exits": sum(len(r.get("exits", [])) for r in result["results"]),
        "errors": len(result["errors"]),
    }


def run_once() -> dict:
    """Manual trigger. Owns its own session so it is safe from any caller."""
    db = SessionLocal()
    try:
        result = run_pass(db)
        _record(result)
        return result
    finally:
        db.close()


async def _loop() -> None:
    STATE.running = True
    log.info("monitor loop started")
    try:
        while True:
            db = SessionLocal()
            try:
                # Re-read settings every pass so the Administration panel takes
                # effect without a restart — including switching the loop off.
                STATE.enabled = bool(settings_store.resolve(db, "monitor.enabled"))
                STATE.interval_seconds = int(settings_store.resolve(db, "monitor.interval_seconds"))
                if STATE.enabled:
                    _record(run_pass(db))
            except Exception as exc:
                STATE.last_error = str(exc)
                log.exception("monitor loop pass failed")
            finally:
                db.close()
            await asyncio.sleep(max(STATE.interval_seconds, 5))
    except asyncio.CancelledError:
        raise
    finally:
        STATE.running = False
        log.info("monitor loop stopped")


async def start() -> None:
    global _task
    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(_loop())


async def stop() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None


def status() -> dict:
    return {
        "running": STATE.running, "enabled": STATE.enabled,
        "interval_seconds": STATE.interval_seconds, "passes": STATE.passes,
        "last_run_at": STATE.last_run_at, "last_duration_ms": STATE.last_duration_ms,
        "last_error": STATE.last_error, "last_result": STATE.last_result,
    }
