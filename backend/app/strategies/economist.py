"""The Economist engine — macro, thematic, medium/long-term. Starts from a
macro thesis (theme) then screens beneficiaries within a small curated universe
per theme (a real, hand-curated substitute for a thematic-exposure database we
don't have). Sector/theme momentum is computed for real as the average
momentum of the theme's basket (app/signals/market.basket_momentum_pct);
fundamentals/valuation/catalyst-visibility are proxies pending a real
fundamentals feed.

Blind spot from the spec: 'often right too early' — enforced here by NEVER
selecting a short-dated structure. Every structure this engine picks is
long-dated by construction (LEAPS / long-dated debit spread / collar); there
is no long_call/long_put/straddle path like the Casino engine has.
"""
from __future__ import annotations

from datetime import date

from app.data.provider import PriceHistory, price_history
from app.signals.market import basket_momentum_pct, market_signals
from app.signals.proxy import catalyst_signals, fundamentals_signals, macro_theme_alignment, options_microstructure
from app.strategies.base import (
    FactorScore, TradeIdea, apply_weight_overrides, normalize_pct, size_from_confidence, weighted_score,
)
from app.tournament.variants_config import ECONOMIST_VARIANT_THEMES, VariantConfig, get_variant_config

THEMES: dict[str, list[str]] = {
    "Infrastructure IA": ["NVDA", "AVGO", "SMCI", "VRT"],
    "Défense": ["LMT", "RTX", "NOC"],
    "Demande d'énergie": ["VST", "NRG", "CEG"],
    "Réindustrialisation & quantique": ["IONQ", "RGTI"],
    "Cybersécurité": ["CRWD", "PANW"],
    "Robotique": ["ISRG", "ROK"],
    "Bénéficiaires de baisse de taux": ["DHI", "LEN", "AMH"],
}

MAX_IDEAS = 5


def generate_ideas(
    limit: int = MAX_IDEAS, variant_key: str | None = None,
    histories: dict[str, PriceHistory] | None = None, as_of: date | None = None,
) -> list[TradeIdea]:
    """See casino.py's `generate_ideas` docstring — same `histories`/`as_of`
    override convention for the backtest engine; unused directly here (no
    calendar-dependent signal in this engine), point-in-time correctness
    comes entirely from the caller pre-truncating `histories`."""
    variant: VariantConfig | None = get_variant_config("economist", variant_key) if variant_key else None
    themes = {t: THEMES[t] for t in ECONOMIST_VARIANT_THEMES[variant_key]} if variant_key else THEMES
    all_symbols = sorted({s for syms in themes.values() for s in syms})
    histories = histories if histories is not None else price_history(all_symbols, days=260)
    ideas: list[TradeIdea] = []

    for theme, symbols in themes.items():
        closes = [histories[s].df["close"] for s in symbols if s in histories]
        theme_momentum = basket_momentum_pct(closes) or 0.0

        for symbol in symbols:
            hist = histories.get(symbol)
            if hist is None or len(hist.df) < 30:
                continue
            m = market_signals(hist.df)
            fund = fundamentals_signals(symbol)
            cat = catalyst_signals(symbol)
            opt = options_microstructure(symbol)
            alignment = macro_theme_alignment(symbol, theme)

            valuation_discipline = 1 - fund["valuation_richness"]  # cheaper vs growth = more disciplined
            factors = [
                FactorScore("macro_theme_alignment", alignment, 0.25, True, {"theme": theme}),
                FactorScore("fundamental_quality", fund["quality_score"], 0.20, True, fund),
                FactorScore("revisions_trend", (fund["revisions_trend"] + 1) / 2, 0.15, True, fund["revisions_trend"]),
                FactorScore("sector_momentum", normalize_pct(theme_momentum, -10, 30), 0.15, False, theme_momentum),
                FactorScore("valuation_discipline", valuation_discipline, 0.10, True, fund["valuation_richness"]),
                FactorScore("catalyst_visibility", cat["catalyst_strength"], 0.10, True, cat["catalyst_strength"]),
                FactorScore("options_liquidity", opt["open_interest_score"], 0.05, True, opt),
            ]
            if variant:
                factors = apply_weight_overrides(factors, variant.weight_overrides)
            score = weighted_score(factors)
            confidence = score / 100
            structure = _pick_structure(valuation_discipline, m.get("realized_vol_30d_pct", 25.0))

            ideas.append(TradeIdea(
                symbol=symbol, engine="economist", action="buy", score=score, confidence=confidence,
                structure=structure, direction="bullish",
                size_pct_of_equity=size_from_confidence(confidence, 0.01, 0.03),
                rationale=(
                    (f"[{variant.name}] " if variant else "") +
                    f"Thème « {theme} » : momentum du panier {theme_momentum:+.1f}% sur 63j (réel), "
                    f"alignement thématique {alignment:.2f} (proxy), qualité fondamentale "
                    f"{fund['quality_score']:.2f} (proxy). Horizon volontairement long — structure "
                    "toujours longue échéance pour ne pas payer le prix d'être 'tôt' avec du theta court."
                ),
                invalidation=f"La thèse macro « {theme} » s'inverse, ou le momentum du panier repasse négatif sur 2 trimestres.",
                main_risk="Thèse macro juste mais mal calée dans le temps — d'où l'interdiction d'échéances courtes sur ce moteur.",
                factors=factors,
            ))

    ideas.sort(key=lambda i: -i.score)
    return ideas[:limit]


def _pick_structure(valuation_discipline: float, vol30: float) -> str:
    if vol30 >= 35:
        return "collar"  # protect against being early with a defined downside
    if valuation_discipline < 0.4:
        return "debit_spread_call"  # rich valuation: cap the premium paid
    return "leaps_call"
