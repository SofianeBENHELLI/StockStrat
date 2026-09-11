"""Shared types for the strategy engines. A TradeIdea is layer 2+3 output: WHICH
stock (selection) and HOW to express the view (structure), never an order by
itself — it still goes through the paper trading engine's own guardrails
(max loss, kill switch) before anything is filled. See app/routers/strategies.py
for the wiring from idea -> paper order.

V1 has no live options chain, so `structure` is a decision-layer label (what a
human/future options-execution layer WOULD trade) while the paper fill itself
happens on the underlying equity, sized so the equity notional matches the
idea's max-loss budget. This keeps the layer-3 reasoning (structure choice
driven by IV/direction/horizon) real and auditable without pretending we have
options contract data we don't."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FactorScore:
    name: str
    value: float  # 0..1 after normalization, used in the weighted score
    weight: float
    is_proxy: bool
    raw: float | dict | None = None  # the underlying signal value(s), for display


@dataclass
class TradeIdea:
    symbol: str
    engine: str  # casino | ml | economist
    action: str  # buy | hold
    score: float  # 0..100, weighted composite
    confidence: float  # 0..1
    structure: str  # long_call | long_put | debit_spread_call | debit_spread_put |
                     # straddle | strangle | calendar_spread | leaps_call | collar
    direction: str  # bullish | bearish | neutral_vol
    size_pct_of_equity: float  # 0.005..0.02 typical, applied to the paper portfolio's equity
    rationale: str
    invalidation: str
    main_risk: str
    factors: list[FactorScore] = field(default_factory=list)
    predicted_move_pct: float | None = None  # ML engine: 20d predicted move
    expected_vol_pct: float | None = None

    def factor_breakdown(self) -> list[dict]:
        return [
            {"name": f.name, "value": f.value, "weight": f.weight, "is_proxy": f.is_proxy, "raw": f.raw}
            for f in self.factors
        ]


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def normalize_pct(value: float, lo: float, hi: float) -> float:
    """Map a raw percentage-ish value into 0..1 given an expected [lo, hi] range."""
    if hi == lo:
        return 0.5
    return clamp((value - lo) / (hi - lo), 0.0, 1.0)


def apply_weight_overrides(factors: list[FactorScore], overrides: dict[str, float]) -> list[FactorScore]:
    """Replace a factor's weight where the variant config names it; factors not
    named keep their engine-default weight. Used to turn one engine into several
    genuinely different tournament variants without duplicating scoring code."""
    if not overrides:
        return factors
    return [
        FactorScore(f.name, f.value, overrides.get(f.name, f.weight), f.is_proxy, f.raw)
        for f in factors
    ]


def weighted_score(factors: list[FactorScore]) -> float:
    total_weight = sum(f.weight for f in factors)
    if total_weight == 0:
        return 0.0
    return round(sum(f.value * f.weight for f in factors) / total_weight * 100, 1)


def size_from_confidence(confidence: float, lo_pct: float = 0.005, hi_pct: float = 0.02) -> float:
    """Casino's spec-mandated 0.5-2% of paper equity, scaled by confidence."""
    return round(lo_pct + confidence * (hi_pct - lo_pct), 4)
