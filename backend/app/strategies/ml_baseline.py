"""ML Strat engine — phase 2 baseline, phase 4 trained ranking model.

Factor computation lives in app/ml/features.py (shared with the historical
training dataset, so the model trains on exactly what it sees at inference
time). When app/ml/train.py has produced an artifact (POST /api/ml/train or
`uv run python -m app.ml.train`), app/ml/inference.py supplies Ridge-learned
weights that replace the hand-picked 0.35/0.20/0.15/0.15/0.15 composite, and its
predicted-move regression replaces the naive momentum*0.3 placeholder — see the
README's Phase 4 section for the walk-forward methodology and its results.
Without a trained artifact yet, this falls back to the original fixed-weight
heuristic so the app keeps working end-to-end, same fallback philosophy as the
mock price feed. Either way the per-variant weight_overrides (Momentum Only,
Ensemble, etc. — app/tournament/variants_config.py) apply on top unchanged.

Output never says 'buy X' as an imperative — per spec, it's: action, predicted
20d move, confidence, expected volatility, best structure, main reason, main
risk.
"""
from __future__ import annotations

from datetime import date

from app.data.provider import PriceHistory, price_history
from app.ml import inference
from app.ml.features import compute_factors
from app.signals.market import market_signals
from app.strategies.base import TradeIdea, apply_weight_overrides, size_from_confidence, weighted_score
from app.tournament.variants_config import VariantConfig, get_variant_config

UNIVERSE = [
    # 101 large-cap names spanning sectors (tech, financials, healthcare, energy,
    # staples, industrials, materials, utilities) — widened from the original 16
    # for more statistical power behind whatever real signal the model can find;
    # verified against yfinance before landing (see README's phase 4 update).
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "BRK-B", "JPM", "V",
    "MA", "UNH", "HD", "PG", "XOM", "CVX", "JNJ", "LLY", "ABBV", "MRK",
    "KO", "PEP", "WMT", "COST", "MCD", "NKE", "DIS", "CMCSA", "ADBE", "CRM",
    "ORCL", "CSCO", "INTC", "AMD", "QCOM", "TXN", "IBM", "NFLX", "PYPL", "ABT",
    "TMO", "DHR", "BMY", "PFE", "GILD", "AMGN", "MDT", "ISRG", "SYK", "BA",
    "CAT", "GE", "HON", "UPS", "RTX", "LMT", "MMM", "DE", "UNP", "NEE",
    "DUK", "SO", "D", "AEP", "SLB", "COP", "EOG", "PSX", "MPC", "WFC",
    "BAC", "C", "GS", "MS", "AXP", "BLK", "SPGI", "SCHW", "LOW", "TGT",
    "SBUX", "TJX", "BKNG", "CVS", "ELV", "CI", "HUM", "VZ", "T", "CHTR",
    "LIN", "APD", "SHW", "ECL", "FCX", "NUE", "ALL", "PGR", "TRV", "MET", "AIG",
]

SCORE_THRESHOLD = 55.0
MAX_IDEAS = 5


def generate_ideas(
    limit: int = MAX_IDEAS, variant_key: str | None = None,
    histories: dict[str, PriceHistory] | None = None, as_of: date | None = None,
) -> list[TradeIdea]:
    """`histories`/`as_of` let a caller (the backtest engine) evaluate this
    engine as of a past date instead of today — omit both for live/current
    behavior, unchanged from before."""
    variant: VariantConfig | None = get_variant_config("ml", variant_key) if variant_key else None
    histories = histories if histories is not None else price_history(UNIVERSE, days=260)
    as_of = as_of if as_of is not None else date.today()
    model_meta = inference.model_metadata()
    ideas: list[TradeIdea] = []

    for symbol in UNIVERSE:
        hist = histories.get(symbol)
        if hist is None:
            continue
        factors = compute_factors(symbol, hist.df, as_of)
        if factors is None:
            continue
        m = market_signals(hist.df)
        momentum = m.get("momentum_63d_pct", 0.0)
        rsi = m.get("rsi_14", 50.0)
        vol30 = m.get("realized_vol_30d_pct", 25.0)

        if model_meta:
            factors = apply_weight_overrides(factors, model_meta["weights"])
        if variant:
            factors = apply_weight_overrides(factors, variant.weight_overrides)
        score = weighted_score(factors)
        if score < SCORE_THRESHOLD:
            continue

        confidence = score / 100
        if model_meta:
            predicted_move = inference.predict_forward_move(factors)
        else:
            # naive, transparent placeholder for a predicted move: damped momentum,
            # not a trained forecast — replaced once a model is trained (see above).
            predicted_move = round(momentum * 0.3, 2)
        bullish = predicted_move >= 0
        structure = _pick_structure(bullish, vol30)

        prefix = f"[{variant.name}] " if variant else ""
        if model_meta:
            acc = model_meta["metrics"].get("directional_accuracy")
            acc_txt = f"{acc * 100:.0f}%" if acc is not None else "n/a"
            rationale = (
                f"{prefix}Modèle Ridge entraîné ({model_meta['n_samples']} exemples, précision "
                f"directionnelle walk-forward {acc_txt}). Tendance prix {momentum:+.1f}% sur 63j "
                f"(réel, RSI {rsi:.0f}). Score composite {score:.0f}/100 avec poids appris."
            )
            macro_is_proxy = next((f.is_proxy for f in factors if f.name == "macro"), True)
            macro_risk_txt = (
                "le facteur macro utilise un régime réel (VIX/taux 10 ans/or/pétrole/dollar) mais sur "
                "seulement quelques années d'historique — sa robustesse hors échantillon reste modeste"
                if not macro_is_proxy else
                "le macro ne varie que par jour calendaire (flux réel indisponible pour le moment)"
            )
            main_risk = (
                "Poids appris par régression Ridge sur historique de prix réel — les facteurs "
                f"fondamentaux/options/news restent statiques par symbole (proxy) et {macro_risk_txt}, "
                "donc leur pouvoir prédictif hors échantillon reste limité (walk-forward détaillé dans "
                "le README)."
            )
        else:
            macro_is_proxy = next((f.is_proxy for f in factors if f.name == "macro"), True)
            macro_txt = "régime macro (proxy)" if macro_is_proxy else "régime macro (réel)"
            rationale = (
                f"{prefix}Tendance prix {momentum:+.1f}% sur 63j (réel, RSI {rsi:.0f}), "
                f"{macro_txt}, sentiment news (proxy). Score composite "
                f"{score:.0f}/100 — modèle baseline à poids fixes, pas encore entraîné "
                "(POST /api/ml/train)."
            )
            main_risk = (
                "Poids fixes non calibrés statistiquement — peut sur-pondérer un facteur bruité "
                "tant qu'aucun entraînement/walk-forward n'a eu lieu."
            )

        ideas.append(TradeIdea(
            symbol=symbol, engine="ml", action="buy", score=score, confidence=confidence,
            structure=structure, direction="bullish" if bullish else "bearish",
            size_pct_of_equity=size_from_confidence(confidence, 0.005, 0.02),
            predicted_move_pct=predicted_move, expected_vol_pct=vol30,
            rationale=rationale,
            invalidation="La tendance de prix 63j s'inverse de signe, ou le score composite retombe sous 55.",
            main_risk=main_risk,
            factors=factors,
        ))

    ideas.sort(key=lambda i: -i.score)
    return ideas[:limit]


def _pick_structure(bullish: bool, vol30: float) -> str:
    if vol30 >= 30:
        return "long_call" if bullish else "long_put"
    return "debit_spread_call" if bullish else "debit_spread_put"
