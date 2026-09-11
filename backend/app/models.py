"""Phase 1 entities: variants, paper portfolios, positions, orders, snapshots,
execution/audit logs. No AI, no strategy logic yet — variants are created manually
or via seed script; the strategy engines (phase 2+) populate `proposed_by`."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Variant(Base):
    """One tournament participant: a named strategy variant with its own paper portfolio."""
    __tablename__ = "variants"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    engine: Mapped[str] = mapped_column(String(20), default="manual")  # casino | ml | economist | manual
    variant_key: Mapped[str] = mapped_column(String(4), default="")  # A | B | C | D | E — see tournament/variants_config.py
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | killed
    generation: Mapped[int] = mapped_column(Integer, default=0)  # 0 = seeded roster, 1+ = spawned from a winner
    parent_variant_id: Mapped[int | None] = mapped_column(ForeignKey("variants.id"), nullable=True)
    scaled: Mapped[bool] = mapped_column(Boolean, default=False)  # cash boost is a one-time reward,
    # not a recurring one — see app/tournament/cycle.py's run_cycle docstring for the bug this guards
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    portfolio: Mapped[PaperPortfolio] = relationship(back_populates="variant", uselist=False)


class PaperPortfolio(Base):
    __tablename__ = "paper_portfolios"
    id: Mapped[int] = mapped_column(primary_key=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("variants.id"), unique=True, index=True)
    initial_cash: Mapped[float] = mapped_column(Float, default=100_000.0)
    cash: Mapped[float] = mapped_column(Float, default=100_000.0)
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
    """Lifecycle: proposed -> filled | rejected. Fill simulation applies slippage and
    a modeled bid-ask spread, and can produce a partial fill — see app/paper/broker.py."""
    __tablename__ = "paper_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("paper_portfolios.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(20))
    side: Mapped[str] = mapped_column(String(4))  # buy | sell
    qty: Mapped[float] = mapped_column(Float)
    order_type: Mapped[str] = mapped_column(String(10), default="market")  # market | limit
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_loss: Mapped[float | None] = mapped_column(Float, nullable=True)  # required: defined before open
    rationale: Mapped[str] = mapped_column(Text, default="")
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)  # set on sell fills; drives hit-rate/profit-factor
    status: Mapped[str] = mapped_column(String(20), default="proposed", index=True)
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


class DecisionLog(Base):
    """Auditable log of every paper trade decision with its reasoning — the spec's
    'journal des décisions'. Phase 1 rationale is manual/free text; phase 5 fills it
    with agentic explanations."""
    __tablename__ = "decision_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("variants.id"), index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("paper_orders.id"), nullable=True)
    symbol: Mapped[str] = mapped_column(String(20))
    action: Mapped[str] = mapped_column(String(10))  # buy | sell | hold
    structure: Mapped[str] = mapped_column(String(30), default="")  # long_call | debit_spread_call | ...
    engine: Mapped[str] = mapped_column(String(20), default="manual")  # casino | ml | economist | manual
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    main_risk: Mapped[str] = mapped_column(Text, default="")
    rationale: Mapped[str] = mapped_column(Text, default="")
    invalidation: Mapped[str] = mapped_column(Text, default="")  # what would break the thesis
    factors: Mapped[list] = mapped_column(JSON, default=list)  # factor breakdown, real vs proxy tagged
    explanation_source: Mapped[str] = mapped_column(String(20), default="template")  # llm | template
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SystemState(Base):
    """Single-row system flags — global kill switch."""
    __tablename__ = "system_state"
    id: Mapped[int] = mapped_column(primary_key=True)
    kill_switch_engaged: Mapped[bool] = mapped_column(Boolean, default=False)
    kill_switch_reason: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class MarketDataCache(Base):
    """Latest quote snapshot per symbol, refreshed on demand."""
    __tablename__ = "market_data_cache"
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    price: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(20), default="yfinance")  # yfinance | mock
    as_of: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CycleRun(Base):
    """One tournament cycle: propose -> execute -> score -> kill weak -> scale
    strong -> spawn new variants from winners. Append-only audit trail so the
    kill/scale/spawn decisions are as inspectable as any individual trade."""
    __tablename__ = "cycle_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | done | failed
    summary: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)  # per-variant actions taken, ranked leaderboard snapshot
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
