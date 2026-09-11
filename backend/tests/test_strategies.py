from __future__ import annotations

from app.strategies import casino, economist, ml_baseline
from app.strategies.base import size_from_confidence

VALID_STRUCTURES = {
    "long_call", "long_put", "debit_spread_call", "debit_spread_put",
    "straddle", "strangle", "calendar_spread", "leaps_call", "collar",
}


def test_casino_ideas_are_well_formed():
    ideas = casino.generate_ideas()
    assert isinstance(ideas, list)
    for idea in ideas:
        assert idea.engine == "casino"
        assert idea.structure in VALID_STRUCTURES
        assert 0 <= idea.score <= 100
        assert 0.005 <= idea.size_pct_of_equity <= 0.02
        assert idea.rationale and idea.invalidation and idea.main_risk
    # sorted descending by score
    scores = [i.score for i in ideas]
    assert scores == sorted(scores, reverse=True)


def test_casino_never_picks_naked_short_structures():
    # V1 guardrail: only long-premium structures (no bare short legs)
    ideas = casino.generate_ideas(limit=50)
    for idea in ideas:
        assert idea.structure in VALID_STRUCTURES


def test_ml_baseline_ideas_have_predicted_move_and_confidence():
    ideas = ml_baseline.generate_ideas()
    for idea in ideas:
        assert idea.engine == "ml"
        assert idea.predicted_move_pct is not None
        assert idea.expected_vol_pct is not None
        assert 0 <= idea.confidence <= 1
        assert idea.structure in VALID_STRUCTURES


def test_economist_never_picks_short_dated_structures():
    long_dated_only = {"leaps_call", "debit_spread_call", "collar"}
    ideas = economist.generate_ideas(limit=50)
    for idea in ideas:
        assert idea.engine == "economist"
        assert idea.structure in long_dated_only, f"{idea.symbol} got short-dated structure {idea.structure}"


def test_size_from_confidence_stays_within_bounds():
    assert size_from_confidence(0.0, 0.005, 0.02) == 0.005
    assert size_from_confidence(1.0, 0.005, 0.02) == 0.02
    mid = size_from_confidence(0.5, 0.005, 0.02)
    assert 0.005 < mid < 0.02
