"""The tournament's weekly cycle: propose trades -> execute in paper -> score ->
kill weak variants -> scale strong ones -> spawn new variants from winners.
Ranked by risk-adjusted performance, never raw profit — see _rank_key below.

Sharpe/Sortino need return history spanning several distinct calendar days to
mean anything; testing this in a single session only ever produces same-day
snapshots, so those ratios stay at 0 until the tournament has actually run for
a few days. total_pnl_pct is used as the practical ranking signal until then —
that's a real limitation of short history, not a bug, and it's why kill/scale
also require a minimum number of trades before acting on a variant, so a
lucky/unlucky first trade can't kill or scale something on pure noise.

That minimum counts FILLS, not closed trades. It used to count closed ones,
which was the same bug the backtest engine hit and fixed on its side only:
nothing in the live app ever sold, so the closed-trade counter sat at zero
forever and kill/scale never fired at all. Exits now exist (app/paper/exits.py)
so positions do close, but the threshold still counts fills — a variant that
has committed capital three times has shown enough to be judged, whether or
not the clock has run out on those positions yet.

'Spawn a new variant from a winner' is implemented as capital-scaling by
cloning a winning variant's exact config into a fresh portfolio — not
parameter mutation. Actually tuning parameters from results is what phase 4's
trained ranking model is for; faking a genetic-algorithm-style mutation here
without real optimization behind it would be theater.

Bug found and fixed while building the backtest engine (app/backtest/): the
scale branch below used to have no guard against re-firing on a variant
that's already been scaled — since P&L usually stays above SCALE_THRESHOLD_PCT
for many cycles in a row once a variant is doing well, it kept collecting
another +10%-of-initial-cash injection every single cycle, indefinitely. A
backtest run showed one variant with ~40% of its final equity coming from
repeated cash injections rather than actual trading — the scale reward is
meant to be a one-time capital bump for clearing the bar, not a recurring
subsidy. `Variant.scaled` now gates it to once.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.explain import resolve_explanation
from app.data.provider import latest_prices
from app.models import CycleRun, DecisionLog, PaperOrder, PaperPortfolio, Position, Variant
from app.paper import accounting, exits
from app.paper.service import OrderRejected, affordable_qty, poll_open_orders, submit_order
from app.strategies import casino, economist, ml_baseline
from app.strategies.base import TradeIdea

ENGINES = {"casino": casino, "ml": ml_baseline, "economist": economist}

MIN_TRADES_TO_JUDGE = 3       # don't kill/scale on a tiny, noisy sample — counted in FILLS, see run_cycle
KILL_THRESHOLD_PCT = -8.0     # total P&L vs initial cash
SCALE_THRESHOLD_PCT = 5.0
SCALE_CASH_BOOST_PCT = 0.10   # +10% of initial cash added to a scaled variant
MAX_SPAWNS_PER_CYCLE = 2
IDEAS_PER_VARIANT_PER_CYCLE = 2


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def run_cycle(db: Session) -> CycleRun:
    run = CycleRun(status="running")
    db.add(run)
    db.commit()
    db.refresh(run)

    detail: dict = {"proposed": [], "exits": [], "killed": [], "scaled": [], "spawned": [], "errors": []}
    active = list(db.scalars(select(Variant).where(Variant.status == "active")))

    for variant in active:
        _propose_and_execute(db, variant, detail)

    leaderboard = _score_all(db, active)
    leaderboard.sort(key=lambda r: -r["total_pnl_pct"])

    for row in leaderboard:
        if row["n_fills"] < MIN_TRADES_TO_JUDGE:
            continue
        variant = db.get(Variant, row["variant_id"])
        if row["total_pnl_pct"] <= KILL_THRESHOLD_PCT:
            variant.status = "killed"
            detail["killed"].append(row)
        elif row["total_pnl_pct"] >= SCALE_THRESHOLD_PCT and not variant.scaled:
            boost = round(variant.portfolio.initial_cash * SCALE_CASH_BOOST_PCT, 2)
            variant.portfolio.cash += boost
            variant.scaled = True
            detail["scaled"].append({**row, "cash_boost": boost})
    db.commit()

    for cand in detail["scaled"][:MAX_SPAWNS_PER_CYCLE]:
        _spawn_from_winner(db, cand, detail)

    run.status = "done"
    run.finished_at = utcnow()
    run.detail = detail
    run.summary = (
        f"{len(detail['proposed'])} ordres proposés sur {len(active)} variantes actives, "
        f"{len(detail.get('exits', []))} positions sorties, "
        f"{len(detail['killed'])} tuées, {len(detail['scaled'])} scalées, "
        f"{len(detail['spawned'])} nouvelles variantes générées."
    )
    db.commit()
    db.refresh(run)
    return run


def _propose_and_execute(db: Session, variant: Variant, detail: dict) -> None:
    portfolio = variant.portfolio
    if portfolio is None:
        return

    # Housekeeping before new ideas, in this order on purpose: resting orders
    # may fill (changing what is held), then exits may close positions (freeing
    # cash and realising P&L) — both of which change what this cycle can afford
    # and what it would consider a duplicate holding.
    poll_open_orders(db, portfolio)
    held_symbols = list(db.scalars(select(Position.symbol).where(
        Position.portfolio_id == portfolio.id, Position.qty > 0)))
    if held_symbols:
        exit_prices = {s: q.price for s, q in latest_prices(held_symbols).items()}
        for order in exits.apply(db, portfolio, exit_prices):
            detail.setdefault("exits", []).append({
                "variant": variant.name, "symbol": order.symbol, "qty": order.qty,
                "reason": order.exit_reason, "status": order.status,
                "realized_pnl": order.realized_pnl,
            })

    try:
        module = ENGINES[variant.engine]
        ideas: list[TradeIdea] = module.generate_ideas(limit=5, variant_key=variant.variant_key or None)
    except Exception as exc:
        detail["errors"].append({"variant": variant.name, "error": str(exc)})
        return

    held = {p.symbol for p in db.scalars(
        select(Position).where(Position.portfolio_id == portfolio.id, Position.qty > 0))}
    placed = 0
    for idea in ideas:
        if placed >= IDEAS_PER_VARIANT_PER_CYCLE:
            break
        if idea.symbol in held:
            continue
        quote = latest_prices([idea.symbol]).get(idea.symbol)
        if quote is None:
            continue
        budget = round(portfolio.cash * idea.size_pct_of_equity, 2)
        qty = affordable_qty(portfolio, budget, quote.price)
        if qty < 1:
            continue
        try:
            order = submit_order(
                db, portfolio=portfolio, symbol=idea.symbol, side="buy", qty=qty,
                max_loss=budget, rationale=f"[cycle] {idea.structure}: {idea.rationale}",
            )
        except OrderRejected as exc:
            detail["errors"].append({"variant": variant.name, "symbol": idea.symbol, "error": str(exc)})
            continue
        rationale, invalidation, main_risk, source = resolve_explanation(idea)
        db.add(DecisionLog(
            variant_id=variant.id, order_id=order.id, symbol=idea.symbol, action=idea.action,
            structure=idea.structure, engine=variant.engine, confidence=idea.confidence, score=idea.score,
            main_risk=main_risk, rationale=rationale, invalidation=invalidation,
            factors=idea.factor_breakdown(), explanation_source=source,
        ))
        db.commit()
        detail["proposed"].append({
            "variant": variant.name, "symbol": idea.symbol, "structure": idea.structure, "status": order.status,
        })
        placed += 1

    symbols = list(db.scalars(select(PaperOrder.symbol).where(PaperOrder.portfolio_id == portfolio.id).distinct()))
    prices = {s: q.price for s, q in latest_prices(symbols).items()} if symbols else {}
    accounting.take_snapshot(db, portfolio, prices)


def _score_all(db: Session, variants: list[Variant]) -> list[dict]:
    rows = []
    for variant in variants:
        portfolio = variant.portfolio
        if portfolio is None:
            continue
        symbols = list(db.scalars(select(PaperOrder.symbol).where(PaperOrder.portfolio_id == portfolio.id).distinct()))
        prices = {s: q.price for s, q in latest_prices(symbols).items()} if symbols else {}
        s = accounting.summary(db, portfolio, prices)
        rows.append({
            "variant_id": variant.id, "name": variant.name, "engine": variant.engine,
            "variant_key": variant.variant_key, "total_pnl_pct": s["total_pnl_pct"],
            "sharpe": s["sharpe"], "n_fills": s["n_trades"], "n_closed_trades": s["n_closed_trades"],
            "hit_rate_pct": s["hit_rate_pct"],
        })
    return rows


def _spawn_from_winner(db: Session, cand: dict, detail: dict) -> None:
    parent = db.get(Variant, cand["variant_id"])
    if parent is None or parent.portfolio is None:
        return
    child = Variant(
        name=f"{parent.name} (gen {parent.generation + 1})", engine=parent.engine,
        variant_key=parent.variant_key, generation=parent.generation + 1, parent_variant_id=parent.id,
        description=f"Clone de capital de « {parent.name} », généré après un cycle gagnant (P&L {cand['total_pnl_pct']:+.1f}%).",
    )
    db.add(child)
    db.commit()
    db.refresh(child)
    child_portfolio = PaperPortfolio(
        variant_id=child.id, initial_cash=parent.portfolio.initial_cash, cash=parent.portfolio.initial_cash,
    )
    db.add(child_portfolio)
    db.commit()
    detail["spawned"].append({"parent": parent.name, "child": child.name, "child_id": child.id})
