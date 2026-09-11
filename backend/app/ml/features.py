"""Point-in-time factor computation shared by the live ML engine
(app/strategies/ml_baseline.py, always "as of today") and phase 4's historical
training dataset (app/ml/dataset.py, "as of" any past date). Keeping one
implementation guarantees the model is trained on exactly the inputs it will see
at inference time — the only thing that differs is how much of `hist_df` the
caller has truncated to, and which `as_of` date is passed to the real-if-available
macro regime (app/data/macro.py)."""
from __future__ import annotations

from datetime import date

import pandas as pd

from app.data.macro import macro_backdrop
from app.signals.market import market_signals
from app.signals.proxy import fundamentals_signals, macro_sensitivity, news_signals, options_microstructure
from app.strategies.base import FactorScore, normalize_pct

FACTOR_NAMES = ["price_trend", "fundamentals", "options", "news", "macro"]
DEFAULT_WEIGHTS = {"price_trend": 0.35, "fundamentals": 0.20, "options": 0.15, "news": 0.15, "macro": 0.15}


def compute_factors(symbol: str, hist_df: pd.DataFrame, as_of: date) -> list[FactorScore] | None:
    """hist_df must already be truncated to rows available as of `as_of` — no
    lookahead. Returns None if there isn't enough history yet (mirrors the
    30-row floor ml_baseline used inline before this was extracted)."""
    if hist_df is None or len(hist_df) < 30:
        return None

    m = market_signals(hist_df)
    fund = fundamentals_signals(symbol)
    opt = options_microstructure(symbol)
    news = news_signals(symbol)
    macro = macro_backdrop(as_of)
    sensitivity = macro_sensitivity(symbol)

    momentum = m.get("momentum_63d_pct", 0.0)
    trend_up = m.get("price_above_sma50", False) and m.get("price_above_sma200", False)

    price_factor = normalize_pct(momentum, -20, 20) * 0.6 + (1.0 if trend_up else 0.0) * 0.4
    options_factor = (opt["open_interest_score"] + (1 - min(opt["bid_ask_spread_bps"] / 50, 1))) / 2
    news_factor = (news["sentiment"] + 1) / 2 * news["source_quality"]
    macro_factor = macro["regime_score"] * (1 - sensitivity) + (1 - macro["regime_score"]) * sensitivity * 0.3

    return [
        FactorScore("price_trend", price_factor, DEFAULT_WEIGHTS["price_trend"], False,
                    {"momentum_63d_pct": momentum, "trend_up": trend_up}),
        FactorScore("fundamentals", fund["quality_score"], DEFAULT_WEIGHTS["fundamentals"], True, fund),
        FactorScore("options", options_factor, DEFAULT_WEIGHTS["options"], True, opt),
        FactorScore("news", news_factor, DEFAULT_WEIGHTS["news"], True, news),
        FactorScore("macro", macro_factor, DEFAULT_WEIGHTS["macro"], macro["source"] != "real", macro),
    ]
