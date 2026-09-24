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
    last_backup_day: str | None = None
    last_backup: str | None = None


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

    try:
        _daily_summary(db, clock)
    except Exception as exc:  # a summary must never break the pass
        result["errors"].append({"model": "-", "stage": "summary", "error": str(exc)})

    day = now.date().isoformat()
    if STATE.last_backup_day != day:
        from app.core.backup import backup_now
        try:
            target = backup_now()
            STATE.last_backup_day = day
            STATE.last_backup = target.name if target else None
        except Exception as exc:
            result["errors"].append({"model": "-", "stage": "backup", "error": str(exc)})

    result["ran_at"] = started.isoformat()
    result["duration_ms"] = int((_now() - started).total_seconds() * 1000)
    return result


def _daily_summary(db: Session, clock: dict, now: datetime | None = None) -> None:
    """Once per trading day, after the close: where every model stands, in
    dollars, and whether the account is concentrated. Sent once — the journal
    entry of the day is the marker."""
    from zoneinfo import ZoneInfo

    from app import notify
    from app.lab import service
    from app.lab.exposure import account_exposure
    from app.models import LabEvent

    ny = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/New_York"))
    if clock.get("is_open") or ny.weekday() >= 5 or ny.hour * 60 + ny.minute < 16 * 60 + 10:
        return
    today = ny.date().isoformat()
    live = [m for m in db.scalars(select(Variant).where(Variant.stage == "paper")) if m.portfolio]
    if not live or not any(m.last_decision_on == today for m in live):
        return  # not a trading day for us (holiday, or the app was not running at decision time)
    marker = f"Résumé du {today}"
    if db.scalar(select(LabEvent).where(LabEvent.kind == "summary", LabEvent.message.like(f"{marker}%"))):
        return

    cards = [service.serialize(db, m, with_backtest=False) for m in live]
    total = sum(c["paper"]["equity"] for c in cards)
    day = sum(c["paper"]["today_usd"] for c in cards)
    pnl = sum(c["paper"]["pnl_usd"] for c in cards)
    lines = [f"Total {total:,.0f} $ · aujourd'hui {day:+,.0f} $ · depuis le départ {pnl:+,.0f} $".replace(",", " ")]
    for c in sorted(cards, key=lambda c: -c["paper"]["today_usd"]):
        lines.append(f"{c['name']} : {c['paper']['today_usd']:+,.0f} $ ({c['paper']['pnl_usd']:+,.0f} $)".replace(",", " "))
    missed = [m.name for m in live if m.last_decision_on != today]
    if missed:
        lines.append(f"Décision manquée : {', '.join(missed)}")
    exposure = account_exposure(db)
    for a in exposure["alerts"]:
        lines.append(f"Concentration : {a['sector']} = {a['pct']:.0f} % du capital engagé ({a['models']} modèles)")
    message = "\n".join(lines)
    runner.journal(db, None, "summary", f"{marker} — {lines[0]}", data={"lines": lines})
    notify.send(db, f"StockStrat · clôture du {ny.strftime('%d/%m')}", message,
                priority="high" if exposure["alerts"] or missed else "default", kind="summary",
                tags=["chart_with_upwards_trend" if day >= 0 else "chart_with_downwards_trend"])


def _heartbeat() -> None:
    """Ping an external dead-man's-switch (healthchecks.io, Uptime Kuma…) if
    one is configured. It is the only alert that still works when this app,
    or the whole machine, is down: the external service notices the silence."""
    import urllib.request

    db = SessionLocal()
    try:
        url = str(settings_store.resolve(db, "monitor.heartbeat_url") or "").strip()
    finally:
        db.close()
    if url:
        try:
            urllib.request.urlopen(url, timeout=5).read()  # noqa: S310 - URL comes from settings
        except Exception as exc:
            log.warning("heartbeat ping failed: %s", exc)


def _record(result: dict) -> None:
    if not result["errors"]:
        _heartbeat()
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
        "clock": STATE.clock, "reconciliation": STATE.reconciliation, "last_backup": STATE.last_backup,
    }
