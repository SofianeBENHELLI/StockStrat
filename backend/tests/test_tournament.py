from __future__ import annotations

from app.models import Variant
from app.tournament.cycle import run_cycle
from app.tournament.seed import seed_roster
from app.tournament.variants_config import ALL_VARIANTS


def test_seed_roster_creates_full_spec_lineup(db):
    created = seed_roster(db)
    assert len(created) == sum(len(v) for v in ALL_VARIANTS.values())  # 4 casino + 5 ml + 5 economist = 14
    assert len(created) == 14

    variants = db.query(Variant).all()
    by_engine: dict[str, int] = {}
    for v in variants:
        by_engine[v.engine] = by_engine.get(v.engine, 0) + 1
    assert by_engine == {"casino": 4, "ml": 5, "economist": 5}
    for v in variants:
        assert v.portfolio is not None
        assert v.portfolio.cash == 100_000.0


def test_seed_roster_is_idempotent(db):
    first = seed_roster(db)
    second = seed_roster(db)
    assert len(first) == 14
    assert len(second) == 0  # nothing new created on re-seed
    assert db.query(Variant).count() == 14


def test_cycle_runs_without_crashing_on_empty_roster(db):
    run = run_cycle(db)
    assert run.status == "done"
    assert run.detail is not None
    assert "proposed" in run.detail and "killed" in run.detail and "scaled" in run.detail


def test_cycle_runs_against_seeded_roster(db):
    seed_roster(db)
    run = run_cycle(db)
    assert run.status == "done"
    # every active variant should have gotten at least a snapshot attempt (no crash),
    # even if zero orders were placed (e.g. casino's real-data volume gate).
    assert isinstance(run.detail["proposed"], list)
