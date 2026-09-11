"""Seed the full tournament roster from the spec: Casino A-D, ML A-E, Economist
A-E (14 variants), one paper portfolio each. Idempotent — running it again only
creates variants that don't already exist (matched by engine + variant_key
among non-killed, generation-0 variants), so it's safe to call on every app
start or from a 'reset roster' button without duplicating."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PaperPortfolio, Variant
from app.tournament.variants_config import ALL_VARIANTS

DEFAULT_INITIAL_CASH = 100_000.0


def seed_roster(db: Session, initial_cash: float = DEFAULT_INITIAL_CASH) -> list[Variant]:
    created: list[Variant] = []
    for engine, variants in ALL_VARIANTS.items():
        for key, cfg in variants.items():
            existing = db.scalar(
                select(Variant).where(Variant.engine == engine, Variant.variant_key == key,
                                      Variant.generation == 0)
            )
            if existing:
                continue
            variant = Variant(
                name=f"{cfg.name} ({engine.upper()}-{key})", engine=engine, variant_key=key,
                description=cfg.description, generation=0,
            )
            db.add(variant)
            db.commit()
            db.refresh(variant)
            portfolio = PaperPortfolio(variant_id=variant.id, initial_cash=initial_cash, cash=initial_cash)
            db.add(portfolio)
            db.commit()
            created.append(variant)
    return created
