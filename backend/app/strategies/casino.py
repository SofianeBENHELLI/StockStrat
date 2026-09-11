"""Casino engine: opportunistic, short-term, asymmetric payoff. Earnings, product
announcements, FDA/regulatory decisions, M&A rumors, short squeezes, momentum
breakouts, unusual options activity, news shocks, IV expansion.

V1 has no earnings-calendar/news/options-chain feed, so catalyst timing,
liquidity and IV-rank inputs are proxies (app/signals/proxy.py) — everything
volume- and price-derived is real (app/signals/market.py). Every idea's factor
breakdown says which is which; nothing here silently presents a proxy as real.

Risk rules enforced here, mirroring the spec's non-negotiables:
- max loss defined before the trade (size_pct_of_equity caps the paper notional;
  the paper trading engine still requires max_loss on every buy — see
  app/paper/service.py)
- no naked short options (V1 structures are long-premium only: long call/put,
  debit spreads, straddle/strangle, calendar — never a short leg alone)
- skip names with wide spreads or thin open interest
- position size capped 0.5-2% of paper equity, scaled by confidence

NOT enforced yet: 'no overexposure to the same event type' — that needs an
event-type feed (earnings vs FDA vs M&A...) this app doesn't have. Flagged
here rather than faked.
"""
from __future__ import annotations

from datetime import date

from app.data.provider import PriceHistory, price_history
from app.signals.market import market_signals
from app.signals.proxy import catalyst_signals, news_signals, options_microstructure
from app.strategies.base import (
    FactorScore, TradeIdea, apply_weight_overrides, clamp, normalize_pct, size_from_confidence, weighted_score,
)
from app.tournament.variants_config import VariantConfig, get_variant_config

UNIVERSE = [
    "TSLA", "NVDA", "AMD", "COIN", "PLTR", "SMCI", "MSTR", "RIVN",
    "NFLX", "UBER", "SNAP", "ROKU", "MARA", "SOFI", "AFRM", "DKNG",
]

MIN_VOLUME_RATIO = 2.0
MAX_SPREAD_BPS = 40.0
MIN_LIQUIDITY_SCORE = 0.3
MAX_IDEAS = 5


def generate_ideas(
    limit: int = MAX_IDEAS, variant_key: str | None = None,
    histories: dict[str, PriceHistory] | None = None, as_of: date | None = None,
) -> list[TradeIdea]:
    """`histories`/`as_of` let a caller (the backtest engine) evaluate this
    engine as of a past date instead of today — omit both for live/current
    behavior, unchanged from before. `market_signals` and the proxy signal
    functions are already pure functions of whatever `hist.df` they're given
    (no hidden "today" dependency), so point-in-time correctness only needs
    the caller to pass in histories already truncated to `as_of`; `as_of`
    itself isn't consumed here (this engine has no calendar-dependent
    signal) but is accepted for a uniform calling convention across engines."""
    variant: VariantConfig | None = get_variant_config("casino", variant_key) if variant_key else None
    histories = histories if histories is not None else price_history(UNIVERSE, days=260)
    ideas: list[TradeIdea] = []

    for symbol in UNIVERSE:
        hist = histories.get(symbol)
        if hist is None or len(hist.df) < 30:
            continue
        m = market_signals(hist.df)
        cat = catalyst_signals(symbol)
        opt = options_microstructure(symbol)
        news = news_signals(symbol)

        volume_ratio = m.get("volume_ratio_vs_20d_avg", 0.0)
        if volume_ratio < MIN_VOLUME_RATIO:
            continue
        if opt["bid_ask_spread_bps"] > MAX_SPREAD_BPS or opt["open_interest_score"] < MIN_LIQUIDITY_SCORE:
            continue
        if not (1 <= cat["catalyst_days_until"] <= 15):
            continue

        momentum = m.get("momentum_20d_pct", 0.0)
        iv_rank = m.get("realized_vol_percentile", 50.0)
        news_shock = abs(news["sentiment"]) * news["urgency"]
        bullish = momentum >= 0

        # Sub-variant direction filters (spec's named sub-strategies, e.g. Casino D
        # is a bearish reversal play — skip candidates that don't fit the thesis).
        if variant and variant.direction_filter == "bullish" and not bullish:
            continue
        if variant and variant.direction_filter == "bearish" and news["sentiment"] >= 0:
            continue

        factors = [
            FactorScore("catalyst_strength", cat["catalyst_strength"], 0.25, True, cat["catalyst_strength"]),
            FactorScore("abnormal_volume", normalize_pct(volume_ratio, 2.0, 6.0), 0.20, False, volume_ratio),
            FactorScore("price_momentum", normalize_pct(abs(momentum), 0, 20), 0.15, False, momentum),
            FactorScore("options_liquidity", opt["open_interest_score"], 0.15, True, opt),
            FactorScore("iv_opportunity", normalize_pct(iv_rank, 40, 100), 0.10, False, iv_rank),
            FactorScore("news_shock", clamp(news_shock, 0, 1), 0.10, True, news),
            FactorScore("short_squeeze", cat["short_squeeze_score"], 0.05, True, cat["short_squeeze_score"]),
        ]
        if variant:
            factors = apply_weight_overrides(factors, variant.weight_overrides)
        score = weighted_score(factors)
        confidence = score / 100

        structure, direction = _pick_structure(
            cat["catalyst_days_until"], iv_rank, momentum, bullish,
            structure_hint=variant.structure_hint if variant else None,
            forced_bearish=bool(variant and variant.direction_filter == "bearish"),
        )

        ideas.append(TradeIdea(
            symbol=symbol, engine="casino", action="buy", score=score, confidence=confidence,
            structure=structure, direction=direction,
            size_pct_of_equity=size_from_confidence(confidence),
            rationale=(
                f"[{variant.name}] " if variant else ""
            ) + (
                f"Catalyseur dans {cat['catalyst_days_until']}j (proxy), volume {volume_ratio:.1f}x la moyenne 20j "
                f"(réel), momentum 20j {momentum:+.1f}% (réel), rang de volatilité réalisée {iv_rank:.0f}e "
                f"percentile (proxy IV)."
            ),
            invalidation="Le volume retombe sous 1.5x la moyenne, ou le catalyseur est repoussé/annulé.",
            main_risk="Décroissance theta rapide si le catalyseur ne produit pas le mouvement attendu à temps.",
            factors=factors,
        ))

    ideas.sort(key=lambda i: -i.score)
    return ideas[:limit]


def _pick_structure(days_until: int, iv_rank: float, momentum: float, bullish: bool,
                    structure_hint: str | None = None, forced_bearish: bool = False) -> tuple[str, str]:
    direction = "bearish" if forced_bearish else ("bullish" if bullish else "bearish")
    if structure_hint == "vol":
        return ("straddle" if abs(momentum) < 3 else "strangle"), "neutral_vol"
    if structure_hint == "outright":
        return ("long_call" if direction == "bullish" else "long_put"), direction
    if structure_hint == "directional_spread":
        return ("debit_spread_call" if direction == "bullish" else "debit_spread_put"), direction

    if days_until <= 3 and iv_rank < 40:
        return ("long_call" if bullish else "long_put"), ("bullish" if bullish else "bearish")
    if iv_rank >= 70:
        return ("straddle" if abs(momentum) < 3 else "strangle"), "neutral_vol"
    if abs(momentum) > 5 and iv_rank < 70:
        return ("debit_spread_call" if bullish else "debit_spread_put"), ("bullish" if bullish else "bearish")
    return "calendar_spread", "neutral_vol"
