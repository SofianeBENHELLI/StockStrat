"""What a profile is, and the parameters everyone shares.

A profile is a strategy family: it turns market data into decisions. It never
touches cash, orders or a broker — that is the engine's job (app/lab/engine.py).
Keeping the two apart is what lets one profile drive both a ten-year backtest
and a live paper account with no second implementation to drift.

Parameters are declared, not hard-coded. Every number that changes behaviour
is a `Param` with bounds and a help text, served to the UI as a schema, so the
lab screen is generated from here and a new parameter is one line.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class Param:
    key: str
    label: str
    type: str                      # int | float | bool | choice
    default: Any
    group: str = "Signal"          # Signal | Portefeuille | Risque
    help: str = ""
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    unit: str = ""
    choices: tuple[tuple[str, str], ...] = ()   # (value, label)
    visible_if: tuple[str, Any] | None = None   # only relevant when another param has this value

    def coerce(self, raw: Any) -> Any:
        if self.type == "bool":
            if isinstance(raw, str):
                return raw.strip().lower() in ("1", "true", "yes", "on", "oui")
            return bool(raw)
        if self.type == "choice":
            value = str(raw)
            if value not in {c[0] for c in self.choices}:
                raise ValueError(f"{self.label} : valeur inconnue « {value} »")
            return value
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{self.label} : nombre attendu, reçu « {raw} »")
        if self.minimum is not None and value < self.minimum:
            raise ValueError(f"{self.label} : minimum {self.minimum:g}{self.unit}")
        if self.maximum is not None and value > self.maximum:
            raise ValueError(f"{self.label} : maximum {self.maximum:g}{self.unit}")
        return int(round(value)) if self.type == "int" else value

    def schema(self) -> dict:
        return {
            "key": self.key, "label": self.label, "type": self.type, "default": self.default,
            "group": self.group, "help": self.help, "minimum": self.minimum, "maximum": self.maximum,
            "step": self.step, "unit": self.unit,
            "choices": [{"value": v, "label": lbl} for v, lbl in self.choices],
            "visible_if": {"key": self.visible_if[0], "value": self.visible_if[1]} if self.visible_if else None,
        }


def risk_params(*, stop: float, trailing: float, take_profit: float, max_hold: int,
                cost: float = 5.0) -> list[Param]:
    """The exits every profile has, with per-profile defaults.

    Zero switches a rule off. Stops are checked once a day on the close, in
    the backtest and in paper alike — an intraday stop in paper against a
    close-only stop in the backtest would make the two impossible to compare.
    """
    return [
        Param("stop_loss_pct", "Stop de perte", "float", stop, "Risque", minimum=0, maximum=50, step=0.5,
              unit="%", help="Sortie si la position perd ce pourcentage depuis l'achat. 0 = désactivé."),
        Param("trailing_stop_pct", "Stop suiveur", "float", trailing, "Risque", minimum=0, maximum=50,
              step=0.5, unit="%",
              help="Sortie si la position recule de ce pourcentage depuis son plus haut. C'est le "
                   "détecteur de précipice : il suit la hausse et ne pardonne pas la chute. 0 = désactivé."),
        Param("take_profit_pct", "Prise de bénéfice", "float", take_profit, "Risque", minimum=0,
              maximum=500, step=1, unit="%", help="Sortie au-delà de ce gain. 0 = désactivé."),
        Param("max_hold_days", "Durée maximale", "int", max_hold, "Risque", minimum=0, maximum=1000,
              unit="séances", help="Sortie après ce nombre de séances de bourse. 0 = pas de limite."),
        Param("cost_bps", "Coût par transaction", "float", cost, "Risque", minimum=0, maximum=100,
              step=1, unit="pb",
              help="Spread et glissement payés à chaque achat et vente, en points de base (1 pb = 0,01 %). "
                   "Alpaca ne prend pas de commission ; ce coût-là est celui du marché."),
    ]


@dataclass
class Decision:
    """What a profile wants today.

    - `exits`: positions to close, with a human-readable reason.
    - `targets`: a full target portfolio as weights of equity — anything held
      and not listed is sold. `None` means "no opinion on the portfolio today".
    - `entries`: additions only (weight per new symbol) — for profiles that fill
      free slots rather than rebalancing the whole book.
    - `notes`: why, per symbol, for the decision journal.
    """
    exits: dict[str, str] = field(default_factory=dict)
    targets: dict[str, float] | None = None
    entries: dict[str, float] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Variant:
    key: str
    name: str
    description: str
    params: dict[str, Any]


class Profile:
    key: str
    label: str
    nickname: str
    horizon: str
    cadence: str                   # daily | monthly
    description: str
    data_name: str = "stocks"

    def params(self) -> list[Param]:
        raise NotImplementedError

    def variants(self) -> list[Variant]:
        raise NotImplementedError

    def universe(self, params: dict) -> list[str]:
        raise NotImplementedError

    def prepare(self, panel, params: dict) -> Any:
        """Precompute everything vectorisable once for the whole panel."""
        raise NotImplementedError

    def decide(self, ctx: Any, i: int, book, params: dict, rebalance: bool) -> Decision:
        raise NotImplementedError

    # -- helpers ------------------------------------------------------------

    def defaults(self) -> dict[str, Any]:
        return {p.key: p.default for p in self.params()}

    def resolve(self, raw: dict | None) -> dict[str, Any]:
        """Validated full parameter set: defaults overlaid with `raw`."""
        specs = {p.key: p for p in self.params()}
        out = self.defaults()
        for key, value in (raw or {}).items():
            if key not in specs:
                raise ValueError(f"paramètre inconnu pour {self.label} : {key}")
            out[key] = specs[key].coerce(value)
        return out

    def schema(self) -> dict:
        return {
            "key": self.key, "label": self.label, "nickname": self.nickname, "horizon": self.horizon,
            "cadence": self.cadence, "description": self.description,
            "params": [p.schema() for p in self.params()],
            "variants": [{"key": v.key, "name": v.name, "description": v.description, "params": v.params}
                         for v in self.variants()],
        }


def row(frame: pd.DataFrame, i: int) -> pd.Series:
    return frame.iloc[i]
