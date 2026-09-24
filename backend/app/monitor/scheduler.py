"""The heartbeat: one pass per interval, for every model in paper.

Each pass, in order:

1. **Poll pending orders** (paper and retired models): an order queued
   overnight fills at the open, and this is where the ledger learns about it.
2. **Decide**, once per session per model, in the last minutes before the
   close — the live equivalent of the close the backtest trades at. Stops are
   checked at this same moment for every profile; the monthly profiles only
   rebalance on the first session of a new month.
3. **Mark to market** each model's equity. Snapshot spacing is what drawdown is
   computed from, so this runs every pass while the market is open and every
   half hour while it is closed.
4. **Reconcile** the sum of all sub-ledgers against Alpaca's positions.

Failure isolation is per model: a model that throws is journalled and skipped,
never allowed to stop the others. In-process by design — a single-operator
install does not need a job queue, and nothing here depends on living inside
the API process if that ever changes.
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
from app.lab import data as market
from app.lab import runner
from app.lab.universe import label
from app.models import PaperPortfolio, PortfolioSnapshot, Position, Variant
from app.paper import venue
from app.paper.service import poll_open_orders

log = logging.getLogger("monitor")
CLOSED_SNAPSHOT_EVERY_S = 1800
RECONCILE_EVERY_S = 300


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
    clock: dict = field(default_factory=dict)
    reconciliation: dict = field(default_factory=dict)
    last_snapshot_at: datetime | None = None
    last_reconcile_at: datetime | None = None
    reconcile_failures: int = 0


STATE = MonitorState()
_task: asyncio.Task | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _minutes_to_close(clock: dict) -> float | None:
    if not clock.get("is_open") or not clock.get("next_close"):
        return None
    return (datetime.fromisoformat(clock["next_close"]) - _now()).total_seconds() / 60


def _journal_fills(db: Session, m: Variant, changed) -> None:
    for o in changed:
        if o.status in ("filled", "partial_fill") and o.purpose == "safety_stop":
            runner.journal(db, m, "exit", f"Stop de secours déclenché chez le broker : {label(o.symbol)} "
                                          f"{o.filled_qty:g} @ {o.filled_avg_price:,.2f} $, P&L réalisé "
                                          f"{(o.realized_pnl or 0):+,.2f} $", o.symbol, {"order_id": o.id})
        elif o.status in ("filled", "partial_fill"):
            verb = "Achat" if o.side == "buy" else "Vente"
            pnl = f", P&L réalisé {o.realized_pnl:+,.2f} $" if o.realized_pnl is not None else ""
            runner.journal(db, m, "fill", f"{verb} exécuté : {label(o.symbol)} {o.filled_qty:g} @ "
                                          f"{o.filled_avg_price:,.2f} ${pnl}", o.symbol,
                           {"order_id": o.id, "status": o.status})
        elif o.status in ("rejected", "cancelled"):
            runner.journal(db, m, "error", f"Ordre {o.side} {o.symbol} {o.status} par le broker", o.symbol,
                           {"order_id": o.id})


def run_pass(db: Session, force_decide: bool = False) -> dict:
    started = _now()
    result = {"decided": [], "fills": 0, "snapshots": 0, "errors": []}
    clock = runner.market_clock(db)
    STATE.clock = clock

    tracked = list(db.scalars(select(Variant).where(Variant.stage.in_(("paper", "retired")))))
    for m in tracked:
        if m.portfolio is None:
            continue
        try:
            changed = poll_open_orders(db, m.portfolio)
            result["fills"] += len(changed)
            _journal_fills(db, m, changed)
        except Exception as exc:
            db.rollback()
            result["errors"].append({"model": m.name, "stage": "poll", "error": str(exc)})

    live = [m for m in tracked if m.stage == "paper" and m.portfolio is not None]
    window = int(settings_store.resolve(db, "monitor.decision_minutes_before_close"))
    to_close = _minutes_to_close(clock)
    if force_decide or (to_close is not None and to_close <= window):
        today = runner.session_date().date().isoformat()
        for m in live:
            if m.last_decision_on == today and not force_decide:
                continue
            try:
                p = runner.decide_and_execute(db, m, market_open=bool(clock.get("is_open")))
                result["decided"].append({"model": m.name, "orders": len(p.orders), "skipped": p.skipped})
            except Exception as exc:
                db.rollback()
                runner.journal(db, m, "error", f"Décision impossible : {exc}")
                result["errors"].append({"model": m.name, "stage": "decide", "error": str(exc)})

    now = _now()
    due = clock.get("is_open") or STATE.last_snapshot_at is None or \
        (now - STATE.last_snapshot_at).total_seconds() >= CLOSED_SNAPSHOT_EVERY_S
    if due and live:
        symbols = sorted({p.symbol for m in live for p in db.scalars(
            select(Position).where(Position.portfolio_id == m.portfolio.id, Position.qty > 0))})
        try:
            prices = market.latest_prices(symbols) if symbols else {}
        except market.DataUnavailable:
            prices = {}
        for m in live:
            mtm = runner.mark_to_market(db, m, prices)
            db.add(PortfolioSnapshot(portfolio_id=m.portfolio.id, equity=mtm["equity"], cash=mtm["cash"]))
            result["snapshots"] += 1
        db.commit()
        STATE.last_snapshot_at = now

    # A mismatch is re-checked a minute later before it is reported: an order
    # can fill at Alpaca between this pass's poll and its reconciliation, and
    # that transient gap closes on its own at the next poll. Only a gap that
    # survives two consecutive checks is a real disagreement.
    recheck = STATE.reconcile_failures > 0
    if recheck or STATE.last_reconcile_at is None or \
            (now - STATE.last_reconcile_at).total_seconds() >= RECONCILE_EVERY_S:
        try:
            STATE.reconciliation = {**venue.reconcile(db), "at": now.isoformat()}
            if STATE.reconciliation.get("ok"):
                STATE.reconcile_failures = 0
            else:
                STATE.reconcile_failures += 1
                if STATE.reconcile_failures == 2:
                    runner.journal(db, None, "error", f"Réconciliation : {STATE.reconciliation.get('detail')}",
                                   data={"differences": STATE.reconciliation.get("differences", [])})
        except Exception as exc:
            STATE.reconciliation = {"ok": False, "detail": str(exc), "at": now.isoformat()}
        STATE.last_reconcile_at = now

    result["ran_at"] = started.isoformat()
    result["duration_ms"] = int((_now() - started).total_seconds() * 1000)
    return result


def _record(result: dict) -> None:
    STATE.last_run_at = result["ran_at"]
    STATE.last_duration_ms = result["duration_ms"]
    STATE.last_error = result["errors"][0]["error"] if result["errors"] else None
    STATE.passes += 1
    STATE.last_result = {"decided": len(result["decided"]), "fills": result["fills"],
                         "snapshots": result["snapshots"], "errors": len(result["errors"])}


def run_once(force_decide: bool = False) -> dict:
    db = SessionLocal()
    try:
        result = run_pass(db, force_decide=force_decide)
        _record(result)
        return result
    finally:
        db.close()


async def _loop() -> None:
    STATE.running = True
    try:
        while True:
            db = SessionLocal()
            try:
                STATE.enabled = bool(settings_store.resolve(db, "monitor.enabled"))
                STATE.interval_seconds = int(settings_store.resolve(db, "monitor.interval_seconds"))
            finally:
                db.close()
            if STATE.enabled:
                try:
                    _record(await asyncio.to_thread(run_once))
                except Exception as exc:
                    STATE.last_error = str(exc)
                    log.exception("monitor pass failed")
            await asyncio.sleep(max(STATE.interval_seconds, 5))
    finally:
        STATE.running = False


async def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())


async def stop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None


def status() -> dict:
    return {
        "running": STATE.running, "enabled": STATE.enabled, "interval_seconds": STATE.interval_seconds,
        "passes": STATE.passes, "last_run_at": STATE.last_run_at, "last_duration_ms": STATE.last_duration_ms,
        "last_error": STATE.last_error, "last_result": STATE.last_result,
        "clock": STATE.clock, "reconciliation": STATE.reconciliation,
    }
