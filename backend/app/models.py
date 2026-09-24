"""Persistence for the lab and its paper accounts.

The central object is the **model** (table `variants`, kept for continuity with
the data already on disk): a profile, a set of parameters and a budget, moving
through stages — `lab` (defined, backtested) -> `paper` (trading on Alpaca paper
with its own ledger) -> `retired`. Rows from the old tournament are kept as
`archived` rather than deleted.

A paper model's money lives in its `PaperPortfolio`: a sub-ledger inside the one
shared Alpaca paper account. Every order belongs to exactly one portfolio, so
attribution is exact at the level of fills even though the broker only sees one
pooled position per symbol — and reconciliation checks the two agree."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Variant(Base):
    """A model: profile + parameters + budget, and where it is in its life."""
    __tablename__ = "variants"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    engine: Mapped[str] = mapped_column(String(20), default="flambeur")  # the profile key
    variant_key: Mapped[str] = mapped_column(String(4), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    params: Mapped[dict] = mapped_column(JSON, default=dict)             # overrides on the profile defaults
    budget: Mapped[float] = mapped_column(Float, default=10_000.0)
    stage: Mapped[str] = mapped_column(String(20), default="lab", index=True)  # lab | paper | retired | archived
    parent_variant_id: Mapped[int | None] = mapped_column(ForeignKey("variants.id"), nullable=True)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_decision_on: Mapped[str | None] = mapped_column(String(10), nullable=True)  # ISO session date
    last_rebalance_on: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # Legacy tournament columns, still present on disk; unused by the lab.
    status: Mapped[str] = mapped_column(String(20), default="active")
    generation: Mapped[int] = mapped_column(Integer, default=0)
    scaled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    portfolio: Mapped[PaperPortfolio] = relationship(back_populates="variant", uselist=False)

    @property
    def profile(self) -> str:
        return self.engine


class PaperPortfolio(Base):
    __tablename__ = "paper_portfolios"
    id: Mapped[int] = mapped_column(primary_key=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("variants.id"), unique=True, index=True)
    initial_cash: Mapped[float] = mapped_column(Float, default=100_000.0)
    cash: Mapped[float] = mapped_column(Float, default=100_000.0)
    broker: Mapped[str] = mapped_column(String(20), default="sim")  # sim | alpaca_paper — resolved by app/paper/brokers.py
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    variant: Mapped[Variant] = relationship(back_populates="portfolio")


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (UniqueConstraint("portfolio_id", "symbol", name="uq_portfolio_symbol"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("paper_portfolios.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    qty: Mapped[float] = mapped_column(Float, default=0.0)
    avg_entry_price: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class PaperOrder(Base):
    """Lifecycle: proposed -> open -> filled | partial_fill | cancelled | rejected.

    `open` exists because an order is not always resolved the moment it is
    submitted: a limit that has not crossed yet stays open, and an external
    broker answers asynchronously. The poller (app/monitor/scheduler.py)
    re-evaluates open orders and settles them through the same
    app/paper/settlement.apply_fill as an immediate fill.

    Fill simulation applies slippage and a modeled bid-ask spread, and can
    produce a partial fill — see app/paper/broker.py."""
    __tablename__ = "paper_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("paper_portfolios.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(20))
    side: Mapped[str] = mapped_column(String(4))  # buy | sell
    qty: Mapped[float] = mapped_column(Float)
    order_type: Mapped[str] = mapped_column(String(10), default="market")  # market | limit | stop
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_in_force: Mapped[str] = mapped_column(String(5), default="day")  # day | gtc
    # trade: a decision of the model. safety_stop: the standing catastrophe stop
    # kept at the broker (app/paper/safety.py) — not a pending decision.
    purpose: Mapped[str] = mapped_column(String(20), default="trade", index=True)
    max_loss: Mapped[float | None] = mapped_column(Float, nullable=True)  # required: defined before open
    rationale: Mapped[str] = mapped_column(Text, default="")
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)  # set on sell fills; drives hit-rate/profit-factor
    status: Mapped[str] = mapped_column(String(20), default="proposed", index=True)
    broker: Mapped[str] = mapped_column(String(20), default="sim")
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    exit_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)  # set on sells opened by app/paper/exits.py
    filled_qty: Mapped[float] = mapped_column(Float, default=0.0)
    filled_avg_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    requested_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    slippage_bps: Mapped[float] = mapped_column(Float, default=0.0)
    spread_bps: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("paper_portfolios.id"), index=True)
    equity: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)
    as_of: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class ExecutionEvent(Base):
    """Append-only order lifecycle log — never updated or deleted by application code."""
    __tablename__ = "execution_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("paper_orders.id"), nullable=True, index=True)
    event: Mapped[str] = mapped_column(String(40))  # proposed | filled | partial_fill | rejected | blocked
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)



class SystemState(Base):
    """Single-row system flags — global kill switch."""
    __tablename__ = "system_state"
    id: Mapped[int] = mapped_column(primary_key=True)
    kill_switch_engaged: Mapped[bool] = mapped_column(Boolean, default=False)
    kill_switch_reason: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AppSetting(Base):
    """Runtime-editable settings, edited from the Administration panel.

    Layered over the environment: app/core/settings_store.py reads the env
    default first and lets a row here override it, so a fresh install works
    with no rows at all and nothing here is required to boot. Secrets are
    stored in `value` but never returned in clear text by the API — see
    settings_store.public_view().
    """
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)  # {"v": <any>} — JSON column needs a container
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class BacktestRun(Base):
    """One backtest of one model, frozen with the exact parameters it ran with.

    Parameters are snapshotted rather than read from the model, because the
    model can be edited afterwards and a result must always say what produced
    it."""
    __tablename__ = "backtest_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("variants.id"), index=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    start: Mapped[str] = mapped_column(String(10))
    end: Mapped[str] = mapped_column(String(10))
    budget: Mapped[float] = mapped_column(Float)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    series: Mapped[dict] = mapped_column(JSON, default=dict)    # dates + equity / SPY / placebo
    trades: Mapped[list] = mapped_column(JSON, default=list)    # most recent fills
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class LabEvent(Base):
    """The journal: every decision, order, fill, exit and error of the lab,
    in plain language. Append-only."""
    __tablename__ = "lab_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    variant_id: Mapped[int | None] = mapped_column(ForeignKey("variants.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(20))       # decision | order | fill | exit | error | info | promote
    symbol: Mapped[str] = mapped_column(String(20), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
