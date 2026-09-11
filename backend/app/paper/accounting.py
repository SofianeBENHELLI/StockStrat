"""Portfolio valuation, P&L and risk-adjusted metrics — deterministic pandas/numpy
math, the numbers shown on the tournament dashboard and used to rank variants.
The actual Sharpe/Sortino/drawdown/hit-rate formulas live in app/paper/metrics.py
(pure functions, no DB) — shared with the historical backtest engine so both
compute risk metrics identically."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PaperOrder, PaperPortfolio, Position, PortfolioSnapshot
from app.paper import metrics


def positions_with_prices(db: Session, portfolio: PaperPortfolio, prices: dict[str, float]) -> list[dict]:
    out = []
    for p in db.scalars(select(Position).where(Position.portfolio_id == portfolio.id, Position.qty > 0)):
        price = prices.get(p.symbol, p.avg_entry_price)
        value = p.qty * price
        unrealized = (price - p.avg_entry_price) * p.qty
        out.append({
            "symbol": p.symbol, "qty": p.qty, "avg_entry_price": p.avg_entry_price,
            "price": price, "value": value, "unrealized_pnl": unrealized,
            "unrealized_pnl_pct": (price / p.avg_entry_price - 1) * 100 if p.avg_entry_price else 0.0,
            "realized_pnl": p.realized_pnl, "opened_at": p.opened_at.isoformat(),
        })
    return sorted(out, key=lambda x: -x["value"])


def summary(db: Session, portfolio: PaperPortfolio, prices: dict[str, float]) -> dict:
    pos = positions_with_prices(db, portfolio, prices)
    positions_value = sum(p["value"] for p in pos)
    equity = portfolio.cash + positions_value
    unrealized = sum(p["unrealized_pnl"] for p in pos)
    realized = sum(p["realized_pnl"] for p in pos) + _closed_realized(db, portfolio)
    total_pnl = equity - portfolio.initial_cash

    snaps = db.scalars(
        select(PortfolioSnapshot).where(PortfolioSnapshot.portfolio_id == portfolio.id)
        .order_by(PortfolioSnapshot.as_of)
    ).all()
    equities = [s.equity for s in snaps] + [equity]
    peak = max(equities) if equities else equity
    drawdown_pct = (peak - equity) / peak * 100 if peak > 0 else 0.0
    max_dd_pct = metrics.max_drawdown_pct(equities)

    dated_equities = [(s.as_of.date().isoformat(), s.equity) for s in snaps]
    daily_rets = metrics.daily_returns(dated_equities)
    vol_pct = metrics.volatility_pct(daily_rets)
    sharpe = metrics.sharpe_ratio(daily_rets)
    sortino = metrics.sortino_ratio(daily_rets)

    orders = db.scalars(select(PaperOrder).where(PaperOrder.portfolio_id == portfolio.id)).all()
    filled = [o for o in orders if o.status in ("filled", "partial_fill")]
    closed_trades = [o.realized_pnl for o in orders if o.realized_pnl is not None]
    avg_win, avg_loss = metrics.avg_win_loss(closed_trades)

    return {
        "portfolio_id": portfolio.id, "variant_id": portfolio.variant_id,
        "cash": portfolio.cash, "positions_value": positions_value, "equity": equity,
        "initial_cash": portfolio.initial_cash,
        "total_pnl": total_pnl, "total_pnl_pct": total_pnl / portfolio.initial_cash * 100 if portfolio.initial_cash else 0.0,
        "unrealized_pnl": unrealized, "realized_pnl": realized,
        "drawdown_pct": drawdown_pct, "max_drawdown_pct": max_dd_pct,
        "volatility_pct": vol_pct, "sharpe": sharpe, "sortino": sortino,
        "n_positions": len(pos), "n_trades": len(filled), "n_closed_trades": len(closed_trades),
        "hit_rate_pct": metrics.hit_rate_pct(closed_trades),
        "avg_win": avg_win, "avg_loss": avg_loss,
        "profit_factor": metrics.profit_factor(closed_trades),
        "positions": pos,
    }


def equity_history(db: Session, portfolio: PaperPortfolio) -> dict:
    snaps = db.scalars(
        select(PortfolioSnapshot).where(PortfolioSnapshot.portfolio_id == portfolio.id)
        .order_by(PortfolioSnapshot.as_of)
    ).all()
    return {"dates": [s.as_of.isoformat() for s in snaps], "equity": [s.equity for s in snaps]}


def take_snapshot(db: Session, portfolio: PaperPortfolio, prices: dict[str, float]) -> PortfolioSnapshot:
    s = summary(db, portfolio, prices)
    snap = PortfolioSnapshot(portfolio_id=portfolio.id, equity=s["equity"], cash=portfolio.cash)
    db.add(snap)
    db.commit()
    return snap


def _closed_realized(db: Session, portfolio: PaperPortfolio) -> float:
    closed = db.scalars(select(Position).where(Position.portfolio_id == portfolio.id, Position.qty <= 0)).all()
    return sum(p.realized_pnl for p in closed)
