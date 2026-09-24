"""The paper runner: the backtest's decisions, sent to a real broker.

For one model on one day it does exactly what the backtest does on one row —
builds the book, calls `engine.plan()`, gets orders — except that the book comes
from the model's ledger instead of a simulation, today's prices come from the
live session instead of a close, and the orders go to Alpaca instead of being
filled at that close.

Two safety rules, both about never trading on a wrong picture of the book:

- **No decision while an order is still pending.** A queued buy that has not
  filled yet is invisible to the ledger; deciding anyway would buy it twice.
- **Sells first, then buys sized on the cash that actually arrived.** During the
  session a market sell fills in seconds, so the runner waits for it, then
  re-reads the cash before buying. Outside the session nothing fills, so buys
  are limited to cash already on hand.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.lab import data as market
from app.lab.engine import Book, Holding, Order, load_for, plan
from app.lab.profiles import Decision, get_profile
from app.lab.universe import BENCHMARK, label
from app.models import LabEvent, PaperOrder, Position, Variant
from app.paper.service import (
    COST_BUFFER, OrderRejected, broker_for, open_orders, poll_open_orders, submit_order,
)

NY = ZoneInfo("America/New_York")
SETTLE_WAIT_SECONDS = 25


def journal(db: Session, variant: Variant | None, kind: str, message: str, symbol: str = "",
            data: dict | None = None, once_per_day: bool = False) -> None:
    """Append to the journal. `once_per_day` suppresses an identical message
    already written today — for conditions the monitor re-checks every minute
    (a pending order during the decision window) that would otherwise fill the
    journal with the same line fifteen times."""
    if once_per_day:
        last = db.scalar(select(LabEvent).where(
            LabEvent.variant_id == (variant.id if variant else None), LabEvent.message == message)
            .order_by(LabEvent.created_at.desc()).limit(1))
        if last is not None and last.created_at.date() == datetime.now(timezone.utc).date():
            return
    db.add(LabEvent(variant_id=variant.id if variant else None, kind=kind, symbol=symbol,
                    message=message, data=data or {}))
    db.commit()


def session_date() -> pd.Timestamp:
    return pd.Timestamp(datetime.now(NY).date())


@dataclass
class Plan:
    session: str
    rebalance: bool
    orders: list[Order]
    decision: Decision
    prices: dict[str, float]
    equity: float
    cash: float
    live: bool
    skipped: str | None = None
    holdings: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "session": self.session, "rebalance": self.rebalance, "live": self.live,
            "skipped": self.skipped, "equity": round(self.equity, 2), "cash": round(self.cash, 2),
            "orders": [{"symbol": o.symbol, "label": label(o.symbol), "side": o.side, "qty": round(o.qty, 6),
                        "price": round(o.price, 4), "notional": round(o.notional, 2), "reason": o.reason,
                        "note": o.note} for o in self.orders],
            "notes": {s: n for s, n in self.decision.notes.items()},
            "holdings": self.holdings,
        }


def build_book(db: Session, variant: Variant, close: pd.DataFrame) -> Book:
    portfolio = variant.portfolio
    dates = close.index
    holdings: dict[str, Holding] = {}
    if portfolio is None:
        return Book(cash=variant.budget)
    for pos in db.scalars(select(Position).where(Position.portfolio_id == portfolio.id, Position.qty > 0)):
        entry_i = min(int(dates.searchsorted(pd.Timestamp(pos.opened_at.date()))), len(dates) - 1)
        since = close[pos.symbol].iloc[entry_i:].dropna() if pos.symbol in close.columns else pd.Series(dtype=float)
        peak = float(max(since.max(), pos.avg_entry_price)) if len(since) else pos.avg_entry_price
        holdings[pos.symbol] = Holding(pos.symbol, pos.qty, pos.avg_entry_price, entry_i, peak)
    return Book(cash=portfolio.cash, holdings=holdings)


def evaluate(db: Session, variant: Variant, force_rebalance: bool = False, live: bool = True) -> Plan:
    """What the model would do right now. Pure: places nothing."""
    profile = get_profile(variant.engine)
    params = profile.resolve(variant.params)
    universe = profile.universe(params)
    panel = load_for(profile, params)
    bars = market.live_bars(sorted(set(universe) | {BENCHMARK})) if live else {}
    today = session_date()
    if bars:
        panel = market.with_live_row(panel, bars, today)
    ctx = profile.prepare(panel.select(universe), params)
    i = len(ctx.close.index) - 1
    session = ctx.close.index[i].date().isoformat()

    this_month = session_date().date().isoformat()[:7]
    rebalance = (force_rebalance or profile.cadence == "daily" or not variant.last_rebalance_on
                 or variant.last_rebalance_on[:7] != this_month)
    book = build_book(db, variant, ctx.close)
    prices = ctx.close.iloc[i]
    for h in book.holdings.values():
        p = prices.get(h.symbol)
        if p is not None and not math.isnan(p):
            h.peak = max(h.peak, float(p))
    orders, decision = plan(profile, ctx, i, book, params, rebalance)
    holdings = []
    for h in book.holdings.values():
        p = float(prices.get(h.symbol, h.entry_price))
        holdings.append({
            "symbol": h.symbol, "label": label(h.symbol), "qty": round(h.qty, 6),
            "entry_price": round(h.entry_price, 4), "price": round(p, 4),
            "value": round(h.qty * p, 2), "pnl_usd": round(h.qty * (p - h.entry_price), 2),
            "pnl_pct": round((p / h.entry_price - 1) * 100, 2) if h.entry_price else 0.0,
            "from_peak_pct": round((p / h.peak - 1) * 100, 2) if h.peak else 0.0,
            "held_sessions": i - h.entry_i,
        })
    return Plan(session, rebalance, orders, decision,
                {s: float(prices[s]) for s in prices.index if not math.isnan(prices[s])},
                book.equity(prices), book.cash, bool(bars), holdings=holdings)


def _wait_for_fills(db: Session, variant: Variant, ids: set[int]) -> None:
    deadline = time.monotonic() + SETTLE_WAIT_SECONDS
    while time.monotonic() < deadline:
        poll_open_orders(db, variant.portfolio)
        still = {o.id for o in open_orders(db, variant.portfolio)} & ids
        if not still:
            return
        time.sleep(2)


def decide_and_execute(db: Session, variant: Variant, force_rebalance: bool = False,
                       market_open: bool = True) -> Plan:
    portfolio = variant.portfolio
    poll_open_orders(db, portfolio)
    pending = open_orders(db, portfolio)
    if pending:
        p = evaluate(db, variant, force_rebalance)
        p.skipped = f"{len(pending)} ordre(s) encore en attente — décision reportée"
        journal(db, variant, "info", p.skipped, once_per_day=True)
        return p

    p = evaluate(db, variant, force_rebalance)
    params = get_profile(variant.engine).resolve(variant.params)
    placed: list[PaperOrder] = []

    sells = [o for o in p.orders if o.side == "sell"]
    buys = [o for o in p.orders if o.side == "buy"]
    for o in sells:
        held = db.scalar(select(Position.qty).where(Position.portfolio_id == portfolio.id,
                                                    Position.symbol == o.symbol)) or 0.0
        qty = math.floor(min(o.qty, held) * 1e9) / 1e9
        if qty <= 0:
            continue
        try:
            order = submit_order(db, portfolio=portfolio, symbol=o.symbol, side="sell", qty=qty,
                                 rationale=f"{o.reason}. {o.note}".strip(" ."), exit_reason=o.reason[:40])
            placed.append(order)
            journal(db, variant, "exit" if "stop" in o.reason or "durée" in o.reason else "order",
                    f"Vente {label(o.symbol)} — {o.reason}", o.symbol,
                    {"qty": qty, "price": o.price, "status": order.status})
        except OrderRejected as exc:
            journal(db, variant, "error", f"Vente {o.symbol} refusée : {exc}", o.symbol)

    if sells and market_open:
        _wait_for_fills(db, variant, {o.id for o in placed})
        db.refresh(portfolio)

    wanted = sum(o.notional for o in buys) * COST_BUFFER
    scale = min(1.0, portfolio.cash / wanted) if wanted > 0 else 1.0
    for o in buys:
        qty = math.floor(o.qty * scale * 0.999 * 1e6) / 1e6
        if qty * o.price < 5:
            continue
        max_loss = qty * o.price * (params["stop_loss_pct"] or params["trailing_stop_pct"] or 100) / 100
        try:
            order = submit_order(db, portfolio=portfolio, symbol=o.symbol, side="buy", qty=qty,
                                 max_loss=round(max_loss, 2), rationale=f"{o.reason}. {o.note}".strip(" ."))
            placed.append(order)
            journal(db, variant, "order", f"Achat {label(o.symbol)} — {o.note or o.reason}", o.symbol,
                    {"qty": qty, "price": o.price, "notional": round(qty * o.price, 2), "status": order.status})
        except OrderRejected as exc:
            journal(db, variant, "error", f"Achat {o.symbol} refusé : {exc}", o.symbol)

    # Bookkeeping uses the real session date, never the last row of the data.
    # If the live feed were missing, that row would be yesterday's, the check
    # "already decided today?" would never match, and the model would decide
    # again on every pass of the monitor until the close.
    today = session_date().date().isoformat()
    variant.last_decision_on = today
    if p.rebalance:
        variant.last_rebalance_on = today
    if not p.live:
        journal(db, variant, "info", "Flux de prix en direct indisponible : décision prise sur la dernière "
                                     "clôture connue.", once_per_day=True)
    db.commit()
    if not p.orders:
        journal(db, variant, "decision", "Aucun mouvement : rien ne justifie d'agir aujourd'hui.")
    else:
        journal(db, variant, "decision",
                f"Décision du {p.session} : {len(sells)} vente(s), {len(buys)} achat(s)"
                + (" — rééquilibrage" if p.rebalance and get_profile(variant.engine).cadence == "monthly" else ""),
                data={"orders": len(placed)})
    return p


def market_clock(db: Session) -> dict:
    try:
        return broker_for(db, "alpaca_paper").clock()
    except Exception as exc:
        return {"is_open": False, "error": str(exc)}


def mark_to_market(db: Session, variant: Variant, prices: dict[str, float] | None = None) -> dict:
    portfolio = variant.portfolio
    positions = list(db.scalars(select(Position).where(Position.portfolio_id == portfolio.id, Position.qty > 0)))
    if prices is None:
        try:
            prices = market.latest_prices([p.symbol for p in positions]) if positions else {}
        except market.DataUnavailable:
            prices = {}
    value = sum(p.qty * prices.get(p.symbol, p.avg_entry_price) for p in positions)
    return {"equity": portfolio.cash + value, "cash": portfolio.cash, "invested": value}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
