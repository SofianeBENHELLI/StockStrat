"""What the whole account holds, across every model.

Each model is shown on its own, which hides the account's real risk: several
models can bet on the same thing. On the first night of paper trading, five of
the nine models together put 39% of the committed capital into
semiconductors — nothing on any single model's page showed it.

This sums held positions (marked to market) and pending buys (at the price they
were sized on) by symbol and by sector, and flags any sector above the alert
threshold. It reports; it does not block — an account-level cap would make a
model's paper record differ from its backtest for reasons outside the model.
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import settings_store
from app.lab import data as market
from app.lab.universe import label, sector
from app.models import PaperOrder, PaperPortfolio, Position, Variant


def account_exposure(db: Session, prices: dict[str, float] | None = None) -> dict:
    models = {v.id: v for v in db.scalars(select(Variant).where(Variant.stage.in_(("paper", "retired"))))}
    portfolios = [v.portfolio for v in models.values() if v.portfolio is not None]
    ids = [p.id for p in portfolios]
    owner = {p.id: models[p.variant_id].name for p in portfolios}
    if not ids:
        return {"total": 0.0, "symbols": [], "sectors": [], "alerts": [], "threshold_pct": 0}

    positions = list(db.scalars(select(Position).where(Position.portfolio_id.in_(ids), Position.qty > 0)))
    pending = list(db.scalars(select(PaperOrder).where(
        PaperOrder.portfolio_id.in_(ids), PaperOrder.status == "open", PaperOrder.side == "buy",
        PaperOrder.purpose == "trade")))
    if prices is None:
        try:
            prices = market.latest_prices(sorted({p.symbol for p in positions})) if positions else {}
        except market.DataUnavailable:
            prices = {}

    by_symbol: dict[str, dict] = defaultdict(lambda: {"held": 0.0, "pending": 0.0, "models": set()})
    for p in positions:
        row = by_symbol[p.symbol]
        row["held"] += p.qty * prices.get(p.symbol, p.avg_entry_price)
        row["models"].add(owner[p.portfolio_id])
    for o in pending:
        row = by_symbol[o.symbol]
        row["pending"] += o.qty * (o.requested_price or 0)
        row["models"].add(owner[o.portfolio_id])

    total = sum(r["held"] + r["pending"] for r in by_symbol.values())
    symbols = sorted(
        ({"symbol": s, "label": label(s), "sector": sector(s), "held": round(r["held"], 2),
          "pending": round(r["pending"], 2), "value": round(r["held"] + r["pending"], 2),
          "pct": round((r["held"] + r["pending"]) / total * 100, 1) if total else 0.0,
          "models": sorted(r["models"])} for s, r in by_symbol.items()),
        key=lambda x: -x["value"])

    sectors: dict[str, dict] = defaultdict(lambda: {"value": 0.0, "symbols": 0, "models": set()})
    for row in symbols:
        agg = sectors[row["sector"]]
        agg["value"] += row["value"]
        agg["symbols"] += 1
        agg["models"].update(row["models"])
    threshold = float(settings_store.resolve(db, "risk.sector_alert_pct"))
    sector_rows = sorted(
        ({"sector": name, "value": round(a["value"], 2), "pct": round(a["value"] / total * 100, 1) if total else 0.0,
          "symbols": a["symbols"], "models": len(a["models"])} for name, a in sectors.items()),
        key=lambda x: -x["value"])
    alerts = [s for s in sector_rows if s["pct"] > threshold]
    return {"total": round(total, 2), "symbols": symbols, "sectors": sector_rows, "alerts": alerts,
            "threshold_pct": threshold}
