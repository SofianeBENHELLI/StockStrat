"""Le Stratège — grandes tendances. Décide chaque mois, tient 3 à 6 mois.

Holds themes, not companies: each theme is one liquid ETF (XLE for oil and
gas, ITA for defence, SMH for semiconductors...). Once a month it ranks the
themes by their momentum over several months and holds the strongest few,
provided each is also in an absolute uptrend (above its 200-day average with a
positive return) — otherwise that slot sits in cash.

The horizon is built into the rules rather than into a clock:

- a theme bought is **kept for at least `min_hold_months`** as long as its
  uptrend holds, even if another theme briefly overtakes it — that is the
  "je vise 3 à 6 mois, je ne réagis pas au bruit" part;
- past that, it must still earn its place in the top at each monthly review;
- and at any moment, the **trailing stop** closes it if it falls too far from
  its high — the "le pétrole a perdu 15 %, je sors tout de suite" part, checked
  every evening, not once a month.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.lab import indicators as ind
from app.lab.profiles.base import Decision, Param, Profile, Variant, risk_params
from app.lab.universe import THEMES

DEFENSIVE = {"GLD", "TLT"}


@dataclass
class Ctx:
    close: pd.DataFrame
    momentum: pd.DataFrame
    trend: pd.DataFrame
    dates: pd.DatetimeIndex


class Stratege(Profile):
    key = "stratege"
    label = "Le Stratège"
    nickname = "Grandes tendances"
    horizon = "3 à 6 mois"
    cadence = "monthly"
    description = ("Mise sur des thèmes de fond — pétrole, défense, semi-conducteurs, or, uranium… — via des "
                   "ETF. Chaque mois, garde les thèmes les plus porteurs, ne réagit pas au bruit, mais "
                   "sort immédiatement si un thème décroche.")

    def params(self) -> list[Param]:
        return [
            Param("lookback_months", "Horizon du momentum", "int", 6, "Signal", minimum=1, maximum=12,
                  unit="mois", help="Les thèmes sont classés sur leur performance sur cette période."),
            Param("trend_sma", "Filtre de tendance", "int", 200, "Signal", minimum=0, maximum=400,
                  unit="séances", help="Un thème n'est détenu que s'il est au-dessus de cette moyenne "
                                       "(et en hausse sur l'horizon). Sinon, sa part reste en cash."),
            Param("include_defensive", "Inclure les refuges", "bool", False, "Signal",
                  help="Ajoute l'or et les obligations longues aux thèmes éligibles."),
            Param("top_k", "Nombre de thèmes", "int", 3, "Portefeuille", minimum=1, maximum=8,
                  help="Le budget est réparti en parts égales entre les thèmes retenus."),
            Param("min_hold_months", "Détention minimale", "int", 3, "Portefeuille", minimum=0, maximum=12,
                  unit="mois", help="Un thème reste détenu au moins ce temps tant que sa tendance tient."),
            *risk_params(stop=0, trailing=15, take_profit=0, max_hold=0),
        ]

    def variants(self) -> list[Variant]:
        return [
            Variant("A", "Top 3 thèmes, 6 mois", "Les 3 thèmes les plus forts sur 6 mois, stop suiveur 15 %.",
                    {}),
            Variant("B", "Convictions longues",
                    "2 thèmes sur 12 mois de momentum, détention minimale 6 mois, stop suiveur 12 %.",
                    {"lookback_months": 12, "top_k": 2, "min_hold_months": 6, "trailing_stop_pct": 12}),
            Variant("C", "Diversifié avec refuges",
                    "4 thèmes dont or et obligations éligibles, horizon 3 mois.",
                    {"top_k": 4, "lookback_months": 3, "include_defensive": True, "trailing_stop_pct": 10}),
        ]

    def universe(self, params: dict) -> list[str]:
        return [s for s in THEMES if params["include_defensive"] or s not in DEFENSIVE]

    def prepare(self, panel, params: dict) -> Ctx:
        close = panel.close
        return Ctx(
            close=close,
            momentum=ind.ret(close, 21 * params["lookback_months"]),
            trend=ind.sma(close, params["trend_sma"]) if params["trend_sma"] > 0 else close * 0,
            dates=close.index,
        )

    def _eligible(self, ctx: Ctx, i: int) -> pd.Series:
        mom = ctx.momentum.iloc[i]
        ok = (ctx.close.iloc[i] > ctx.trend.iloc[i]) & (mom > 0)
        return mom[ok].dropna().sort_values(ascending=False)

    def decide(self, ctx: Ctx, i: int, book, params: dict, rebalance: bool) -> Decision:
        d = Decision()
        close, trend = ctx.close.iloc[i], ctx.trend.iloc[i]

        # Every evening: a held theme that has lost its uptrend is a broken thesis.
        for sym in book.holdings:
            if params["trend_sma"] > 0 and bool(close.get(sym, 0) < trend.get(sym, 0)):
                d.exits[sym] = "tendance cassée"

        if not rebalance:
            return d

        ranked = self._eligible(ctx, i)
        top = list(ranked.index[:params["top_k"]])
        keep = []
        for sym, h in book.holdings.items():
            if sym in d.exits:
                continue
            held_days = i - h.entry_i
            if held_days < 21 * params["min_hold_months"] and sym in ranked.index:
                keep.append(sym)       # inside its minimum horizon and still trending: hold on
            elif sym in top:
                keep.append(sym)       # past the horizon, but still earning its place
        chosen = list(dict.fromkeys(keep + [s for s in top if s not in keep]))[:params["top_k"]]
        weight = 1.0 / params["top_k"]
        d.targets = {s: weight for s in chosen}
        for s in chosen:
            d.notes[s] = (f"{THEMES.get(s, s)} : {ranked.get(s, float('nan')) * 100:+.1f} % sur "
                          f"{params['lookback_months']} mois")
        return d
