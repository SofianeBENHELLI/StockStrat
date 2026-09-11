from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.backtest.engine import run_backtest

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


class BacktestRequest(BaseModel):
    # Defaults measured at ~3 min end-to-end — see app/backtest/engine.py's
    # run_backtest docstring before raising years or lowering the cadence.
    years: int = Field(default=1, ge=1, le=5)
    rebalance_every_days: int = Field(default=10, ge=5, le=30)


@router.post("/run")
def run(body: BacktestRequest) -> dict:
    return run_backtest(years=body.years, rebalance_every_days=body.rebalance_every_days)
