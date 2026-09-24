"""Le Flambeur — court terme. Décide chaque jour, tient quelques séances.

Two short-horizon families, picked by the `mode` parameter, both on real
price and volume only:

- **Rebond** (mean reversion in an uptrend). Buy a large cap that is above its
  200-day average but has just been sold off hard — a very low 2-day RSI — and
  sell as soon as it bounces back above its 5-day average. Among the most
  documented short-term effects on US large caps; it earns small, frequent gains
  and needs the trend filter to avoid catching a real collapse.
- **Cassure** (breakout on volume). Buy a stock that closes above its 20-day
  high on unusually heavy volume, and ride it with a trailing stop.

It fills free slots rather than rebalancing: a position lives until one of its
exits fires, and a freed slot is refilled the same evening.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.lab import indicators as ind
from app.lab.profiles.base import Decision, Param, Profile, Variant, risk_params
from app.lab.universe import MEGA_CAPS


@dataclass
class Ctx:
    close: pd.DataFrame
    trend: pd.DataFrame
    rsi: pd.DataFrame
    exit_sma: pd.DataFrame
    breakout_level: pd.DataFrame
    vol_ratio: pd.DataFrame


class Flambeur(Profile):
    key = "flambeur"
    label = "Le Flambeur"
    nickname = "Court terme"
    horizon = "quelques jours"
    cadence = "daily"
    description = ("Opportuniste. Chaque soir, cherche parmi 100 grandes capitalisations US celles qui "
                   "viennent de décrocher dans une tendance haussière (rebond) ou de casser un plus haut "
                   "sur volume (cassure), et sort vite.")

    def params(self) -> list[Param]:
        return [
            Param("mode", "Famille de signal", "choice", "rebond", "Signal",
                  choices=(("rebond", "Rebond après excès de baisse"), ("cassure", "Cassure sur volume")),
                  help="Rebond : achète la panique dans une tendance saine. Cassure : achète la force."),
            Param("trend_sma", "Filtre de tendance", "int", 200, "Signal", minimum=0, maximum=400,
                  unit="séances", help="N'achète que si le cours est au-dessus de sa moyenne sur cette "
                                       "durée. 0 = pas de filtre."),
            Param("rsi_len", "RSI — période", "int", 2, "Signal", minimum=2, maximum=30,
                  unit="séances", visible_if=("mode", "rebond"),
                  help="Un RSI très court mesure l'excès de la baisse des derniers jours."),
            Param("entry_rsi", "RSI — seuil d'achat", "float", 10, "Signal", minimum=1, maximum=50,
                  visible_if=("mode", "rebond"), help="Achète quand le RSI passe sous ce seuil."),
            Param("exit_sma", "Sortie au rebond", "int", 5, "Signal", minimum=0, maximum=50, unit="séances",
                  visible_if=("mode", "rebond"),
                  help="Vend dès que le cours repasse au-dessus de sa moyenne sur cette durée. 0 = désactivé."),
            Param("breakout_len", "Plus haut de référence", "int", 20, "Signal", minimum=5, maximum=250,
                  unit="séances", visible_if=("mode", "cassure"),
                  help="Achète quand la clôture dépasse le plus haut de cette période."),
            Param("volume_mult", "Volume anormal", "float", 1.5, "Signal", minimum=1, maximum=10, step=0.1,
                  unit="×", visible_if=("mode", "cassure"),
                  help="Exige un volume au moins égal à ce multiple de la moyenne 20 séances."),
            Param("max_positions", "Positions simultanées", "int", 5, "Portefeuille", minimum=1, maximum=20,
                  help="Le budget est réparti en autant de parts égales."),
            *risk_params(stop=8, trailing=0, take_profit=0, max_hold=10),
        ]

    def variants(self) -> list[Variant]:
        return [
            Variant("A", "Rebond classique", "RSI(2) sous 10 dans une tendance haussière, 5 positions.",
                    {"mode": "rebond"}),
            Variant("B", "Rebond concentré",
                    "Seulement les excès extrêmes (RSI(2) sous 5), 3 positions plus grosses.",
                    {"mode": "rebond", "entry_rsi": 5, "max_positions": 3, "stop_loss_pct": 10}),
            Variant("C", "Cassure sur volume",
                    "Plus haut 20 séances franchi avec 1,5× le volume, stop suiveur 6 %.",
                    {"mode": "cassure", "trailing_stop_pct": 6, "stop_loss_pct": 0, "max_hold_days": 15}),
        ]

    def universe(self, params: dict) -> list[str]:
        return list(MEGA_CAPS)

    def prepare(self, panel, params: dict) -> Ctx:
        close = panel.close
        trend = ind.sma(close, params["trend_sma"]) if params["trend_sma"] > 0 else close * 0
        return Ctx(
            close=close, trend=trend,
            rsi=ind.rsi(close, params["rsi_len"]),
            exit_sma=ind.sma(close, params["exit_sma"]) if params["exit_sma"] > 0 else close * np.nan,
            breakout_level=ind.rolling_high(panel.high, params["breakout_len"]),
            vol_ratio=ind.volume_ratio(panel.volume, 20),
        )

    def decide(self, ctx: Ctx, i: int, book, params: dict, rebalance: bool) -> Decision:
        d = Decision()
        close = ctx.close.iloc[i]

        if params["mode"] == "rebond" and params["exit_sma"] > 0:
            above = close > ctx.exit_sma.iloc[i]
            for sym in book.holdings:
                if bool(above.get(sym, False)):
                    d.exits[sym] = "rebond atteint"

        free = params["max_positions"] - (len(book.holdings) - len(d.exits))
        if free <= 0:
            return d

        in_trend = close > ctx.trend.iloc[i]
        if params["mode"] == "rebond":
            score = ctx.rsi.iloc[i]
            ok = in_trend & (score < params["entry_rsi"])
            ranked = score[ok].sort_values()             # the most oversold first
        else:
            vr = ctx.vol_ratio.iloc[i]
            ok = in_trend & (close > ctx.breakout_level.iloc[i]) & (vr >= params["volume_mult"])
            ranked = vr[ok].sort_values(ascending=False)  # the heaviest volume first

        weight = 1.0 / params["max_positions"]
        for sym in ranked.index:
            if free <= 0:
                break
            if sym in book.holdings or sym in d.exits:
                continue
            d.entries[sym] = weight
            if params["mode"] == "rebond":
                d.notes[sym] = f"RSI({params['rsi_len']}) = {ctx.rsi.iloc[i][sym]:.1f} au-dessus de la tendance"
            else:
                d.notes[sym] = f"cassure du plus haut {params['breakout_len']}j, volume ×{ctx.vol_ratio.iloc[i][sym]:.1f}"
            free -= 1
        return d
