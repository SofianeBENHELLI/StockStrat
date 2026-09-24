"""The feedback loop: what paper trading teaches about the backtest.

Two measurements, both in the units the owner judges by.

**Execution cost.** For every filled order, the gap between the price the
decision was sized on (the quote at submission) and the price actually
obtained, signed so that a cost is positive: paying more on a buy, receiving
less on a sell. This is the number every backtest *assumed* (`cost_bps`, 5
basis points by default) and nobody had measured. The Flambeur's ten-year
result swings from +12,700 $ to -5,000 $ between 0 and 20 bp, so this single
measurement decides whether that model is worth anything.

Orders submitted outside the regular session are kept apart: they fill at the
next open, so their gap includes the overnight move — real, but not an
execution cost, and averaging it in would drown the signal in market noise.

**Reality gap.** Paper P&L against the backtest replayed on the same days with
the same code, and how much of the difference the measured execution cost
explains. What remains is timing and data (the live decision runs a few
minutes before the close on a projected volume, the backtest on the close).

Suggestions are just that: facts with a proposed action. Nothing here changes
a model — parameters of a paper model are frozen by design.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from statistics import median
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.lab.profiles import get_profile
from app.models import PaperOrder, PaperPortfolio, Variant

NY = ZoneInfo("America/New_York")
MIN_FILLS = 10          # below this, a cost estimate is noise
GAP_ALERT_PCT = 2.0     # paper behind its replay by more than 2% of the budget


def in_session(at: datetime) -> bool:
    """Regular US session, weekdays 9:30-16:00 New York (holidays not modelled:
    no order is submitted on one, the app decides only when Alpaca says open)."""
    ny = (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).astimezone(NY)
    return ny.weekday() < 5 and time(9, 30) <= ny.time() < time(16, 0)


def order_cost_bps(o: PaperOrder) -> float | None:
    if not o.requested_price or not o.filled_avg_price:
        return None
    diff = (o.filled_avg_price - o.requested_price) / o.requested_price * 10_000
    return diff if o.side == "buy" else -diff


def execution_costs(db: Session, portfolio_ids: list[int] | None = None) -> dict:
    q = select(PaperOrder).where(PaperOrder.status.in_(("filled", "partial_fill")),
                                 PaperOrder.purpose == "trade", PaperOrder.broker != "sim")
    if portfolio_ids is not None:
        q = q.where(PaperOrder.portfolio_id.in_(portfolio_ids))
    rows = []
    for o in db.scalars(q):
        bps = order_cost_bps(o)
        if bps is None:
            continue
        rows.append({"order": o, "bps": bps, "in_session": in_session(o.created_at),
                     "usd": bps / 10_000 * o.filled_qty * o.filled_avg_price})

    def stats(sample: list[dict]) -> dict:
        if not sample:
            return {"n": 0, "avg_bps": None, "median_bps": None, "weighted_bps": None, "cost_usd": 0.0}
        notional = sum(r["order"].filled_qty * r["order"].filled_avg_price for r in sample)
        return {
            "n": len(sample),
            "avg_bps": round(sum(r["bps"] for r in sample) / len(sample), 2),
            "median_bps": round(median(r["bps"] for r in sample), 2),
            "weighted_bps": round(sum(r["usd"] for r in sample) / notional * 10_000, 2) if notional else None,
            "cost_usd": round(sum(r["usd"] for r in sample), 2),
        }

    session = [r for r in rows if r["in_session"]]
    overnight = [r for r in rows if not r["in_session"]]
    return {
        "in_session": stats(session),
        "overnight": stats(overnight),
        "recent": [{"id": r["order"].id, "symbol": r["order"].symbol, "side": r["order"].side,
                    "bps": round(r["bps"], 1), "usd": round(r["usd"], 2), "in_session": r["in_session"],
                    "at": r["order"].created_at.isoformat()} for r in sorted(
                        rows, key=lambda r: r["order"].created_at, reverse=True)[:15]],
    }


def model_feedback(db: Session, m: Variant, replay: dict | None = None, paper_pnl: float | None = None) -> dict:
    """Everything the paper record says about one model, with suggestions."""
    portfolio: PaperPortfolio | None = m.portfolio
    if portfolio is None:
        return {"available": False}
    assumed = float(get_profile(m.engine).resolve(m.params)["cost_bps"])
    costs = execution_costs(db, [portfolio.id])
    measured = costs["in_session"]["weighted_bps"]

    out: dict = {"available": True, "assumed_cost_bps": assumed, "costs": costs, "suggestions": []}
    if replay and replay.get("available") and paper_pnl is not None:
        gap = paper_pnl - float(replay["pnl_usd"])
        explained = -(costs["in_session"]["cost_usd"] + costs["overnight"]["cost_usd"])
        out["gap"] = {"paper_pnl_usd": round(paper_pnl, 2), "replay_pnl_usd": float(replay["pnl_usd"]),
                      "gap_usd": round(gap, 2), "execution_usd": round(explained, 2),
                      "other_usd": round(gap - explained, 2)}
        if gap < -GAP_ALERT_PCT / 100 * portfolio.initial_cash:
            out["suggestions"].append({
                "level": "warning",
                "text": f"Le paper est en retard de {abs(gap):,.0f} $ sur son backtest rejoué sur les mêmes jours. "
                        f"Le coût d'exécution en explique {abs(min(explained, 0)):,.0f} $ ; le reste vient du "
                        f"moment de décision et des données. À surveiller avant toute conclusion.".replace(",", " ")})

    n = costs["in_session"]["n"]
    if measured is not None and n >= MIN_FILLS:
        if measured > assumed + 2:
            out["suggestions"].append({
                "level": "action",
                "text": f"Coût d'exécution mesuré : {measured:.1f} pb par ordre sur {n} ordres, contre {assumed:.0f} pb "
                        f"supposés dans le backtest. Clonez le modèle au labo avec « Coût par transaction » à "
                        f"{measured:.0f} pb et re-backtestez : c'est son vrai résultat.",
                "suggested_cost_bps": round(measured)})
        elif measured < assumed - 2:
            out["suggestions"].append({
                "level": "info",
                "text": f"L'exécution coûte moins que prévu ({measured:.1f} pb mesurés contre {assumed:.0f} supposés) : "
                        f"le backtest est prudent sur ce point."})
    elif n < MIN_FILLS:
        out["suggestions"].append({
            "level": "info",
            "text": f"{n} ordre{'s' if n > 1 else ''} exécuté{'s' if n > 1 else ''} en séance pour l'instant : il en "
                    f"faut au moins {MIN_FILLS} pour estimer le coût réel."})
    return out
