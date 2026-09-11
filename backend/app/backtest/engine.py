"""Historical backtest: replays the same 3 engines + kill/scale logic used
live (app/tournament/cycle.py) over years of past price data in one blocking
call, instead of waiting months for the live cycle to accumulate enough
trades. Point-in-time correct — at each rebalance date, every engine only
ever sees price history up to and including that date (app/strategies/*.py's
`histories`/`as_of` override params, app/ml/features.py's `compute_factors`).

**Buy-only, same as the live cycle.** There is no automated sell anywhere in
this app (only a manual sell form, app/routers/variants.py) — the backtest
mirrors that rather than inventing a nicer exit rule that would diverge from
what live actually does. One real consequence, discovered while building
this: the live cycle's kill/scale gates on `n_closed_trades`, which — since
nothing auto-sells — never leaves 0 for cycle-driven trades. Kill/scale
would essentially never fire if mirrored literally. Here it gates on
`n_fills` (buy fills placed) instead, per the user's explicit call — a
deliberate, documented divergence from live, not an oversight; live has the
same underlying gap, unresolved.

**Known, undisclosed-nowhere-else caveat**: the ML engine's trained artifact
(app/ml/artifacts/ranking_model.json) was fit on roughly the same multi-year
window a backtest replays, so its numbers here carry an in-sample advantage
Casino/Economist (static heuristics, never fit to any window) never had.
Not corrected — that would need point-in-time model retraining, out of
scope for v1. Surfaced in the API response and the frontend instead.

No "spawn a new variant from a winner" here (unlike the live cycle) — that
grows the roster for future live cycles, it doesn't add information to a
backtest's answer to "does this approach have edge".
"""
from __future__ import annotations

import itertools
import math
from datetime import date

import pandas as pd

from app.backtest.portfolio import BtPortfolio, BtPosition
from app.data.provider import PriceHistory, price_history
from app.paper import metrics
from app.paper.broker import simulate_fill
from app.strategies import casino, economist, ml_baseline
from app.strategies.base import TradeIdea
from app.tournament import cycle
from app.tournament.variants_config import ALL_VARIANTS

ENGINES = {"casino": casino, "ml": ml_baseline, "economist": economist}
INITIAL_CASH = 100_000.0

ML_INSAMPLE_CAVEAT = (
    "Le modèle Ridge du Matheux a été entraîné sur à peu près la même fenêtre "
    "historique que ce backtest rejoue — contrairement au Flambeur et au "
    "Stratège (formules statiques jamais calées sur aucune période), ses "
    "résultats ici bénéficient donc d'un avantage in-sample. Pas corrigé — "
    "corriger ça demanderait de ré-entraîner le modèle point-in-time à "
    "chaque date, hors périmètre pour l'instant."
)


def _union_universe() -> list[str]:
    symbols: set[str] = set(casino.UNIVERSE) | set(ml_baseline.UNIVERSE)
    for theme_symbols in economist.THEMES.values():
        symbols.update(theme_symbols)
    return sorted(symbols)


def _close_panel(histories: dict[str, PriceHistory]) -> pd.DataFrame:
    """Wide DataFrame (dates x symbols, close price), forward-filled so a
    symbol missing a bar on a given day still prices at its last known close
    — cheap enough to look up every trading day, unlike full factor
    computation. Used to mark equity daily even though ideas are only
    (re)generated every `rebalance_every_days` — see run_backtest."""
    closes = {sym: h.df["close"] for sym, h in histories.items()}
    return pd.DataFrame(closes).sort_index().ffill()


def _truncate_histories(histories: dict[str, PriceHistory], as_of: date) -> dict[str, PriceHistory]:
    cutoff = pd.Timestamp(as_of)
    out: dict[str, PriceHistory] = {}
    for symbol, h in histories.items():
        df = h.df[h.df.index <= cutoff]
        if not df.empty:
            out[symbol] = PriceHistory(symbol=symbol, df=df, source=h.source)
    return out


def _new_portfolios() -> dict[str, BtPortfolio]:
    portfolios: dict[str, BtPortfolio] = {}
    for engine_name, variants in ALL_VARIANTS.items():
        for key, cfg in variants.items():
            vid = f"{engine_name}-{key}"
            portfolios[vid] = BtPortfolio(
                variant_id=vid, name=cfg.name, engine=engine_name, variant_key=key,
                initial_cash=INITIAL_CASH, cash=INITIAL_CASH,
            )
    return portfolios


def _propose_and_fill(
    portfolio: BtPortfolio, ideas: list[TradeIdea], prices: dict[str, float], order_counter: itertools.count,
) -> None:
    held = portfolio.held_symbols()
    placed = 0
    for idea in ideas:
        if placed >= cycle.IDEAS_PER_VARIANT_PER_CYCLE:
            break
        if idea.symbol in held:
            continue
        market_price = prices.get(idea.symbol)
        if market_price is None or market_price <= 0:
            continue
        budget = round(portfolio.cash * idea.size_pct_of_equity, 2)
        qty = math.floor(budget / market_price)
        if qty < 1:
            continue
        result = simulate_fill(
            symbol=idea.symbol, side="buy", qty=qty, order_type="market",
            limit_price=None, market_price=market_price, order_id=next(order_counter),
        )
        if result.status not in ("filled", "partial_fill") or not result.filled_avg_price:
            continue
        notional = result.filled_qty * result.filled_avg_price
        if notional > portfolio.cash:
            continue

        pos = portfolio.positions.setdefault(idea.symbol, BtPosition(symbol=idea.symbol))
        new_qty = pos.qty + result.filled_qty
        pos.avg_entry_price = (pos.avg_entry_price * pos.qty + notional) / new_qty if new_qty else 0.0
        pos.qty = new_qty
        portfolio.cash -= notional
        portfolio.n_fills += 1
        placed += 1


def _apply_kill_scale(portfolios: dict[str, BtPortfolio], prices: dict[str, float]) -> None:
    for portfolio in portfolios.values():
        if portfolio.status != "active" or portfolio.n_fills < cycle.MIN_TRADES_TO_JUDGE:
            continue
        equity = portfolio.equity(prices)
        total_pnl_pct = (equity - portfolio.initial_cash) / portfolio.initial_cash * 100
        if total_pnl_pct <= cycle.KILL_THRESHOLD_PCT:
            portfolio.status = "killed"
        elif total_pnl_pct >= cycle.SCALE_THRESHOLD_PCT and not portfolio.scaled:
            portfolio.cash += round(portfolio.initial_cash * cycle.SCALE_CASH_BOOST_PCT, 2)
            portfolio.scaled = True


def _result_row(portfolio: BtPortfolio, periods_per_year: float) -> dict:
    equities = [e for _, e in portfolio.equity_history]
    max_dd_pct = metrics.max_drawdown_pct(equities) if equities else 0.0
    rets = metrics.daily_returns(portfolio.equity_history)
    final_equity = equities[-1] if equities else portfolio.initial_cash
    total_pnl_pct = (final_equity - portfolio.initial_cash) / portfolio.initial_cash * 100

    return {
        "variant_id": portfolio.variant_id, "name": portfolio.name, "engine": portfolio.engine,
        "variant_key": portfolio.variant_key, "status": portfolio.status,
        "final_equity": round(final_equity, 2), "total_pnl_pct": round(total_pnl_pct, 3),
        "max_drawdown_pct": round(max_dd_pct, 3),
        "sharpe": round(metrics.sharpe_ratio(rets, periods_per_year), 3),
        "sortino": round(metrics.sortino_ratio(rets, periods_per_year), 3),
        "n_fills": portfolio.n_fills,
        # honest, same reason live never populates these — see module docstring
        "hit_rate_pct": None, "profit_factor": None,
        # (ISO date, equity) pairs, one per trading day — same dates array across
        # every variant in this run (all portfolios are marked on the same daily
        # loop) — for the frontend's per-engine equity-curve charts.
        "equity_history": portfolio.equity_history,
    }


def run_backtest(years: int = 1, rebalance_every_days: int = 10) -> dict:
    """Defaults measured at ~3 minutes end-to-end (14 variants, ~127-symbol
    union universe) — years=3/rebalance_every_days=5 from an earlier draft of
    this plan was untested and turned out to run 10+ minutes, too slow for a
    synchronous request. Larger `years` scale roughly linearly; smaller
    `rebalance_every_days` scales worse (more expensive factor-computation
    passes, not just more snapshots) — warn accordingly in the UI rather than
    defaulting to it."""
    universe = _union_universe()
    total_days = years * 252 + 60
    histories = price_history(universe, days=total_days)
    panel = _close_panel(histories)
    trading_days = list(panel.index)
    rebalance_days = set(trading_days[::max(rebalance_every_days, 1)])

    portfolios = _new_portfolios()
    order_counter = itertools.count(1)

    for ts in trading_days:
        as_of = ts.date()
        # cheap price lookup every day (marks equity accurately); the
        # expensive part — factor computation across the universe — only
        # runs on rebalance days, below.
        prices_asof = panel.loc[ts].dropna().to_dict()

        if ts in rebalance_days:
            hist_asof = _truncate_histories(histories, as_of)
            for engine_name, variants in ALL_VARIANTS.items():
                module = ENGINES[engine_name]
                for key in variants:
                    portfolio = portfolios[f"{engine_name}-{key}"]
                    if portfolio.status != "active":
                        continue
                    try:
                        ideas = module.generate_ideas(limit=5, variant_key=key, histories=hist_asof, as_of=as_of)
                    except Exception:
                        ideas = []
                    _propose_and_fill(portfolio, ideas, prices_asof, order_counter)
            _apply_kill_scale(portfolios, prices_asof)

        for portfolio in portfolios.values():
            portfolio.equity_history.append((as_of.isoformat(), portfolio.equity(prices_asof)))

    # true daily snapshots now (decoupled from rebalance cadence above), so
    # the standard 252-trading-day annualization is correct again — no more
    # periods_per_year juggling needed at the call site.
    results = [_result_row(p, metrics.TRADING_DAYS_PER_YEAR) for p in portfolios.values()]
    results.sort(key=lambda r: -r["total_pnl_pct"])
    return {
        "years": years, "rebalance_every_days": rebalance_every_days,
        "n_trading_days": len(trading_days), "n_rebalances": len(rebalance_days),
        "universe_size": len(universe),
        "ml_insample_caveat": ML_INSAMPLE_CAVEAT,
        "results": results,
    }
