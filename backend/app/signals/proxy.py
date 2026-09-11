"""Signal families the app has no real feed for yet: news, macro, fundamentals,
and options-chain microstructure (spread, OI, skew — realized-vol percentile is
used as an IV-rank proxy in market.py since it's at least derived from real
prices). Every value here is a deterministic pseudo-random function of the
symbol, tagged is_proxy=True everywhere it surfaces — the UI and the strategy
engines must show these differently from real signals, never blend them in
silently. Swap this module for a real news/macro/options provider without
touching the strategy engines: same function signatures, same output shape."""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone


def _unit(seed: str) -> float:
    digest = hashlib.sha256(seed.encode()).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def _signed_unit(seed: str) -> float:
    return _unit(seed) * 2 - 1


def news_signals(symbol: str) -> dict:
    return {
        "source": "proxy",
        "sentiment": round(_signed_unit(f"{symbol}:news_sentiment"), 3),  # -1..1
        "novelty": round(_unit(f"{symbol}:news_novelty"), 3),  # 0..1, higher = fresher
        "urgency": round(_unit(f"{symbol}:news_urgency"), 3),  # 0..1
        "source_quality": round(_unit(f"{symbol}:news_quality"), 3),  # 0..1
    }


def catalyst_signals(symbol: str) -> dict:
    """Imminent-catalyst proxy for the Casino engine (earnings, FDA, M&A rumor...).
    days_until in [1, 15] mimics the spec's catalyst window; strength in 0..1."""
    days = 1 + int(_unit(f"{symbol}:catalyst_days") * 14)
    return {
        "source": "proxy",
        "catalyst_strength": round(_unit(f"{symbol}:catalyst_strength"), 3),
        "catalyst_days_until": days,
        "short_squeeze_score": round(_unit(f"{symbol}:squeeze"), 3),
    }


def options_microstructure(symbol: str) -> dict:
    """OI, bid-ask spread, skew — the pieces of the options signal family that
    genuinely require a live chain. realized_vol_percentile (see market.py) is
    used as the IV-rank stand-in since that part is at least price-derived."""
    return {
        "source": "proxy",
        "open_interest_score": round(_unit(f"{symbol}:oi"), 3),  # 0..1, higher = more liquid
        "bid_ask_spread_bps": round(5 + _unit(f"{symbol}:spread") * 45, 1),
        "skew": round(_signed_unit(f"{symbol}:skew"), 3),
    }


def fundamentals_signals(symbol: str) -> dict:
    return {
        "source": "proxy",
        "quality_score": round(_unit(f"{symbol}:fundamentals_quality"), 3),  # margins/FCF/debt composite
        "revisions_trend": round(_signed_unit(f"{symbol}:revisions"), 3),  # -1..1
        "valuation_richness": round(_unit(f"{symbol}:valuation"), 3),  # 0..1, higher = more expensive
        "pricing_power": round(_unit(f"{symbol}:pricing_power"), 3),
    }


def macro_backdrop(as_of: date | None = None) -> dict:
    """Market-wide macro regime proxy (rates/inflation/liquidity/dollar composite
    in the real spec) — no FRED feed wired up yet, so this is a single score that
    drifts by calendar day rather than per-symbol noise. Stands in until
    app/data/macro.py exists. `as_of` lets phase 4's historical training samples
    compute the regime as it would have been on a past date, instead of always
    today's."""
    day = (as_of if as_of is not None else datetime.now(timezone.utc).date()).isoformat()
    return {"source": "proxy", "regime_score": round(_unit(f"macro:{day}"), 3)}  # 0..1, higher = more supportive


def macro_sensitivity(symbol: str) -> float:
    """0..1 proxy for how exposed a name is to macro swings (rate-sensitive
    growth vs. defensive staples, roughly)."""
    return round(_unit(f"{symbol}:macro_sensitivity"), 3)


def macro_theme_alignment(symbol: str, theme: str) -> float:
    """0..1 — how exposed `symbol` is to `theme`'s revenue thesis. In this proxy,
    membership in the theme's curated universe (see app/strategies/economist.py)
    already gates candidacy, so alignment is high-and-stable rather than a fake
    fine-grained score."""
    return round(0.75 + _unit(f"{symbol}:{theme}:alignment") * 0.25, 3)
