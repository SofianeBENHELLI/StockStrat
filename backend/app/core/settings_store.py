"""Runtime-editable settings, layered over the environment.

Three rules this module exists to enforce:

1. **The environment stays the default, never the only source.** Every field
   declares where its default comes from (usually `app/core/config.py`, i.e.
   `.env`); a row in `app_settings` overrides it. A fresh database therefore
   boots with zero rows and behaves exactly like before this module existed.

2. **Secrets go in, never out.** A `secret` field accepts a new value and
   reports whether one is configured plus its last 4 characters, but its clear
   text is only ever returned by `resolve()`, which is for server-side callers.
   `public_view()` — what the Administration panel reads — cannot leak it.

3. **The schema is data, not code.** `FIELDS` below is the single description
   of every setting: type, default, bounds, help text and group. The API serves
   it and the UI renders whatever it is given, so adding a setting is one entry
   here rather than a backend change plus a matching frontend change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AppSetting

FieldType = Literal["float", "int", "bool", "str", "enum", "secret"]


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    type: FieldType
    group: str
    default: Any = None
    env_attr: str | None = None       # attribute on core.config.Settings supplying the default
    help: str = ""
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    unit: str = ""
    # A field can be visible and stored while the feature that consumes it is not
    # built yet; say so explicitly rather than shipping a control that silently
    # does nothing.
    available: bool = True
    unavailable_reason: str = ""


FIELDS: tuple[Field, ...] = (
    # ---- Execution (app/paper/brokers.py) ----------------------------------
    Field("execution.default_broker", "Broker des modèles déployés", "enum", "execution", default="alpaca_paper",
          choices=("sim", "alpaca_paper"),
          help="Qui exécute les ordres. « sim » est le simulateur de fills intégré "
               "(spread modélisé, slippage, fills partiels)."),
    Field("execution.max_order_notional", "Taille maximale d'un ordre", "float", "execution", default=25_000.0,
          minimum=1.0, unit="$",
          help="Plafond strict par ordre, vérifié avant tout appel au broker."),

    # ---- Monitor (app/monitor/scheduler.py) --------------------------------
    Field("monitor.enabled", "Monitor en arrière-plan", "bool", "monitor", default=True,
          help="Sonde les ordres en attente, applique les règles de sortie et enregistre l'équité. "
               "Sans lui, l'équité n'est relevée que lorsqu'on lance un cycle à la main, ce qui fausse "
               "le Sharpe et le drawdown."),
    Field("monitor.decision_minutes_before_close", "Heure de décision", "int", "monitor", default=15,
          minimum=2, maximum=390, unit="min avant clôture",
          help="Les modèles décident chaque jour ce nombre de minutes avant la clôture de Wall Street, "
               "sur le dernier prix — l'équivalent réel de la clôture utilisée par le backtest."),
    Field("execution.safety_stop_pct", "Stop de secours chez le broker", "float", "execution", default=25.0,
          minimum=0, maximum=90, unit="%",
          help="Chaque position garde chez Alpaca un ordre stop permanent à ce pourcentage sous son prix "
               "d'achat. Il protège quand l'application est arrêtée ; en temps normal les stops des modèles "
               "(vérifiés à la clôture) agissent bien avant. Actions entières uniquement. 0 = désactivé."),
    Field("execution.max_total_allocation", "Capital total alloué aux modèles", "float", "execution",
          default=95_000.0, minimum=100.0, unit="$",
          help="Plafond de la somme des budgets des modèles en paper. Le compte Alpaca a 100 000 $ de "
               "cash et 400 000 $ de pouvoir d'achat avec marge : ce plafond garantit qu'on n'emprunte jamais."),
    Field("monitor.interval_seconds", "Période de sondage", "int", "monitor", default=60,
          env_attr="quote_refresh_seconds", minimum=10, maximum=3600, unit="s",
          help="Fréquence d'exécution du passage du monitor."),

    # ---- Market data (app/data/provider.py) --------------------------------
    Field("data.market_data_provider", "Source des prix", "enum", "data", default="alpaca",
          choices=("alpaca", "mock"),
          help="« alpaca » : dernier trade réel. Sans prix, un ordre est refusé — jamais passé sur un "
               "prix inventé. « mock » : prix fictifs déterministes, pour les tests hors ligne uniquement."),

    # ---- Connections -------------------------------------------------------
    Field("connections.alpaca_api_key", "Alpaca paper — identifiant de clé API", "secret", "connections",
          env_attr="alpaca_api_key",
          help="Commence par PK. Généré depuis le tableau de bord Alpaca, côté Paper. "
               "Une clé paper ne peut atteindre que l'endpoint paper."),
    Field("connections.alpaca_secret_key", "Alpaca paper — clé secrète", "secret", "connections",
          env_attr="alpaca_secret_key",
          help="Affichée une seule fois par Alpaca à la génération. Si elle est perdue, il faut "
               "régénérer la paire."),
)

BY_KEY: dict[str, Field] = {f.key: f for f in FIELDS}

GROUP_LABELS: dict[str, tuple[str, str]] = {
    "execution": ("Exécution", "Qui exécute les ordres, et le plafond d'un ordre unitaire."),
    "monitor": ("Monitor", "La boucle de fond qui sonde les ordres en attente et enregistre l'équité."),
    "data": ("Données de marché", "D'où viennent les prix."),
    "connections": ("Connexions", "Identifiants des services externes. Stockés côté serveur ; jamais "
                                  "renvoyés au navigateur une fois enregistrés."),
}


def _env_default(f: Field) -> Any:
    if f.env_attr:
        value = getattr(get_settings(), f.env_attr, None)
        if value is not None and value != "":
            return value
    return f.default


def _rows(db: Session) -> dict[str, Any]:
    return {row.key: (row.value or {}).get("v") for row in db.scalars(select(AppSetting))}


def _coerce(f: Field, raw: Any) -> Any:
    """Validate and convert one incoming value. Raises ValueError with a message
    meant to be shown to the person editing the field."""
    if f.type == "bool":
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "yes", "on")
        return bool(raw)
    if f.type in ("float", "int"):
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{f.label}: expected a number, got {raw!r}")
        if f.type == "int":
            value = int(round(value))
        if f.minimum is not None and value < f.minimum:
            raise ValueError(f"{f.label}: must be at least {f.minimum}{f.unit}")
        if f.maximum is not None and value > f.maximum:
            raise ValueError(f"{f.label}: must be at most {f.maximum}{f.unit}")
        return value
    if f.type == "enum":
        value = str(raw)
        if value not in f.choices:
            raise ValueError(f"{f.label}: must be one of {', '.join(f.choices)}")
        return value
    return str(raw)


def resolve(db: Session, key: str) -> Any:
    """The effective value: database override if present, else the environment
    default. Server-side callers only — returns secrets in clear text."""
    f = BY_KEY[key]
    stored = _rows(db).get(key)
    if stored is None or stored == "":
        return _env_default(f)
    return stored


def resolve_all(db: Session) -> dict[str, Any]:
    stored = _rows(db)
    out = {}
    for f in FIELDS:
        value = stored.get(f.key)
        out[f.key] = _env_default(f) if value is None or value == "" else value
    return out


def _mask(value: Any) -> str:
    text = str(value or "")
    if len(text) <= 4:
        return "••••" if text else ""
    return f"••••{text[-4:]}"


def public_view(db: Session) -> dict:
    """What the Administration panel renders. Secrets are reduced to
    configured/last-4 here and cannot be recovered from this payload."""
    values = resolve_all(db)
    stored = _rows(db)
    groups: dict[str, dict] = {}
    for f in FIELDS:
        label, description = GROUP_LABELS[f.group]
        g = groups.setdefault(f.group, {"key": f.group, "label": label, "description": description, "fields": []})
        entry = {
            "key": f.key, "label": f.label, "type": f.type, "help": f.help,
            "unit": f.unit, "choices": list(f.choices),
            "minimum": f.minimum, "maximum": f.maximum,
            "available": f.available, "unavailable_reason": f.unavailable_reason,
            "overridden": f.key in stored and stored[f.key] not in (None, ""),
        }
        if f.type == "secret":
            entry["value"] = None
            entry["configured"] = bool(values[f.key])
            entry["masked"] = _mask(values[f.key])
            entry["source"] = "database" if entry["overridden"] else ("environment" if entry["configured"] else "unset")
        else:
            entry["value"] = values[f.key]
            entry["source"] = "database" if entry["overridden"] else "environment"
        g["fields"].append(entry)
    return {"groups": list(groups.values())}


def apply_updates(db: Session, updates: dict[str, Any]) -> list[str]:
    """Write incoming values. Unknown keys and unavailable fields are refused
    rather than silently dropped. An empty string on a secret clears the
    override and falls back to the environment."""
    unknown = [k for k in updates if k not in BY_KEY]
    if unknown:
        raise ValueError(f"unknown setting(s): {', '.join(sorted(unknown))}")
    blocked = [k for k in updates if not BY_KEY[k].available]
    if blocked:
        raise ValueError(f"setting(s) not available yet: {', '.join(sorted(blocked))}")

    changed = []
    for key, raw in updates.items():
        f = BY_KEY[key]
        row = db.get(AppSetting, key)
        if f.type == "secret" and (raw is None or str(raw).strip() == ""):
            if row is not None:
                db.delete(row)
                changed.append(key)
            continue
        value = _coerce(f, raw)
        if row is None:
            db.add(AppSetting(key=key, value={"v": value}))
        else:
            row.value = {"v": value}
        changed.append(key)
    db.commit()
    return changed
