"""Named sub-variants per engine, per the spec's tournament roster. Each variant
is the SAME underlying engine (same signals, same guardrails) run with a
different weight emphasis and, where the spec names a distinct sub-strategy
(e.g. Casino's 'short-squeeze calls' vs 'post-news reversal puts'), a different
filter/structure/direction bias. This is what makes them genuinely different
bets rather than four copies of the same variant with different labels.

weight_overrides replaces the engine's default weight for any factor named
here; factors not listed keep their default weight (so a variant can sharpen
emphasis without having to repeat every factor)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class VariantConfig:
    key: str
    engine: str
    name: str
    description: str
    weight_overrides: dict[str, float] = field(default_factory=dict)
    direction_filter: str | None = None  # force "bullish" | "bearish" candidates only
    structure_hint: str | None = None    # engine-specific structure-family hint


CASINO_VARIANTS: dict[str, VariantConfig] = {
    "A": VariantConfig(
        key="A", engine="casino", name="Earnings Straddles",
        description="Catalyseur imminent + IV élevée priorisés — parie sur l'amplitude du mouvement, pas sa direction.",
        weight_overrides={"catalyst_strength": 0.40, "iv_opportunity": 0.25, "price_momentum": 0.05,
                          "news_shock": 0.05, "short_squeeze": 0.0},
        structure_hint="vol",
    ),
    "B": VariantConfig(
        key="B", engine="casino", name="Momentum Call Spreads",
        description="Momentum haussier fort + catalyseur — parie sur la direction avec un coût de prime plafonné.",
        weight_overrides={"price_momentum": 0.35, "catalyst_strength": 0.20, "iv_opportunity": 0.05,
                          "short_squeeze": 0.0},
        direction_filter="bullish", structure_hint="directional_spread",
    ),
    "C": VariantConfig(
        key="C", engine="casino", name="Short-Squeeze Calls",
        description="Priorise le potentiel de short squeeze — calls directs, conviction directionnelle forte.",
        weight_overrides={"short_squeeze": 0.35, "abnormal_volume": 0.30, "catalyst_strength": 0.10,
                          "iv_opportunity": 0.05},
        direction_filter="bullish", structure_hint="outright",
    ),
    "D": VariantConfig(
        key="D", engine="casino", name="Post-News Reversal Puts",
        description="Choc de news négatif après une hausse récente — parie sur un retournement baissier.",
        weight_overrides={"news_shock": 0.35, "price_momentum": 0.05, "catalyst_strength": 0.15,
                          "short_squeeze": 0.0},
        direction_filter="bearish", structure_hint="directional_spread",
    ),
}

ML_VARIANTS: dict[str, VariantConfig] = {
    "A": VariantConfig(
        key="A", engine="ml", name="Momentum Only",
        description="Un seul facteur : la tendance de prix. Baseline pour juger si les autres familles de signaux ajoutent de la valeur.",
        weight_overrides={"price_trend": 1.0, "fundamentals": 0.0, "options": 0.0, "news": 0.0, "macro": 0.0},
    ),
    "B": VariantConfig(
        key="B", engine="ml", name="Fondamentaux + Prix",
        description="Combine tendance de prix et qualité fondamentale, ignore news/options/macro.",
        weight_overrides={"price_trend": 0.55, "fundamentals": 0.45, "options": 0.0, "news": 0.0, "macro": 0.0},
    ),
    "C": VariantConfig(
        key="C", engine="ml", name="Sentiment News + Flux Options",
        description="Ignore le prix et les fondamentaux — parie uniquement sur le sentiment news et la microstructure options.",
        weight_overrides={"price_trend": 0.0, "fundamentals": 0.0, "options": 0.55, "news": 0.45, "macro": 0.0},
    ),
    "D": VariantConfig(
        key="D", engine="ml", name="Régime Macro + Rotation",
        description="Le régime macro domine le score — pari sur la rotation sectorielle plutôt que le stock-picking.",
        weight_overrides={"price_trend": 0.25, "fundamentals": 0.0, "options": 0.0, "news": 0.0, "macro": 0.75},
    ),
    "E": VariantConfig(
        key="E", engine="ml", name="Ensemble",
        description="Le mix par défaut à 5 facteurs — le variant contre lequel les autres se mesurent.",
        weight_overrides={},
    ),
}

ECONOMIST_VARIANTS: dict[str, VariantConfig] = {
    "A": VariantConfig(key="A", engine="economist", name="Infrastructure IA",
                       description="Thème unique : infrastructure IA.", weight_overrides={}),
    "B": VariantConfig(key="B", engine="economist", name="Demande d'Énergie",
                       description="Thème unique : demande d'énergie.", weight_overrides={}),
    "C": VariantConfig(key="C", engine="economist", name="Défense",
                       description="Thème unique : défense.", weight_overrides={}),
    "D": VariantConfig(key="D", engine="economist", name="Bénéficiaires de Baisse de Taux",
                       description="Thème unique : promoteurs immobiliers / foncières sensibles aux taux.",
                       weight_overrides={}),
    "E": VariantConfig(key="E", engine="economist", name="Quantique & Réindustrialisation",
                       description="Thème unique : réindustrialisation et informatique quantique.",
                       weight_overrides={}),
}

# Maps each Economist variant to the theme(s) it's restricted to (single-theme variants
# per the spec's roster; the un-parametrized engine still screens all themes).
ECONOMIST_VARIANT_THEMES: dict[str, list[str]] = {
    "A": ["Infrastructure IA"],
    "B": ["Demande d'énergie"],
    "C": ["Défense"],
    "D": ["Bénéficiaires de baisse de taux"],
    "E": ["Réindustrialisation & quantique"],
}

ALL_VARIANTS: dict[str, dict[str, VariantConfig]] = {
    "casino": CASINO_VARIANTS, "ml": ML_VARIANTS, "economist": ECONOMIST_VARIANTS,
}


def get_variant_config(engine: str, key: str) -> VariantConfig:
    cfg = ALL_VARIANTS.get(engine, {}).get(key)
    if cfg is None:
        raise KeyError(f"no variant '{key}' for engine '{engine}'")
    return cfg
