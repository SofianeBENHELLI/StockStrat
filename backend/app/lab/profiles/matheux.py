"""Le Matheux — modèle appris. Décide chaque mois, tient environ un mois.

Each month it scores ~100 large caps on how they are likely to rank over the
next month, holds the best-ranked few in equal weight, and starts over the
following month.

**The one rule that matters: walk-forward, point-in-time.** On a rebalance day
the model is refit using only examples whose outcome was *already known* that
day: an example taken at date s, labelled with the return from s to s+h, is
usable at date t only if s + h <= t. The fit window then slides forward month
by month. Nothing the model sees in a backtest was unknowable on that date, and
the live runner calls this very code on today's data, so paper trading gets
exactly the model the backtest measured — no separate "production" model.

**What it learns from.** Price and volume only, turned into cross-sectional
ranks each month (rank 0..1 among the universe, centred): short and medium
momentum, the classic 12-minus-1-month momentum, volatility, RSI, distance to
the 50- and 200-day averages, and a volume trend. Ranking both the features
and the target makes the model learn *relative* strength — which stocks beat
the others — rather than chase the level of the whole market, which no feature
here can predict.

**Honest expectation.** Cross-sectional stock prediction from price data alone
is a weak signal: a rank correlation of a few hundredths between prediction and
outcome is normal, and the backtest reports it (`diagnostics`) so a good
equity curve can be told apart from a lucky one.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.lab import indicators as ind
from app.lab.profiles.base import Decision, Param, Profile, Variant, risk_params
from app.lab.universe import BENCHMARK, MEGA_CAPS

FEATURES = {
    "mom_1m": "Momentum 1 mois",
    "mom_3m": "Momentum 3 mois",
    "mom_6m": "Momentum 6 mois",
    "mom_12_1": "Momentum 12 mois hors dernier mois",
    "vol_1m": "Volatilité 1 mois",
    "vol_3m": "Volatilité 3 mois",
    "rsi_14": "RSI 14",
    "dist_sma50": "Écart à la moyenne 50",
    "dist_sma200": "Écart à la moyenne 200",
    "volume_trend": "Tendance des volumes",
}


@dataclass
class Ctx:
    close: pd.DataFrame
    features: dict[str, pd.DataFrame]
    label: pd.DataFrame
    month_starts: list[int]
    regime: pd.Series
    candidates: list[str]
    predictions: dict[int, pd.Series] = field(default_factory=dict)
    coefficients: dict[int, dict[str, float]] = field(default_factory=dict)
    train_sizes: dict[int, int] = field(default_factory=dict)


def _xs_rank(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.rank(axis=1, pct=True) - 0.5


class Matheux(Profile):
    key = "matheux"
    label = "Le Matheux"
    nickname = "Modèle appris"
    horizon = "environ 1 mois"
    cadence = "monthly"
    description = ("Un modèle de machine learning réentraîné chaque mois sur le passé récent classe 100 "
                   "grandes capitalisations sur leur performance probable du mois suivant, et achète les "
                   "mieux classées. N'apprend que ce qui était connu à la date de décision.")

    def params(self) -> list[Param]:
        return [
            Param("model", "Algorithme", "choice", "ridge", "Signal",
                  choices=(("ridge", "Régression linéaire régularisée (Ridge)"),
                           ("gbm", "Arbres de décision boostés")),
                  help="Ridge : lisible, robuste, capture des effets linéaires. Arbres : capture des "
                       "interactions, plus sujet au surapprentissage."),
            Param("train_months", "Fenêtre d'apprentissage", "int", 36, "Signal", minimum=12, maximum=96,
                  unit="mois", help="Le modèle apprend sur les exemples de cette période glissante."),
            Param("horizon_days", "Horizon prédit", "int", 21, "Signal", minimum=5, maximum=63,
                  unit="séances", help="Le modèle prédit le classement à cette échéance (21 ≈ 1 mois)."),
            Param("ridge_alpha", "Régularisation", "float", 10.0, "Signal", minimum=0.01, maximum=1000,
                  visible_if=("model", "ridge"),
                  help="Plus elle est forte, plus le modèle reste simple et prudent."),
            Param("regime_filter", "Filtre de marché", "bool", False, "Signal",
                  help="Passe entièrement en cash quand le S&P 500 est sous sa moyenne 200 séances."),
            Param("top_n", "Titres détenus", "int", 10, "Portefeuille", minimum=1, maximum=30,
                  help="Les N titres les mieux classés, à parts égales."),
            *risk_params(stop=12, trailing=0, take_profit=0, max_hold=0),
        ]

    def variants(self) -> list[Variant]:
        return [
            Variant("A", "Linéaire", "Ridge sur 3 ans glissants, 10 titres.", {"model": "ridge"}),
            Variant("B", "Arbres boostés", "Gradient boosting sur 3 ans glissants, 10 titres.",
                    {"model": "gbm"}),
            Variant("C", "Concentré et prudent",
                    "Ridge, 5 titres seulement, cash quand le marché est sous sa moyenne 200 séances.",
                    {"model": "ridge", "top_n": 5, "regime_filter": True, "stop_loss_pct": 10}),
        ]

    def universe(self, params: dict) -> list[str]:
        return list(MEGA_CAPS) + [BENCHMARK]

    def prepare(self, panel, params: dict) -> Ctx:
        candidates = [s for s in panel.close.columns if s != BENCHMARK]
        close = panel.close[candidates]
        volume = panel.volume[candidates]
        daily = close.pct_change(fill_method=None)
        raw = {
            "mom_1m": ind.ret(close, 21),
            "mom_3m": ind.ret(close, 63),
            "mom_6m": ind.ret(close, 126),
            "mom_12_1": close.shift(21) / close.shift(252) - 1,
            "vol_1m": daily.rolling(21, min_periods=21).std(),
            "vol_3m": daily.rolling(63, min_periods=63).std(),
            "rsi_14": ind.rsi(close, 14),
            "dist_sma50": close / ind.sma(close, 50) - 1,
            "dist_sma200": close / ind.sma(close, 200) - 1,
            "volume_trend": volume.rolling(20).mean() / volume.rolling(120).mean() - 1,
        }
        h = params["horizon_days"]
        label = _xs_rank(close.shift(-h) / close - 1)
        bench = panel.close[BENCHMARK] if BENCHMARK in panel.close.columns else close.mean(axis=1)
        regime = bench > bench.rolling(200, min_periods=200).mean()
        return Ctx(close=close, features={k: _xs_rank(v) for k, v in raw.items()}, label=label,
                   month_starts=ind.month_starts(close.index), regime=regime, candidates=candidates)

    def _sample(self, ctx: Ctx, i: int) -> pd.DataFrame:
        return pd.DataFrame({k: f.iloc[i] for k, f in ctx.features.items()})

    def _fit_predict(self, ctx: Ctx, i: int, params: dict) -> pd.Series | None:
        if i in ctx.predictions:
            return ctx.predictions[i]
        h, window = params["horizon_days"], 21 * params["train_months"]
        xs, ys = [], []
        for s in ctx.month_starts:
            if s + h > i:             # outcome not known yet on day i
                break
            if s < i - window:
                continue
            x = self._sample(ctx, s)
            y = ctx.label.iloc[s]
            ok = x.notna().all(axis=1) & y.notna()
            if ok.sum() >= 10:
                xs.append(x[ok])
                ys.append(y[ok])
        if len(xs) < 6:               # under ~6 months of examples, there is nothing to learn from
            return None
        X, y = pd.concat(xs), pd.concat(ys)
        today = self._sample(ctx, i).dropna()
        if today.empty:
            return None

        if params["model"] == "gbm":
            from sklearn.ensemble import HistGradientBoostingRegressor
            model = HistGradientBoostingRegressor(max_iter=150, learning_rate=0.05, max_depth=3,
                                                  min_samples_leaf=40, random_state=0)
            model.fit(X.values, y.values)
        else:
            from sklearn.linear_model import Ridge
            model = Ridge(alpha=params["ridge_alpha"])
            model.fit(X.values, y.values)
            ctx.coefficients[i] = {k: float(c) for k, c in zip(X.columns, model.coef_)}

        pred = pd.Series(model.predict(today.values), index=today.index).sort_values(ascending=False)
        ctx.predictions[i] = pred
        ctx.train_sizes[i] = len(X)
        return pred

    def decide(self, ctx: Ctx, i: int, book, params: dict, rebalance: bool) -> Decision:
        d = Decision()
        if not rebalance:
            return d
        if params["regime_filter"] and not bool(ctx.regime.iloc[i]):
            d.targets = {}
            for sym in book.holdings:
                d.notes[sym] = "marché sous sa moyenne 200 séances : passage en cash"
            return d
        pred = self._fit_predict(ctx, i, params)
        if pred is None:
            return d                  # not enough history yet: stay put rather than guess
        chosen = list(pred.index[:params["top_n"]])
        weight = 1.0 / params["top_n"]
        d.targets = {s: weight for s in chosen}
        for rank, s in enumerate(chosen, 1):
            d.notes[s] = f"rang {rank}/{len(pred)} prédit par le modèle ({params['model']})"
        return d

    def diagnostics(self, ctx: Ctx, params: dict) -> dict:
        """How good were the predictions, measured after the fact?

        For every rebalance, the rank correlation between what the model
        predicted and how the stocks actually ranked over the horizon. The mean
        and its t-statistic say whether there is a signal at all; the hit rate
        is the share of months where the correlation was positive.
        """
        ics = []
        for i, pred in sorted(ctx.predictions.items()):
            realised = ctx.label.iloc[i].reindex(pred.index).dropna()
            if len(realised) < 10:
                continue
            ics.append(float(pred.reindex(realised.index).rank().corr(realised.rank())))
        coefs = {}
        if ctx.coefficients:
            last = ctx.coefficients[max(ctx.coefficients)]
            coefs = {FEATURES[k]: round(v, 4) for k, v in sorted(last.items(), key=lambda kv: -abs(kv[1]))}
        if not ics:
            return {"months_scored": 0, "coefficients": coefs}
        arr = np.array(ics)
        t = float(arr.mean() / (arr.std(ddof=1) / np.sqrt(len(arr)))) if len(arr) > 1 and arr.std() > 0 else 0.0
        return {
            "months_scored": len(arr),
            "mean_rank_correlation": round(float(arr.mean()), 4),
            "t_stat": round(t, 2),
            "positive_months_pct": round(float((arr > 0).mean() * 100), 1),
            "last_train_size": ctx.train_sizes[max(ctx.train_sizes)] if ctx.train_sizes else 0,
            "coefficients": coefs,
        }
