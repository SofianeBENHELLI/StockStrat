"""Exit rules — when an open position gets closed.

Why this module exists: before it, nothing in the application ever sold. Every
cycle bought, positions only accumulated, and the consequences were not
cosmetic:

- `PaperOrder.realized_pnl` was never set, so hit rate and profit factor were
  permanently null on every variant;
- the tournament's kill/scale step (app/tournament/cycle.py) gates on a minimum
  number of *closed* trades, which stayed at zero forever, so weak variants
  were never killed and strong ones never scaled;
- cash only ever went down, so a variant eventually stopped being able to
  trade at all.

The rules are intentionally boring — a stop, a target, and a clock. They are
not a strategy and do not try to be: the three engines decide what to buy, and
this decides only when to stop holding it. Anything cleverer belongs in the
engines, where it can be backtested.

Thresholds come from the Administration panel (app/core/settings_store.py), so
they can be tuned without a deploy, and the reason for every exit is recorded
on the order (`PaperOrder.exit_reason`) so the decision log stays auditable.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import settings_store
from app.models import PaperOrder, PaperPortfolio, Position
from app.paper.service import OrderRejected, submit_order

STOP_LOSS = "stop_loss"
TAKE_PROFIT = "take_profit"
TIME_STOP = "time_stop"


@dataclass(frozen=True)
class ExitRules:
    enabled: bool
    stop_loss_pct: float
    take_profit_pct: float
    max_holding_days: int

    @classmethod
    def from_settings(cls, db: Session) -> ExitRules:
        v = settings_store.resolve_all(db)
        return cls(
            enabled=bool(v["trading.exits_enabled"]),
            stop_loss_pct=float(v["trading.exit_stop_loss_pct"]),
            take_profit_pct=float(v["trading.exit_take_profit_pct"]),
            max_holding_days=int(v["trading.exit_max_holding_days"]),
        )


@dataclass(frozen=True)
class ExitDecision:
    symbol: str
    qty: float
    reason: str
    detail: str


def _aware(dt: datetime) -> datetime:
    """Snapshot timestamps are stored naive-UTC by SQLite; make them comparable
    without pretending they are local time."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def evaluate(
    db: Session, portfolio: PaperPortfolio, prices: dict[str, float],
    rules: ExitRules | None = None, now: datetime | None = None,
) -> list[ExitDecision]:
    """Pure decision step: which positions should be closed, and why. Takes no
    action, so it can be unit-tested and previewed in the UI."""
    rules = rules or ExitRules.from_settings(db)
    if not rules.enabled:
        return []
    now = now or datetime.now(timezone.utc)

    decisions: list[ExitDecision] = []
    positions = db.scalars(select(Position).where(
        Position.portfolio_id == portfolio.id, Position.qty > 0))

    for pos in positions:
        price = prices.get(pos.symbol)
        if price is None or pos.avg_entry_price <= 0:
            # No fresh price means no honest P&L to judge; leave it alone and
            # let the next pass decide rather than exiting on a stale number.
            continue
        pnl_pct = (price / pos.avg_entry_price - 1) * 100
        held_days = (now - _aware(pos.opened_at)).days

        if pnl_pct <= rules.stop_loss_pct:
            reason, detail = STOP_LOSS, f"{pnl_pct:+.2f}% <= {rules.stop_loss_pct:+.2f}%"
        elif pnl_pct >= rules.take_profit_pct:
            reason, detail = TAKE_PROFIT, f"{pnl_pct:+.2f}% >= {rules.take_profit_pct:+.2f}%"
        elif held_days >= rules.max_holding_days:
            reason, detail = TIME_STOP, f"held {held_days}d >= {rules.max_holding_days}d"
        else:
            continue
        decisions.append(ExitDecision(symbol=pos.symbol, qty=pos.qty, reason=reason, detail=detail))

    return decisions


def apply(
    db: Session, portfolio: PaperPortfolio, prices: dict[str, float],
    rules: ExitRules | None = None, now: datetime | None = None,
) -> list[PaperOrder]:
    """Evaluate and submit the resulting sells. A rejected exit is skipped, not
    raised: one position that cannot be closed must not stop the others."""
    orders = []
    for decision in evaluate(db, portfolio, prices, rules=rules, now=now):
        try:
            order = submit_order(
                db, portfolio=portfolio, symbol=decision.symbol, side="sell", qty=decision.qty,
                rationale=f"[exit:{decision.reason}] {decision.detail}",
                exit_reason=decision.reason,
            )
        except OrderRejected:
            continue
        orders.append(order)
    return orders
