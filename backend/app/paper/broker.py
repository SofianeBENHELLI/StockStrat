"""Paper fill simulator. This is the piece the spec calls out by name as a blind
spot to avoid: a paper broker that always fills 100% at the quoted price produces
paper results that don't resemble live trading. So every fill here goes through:

  1. a modeled bid-ask spread (widens for low-priced/thin names),
  2. slippage on top of the spread-adjusted price (worse for market orders,
     scaled by order size),
  3. a chance of a partial fill on larger orders.

Deterministic given (symbol, qty, side) so paper results are reproducible for
backtesting/comparison — driven by a seeded hash, not real randomness.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass
class FillResult:
    status: str  # filled | partial_fill | rejected
    filled_qty: float
    filled_avg_price: float | None
    spread_bps: float
    slippage_bps: float
    detail: str = ""


def _deterministic_unit(seed: str) -> float:
    """Stable pseudo-random float in [0, 1) derived from a seed string."""
    digest = hashlib.sha256(seed.encode()).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def simulate_fill(
    *, symbol: str, side: str, qty: float, order_type: str,
    limit_price: float | None, market_price: float | None, order_id: int,
    unfilled_limit_status: str = "rejected",
    allow_partial: bool = True,
) -> FillResult:
    """`unfilled_limit_status` is what a limit order that has not crossed yet
    resolves to. It defaults to "rejected", which is the original single-shot
    behaviour the historical backtest relies on (app/backtest/engine.py places
    market orders only, so it never reaches this path). The live path passes
    "open" instead, leaving the order resting for the poller to re-evaluate
    against a fresh price — see app/paper/brokers.py."""
    if market_price is None or market_price <= 0:
        return FillResult("rejected", 0.0, None, 0.0, 0.0, detail="no market price available")
    if qty <= 0:
        return FillResult("rejected", 0.0, None, 0.0, 0.0, detail="quantity must be positive")

    seed = f"{symbol}:{order_id}:{side}:{qty}"
    rand = _deterministic_unit(seed)

    # spread widens for cheap/thin names as a rough liquidity proxy
    spread_bps = 5.0 + (1.0 / max(market_price, 1.0)) * 200.0
    spread_bps = min(spread_bps, 80.0)
    half_spread = market_price * (spread_bps / 2 / 10_000)
    quoted_price = market_price + half_spread if side == "buy" else market_price - half_spread

    if order_type == "limit":
        if limit_price is None:
            return FillResult("rejected", 0.0, None, 0.0, 0.0, detail="limit order without limit price")
        crosses = quoted_price <= limit_price if side == "buy" else quoted_price >= limit_price
        if not crosses:
            return FillResult(unfilled_limit_status, 0.0, None, spread_bps, 0.0, detail="limit not reached")

    # slippage: bigger orders move the price more against the trader
    size_factor = min(qty / 500.0, 1.0)
    slippage_bps = 2.0 + rand * 8.0 + size_factor * 15.0
    slip = quoted_price * (slippage_bps / 10_000)
    fill_price = quoted_price + slip if side == "buy" else quoted_price - slip
    fill_price = round(max(fill_price, 0.01), 4)

    # partial fill chance grows with size, capped so small orders (the common case) always fill
    partial_threshold = 0.85 - size_factor * 0.35
    if allow_partial and rand > partial_threshold:
        fill_pct = 0.3 + (1 - rand) * 0.6
        filled_qty = round(qty * fill_pct, 4)
        return FillResult("partial_fill", filled_qty, fill_price, spread_bps, slippage_bps,
                          detail=f"partial: {filled_qty}/{qty} filled")

    return FillResult("filled", qty, fill_price, spread_bps, slippage_bps)
