from __future__ import annotations

from app.agent import explain as explain_module
from app.agent.explain import TradeExplanation, resolve_explanation
from app.strategies.base import FactorScore, TradeIdea


def _idea() -> TradeIdea:
    return TradeIdea(
        symbol="AAPL", engine="ml", action="buy", score=78.0, confidence=0.78,
        structure="long_call", direction="bullish", size_pct_of_equity=0.015,
        rationale="Tendance prix +12% sur 63j (réel). Score composite 78/100.",
        invalidation="La tendance de prix 63j s'inverse de signe.",
        main_risk="Poids appris par régression Ridge.",
        factors=[FactorScore("price_trend", 0.8, 0.35, False, {"momentum_63d_pct": 12.0})],
    )


class _FakeMessages:
    def __init__(self, result):
        self._result = result

    def parse(self, **kwargs):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _FakeClient:
    def __init__(self, result):
        self.messages = _FakeMessages(result)


def test_resolve_explanation_falls_back_without_a_key(monkeypatch):
    monkeypatch.setattr(explain_module, "_client", lambda: None)
    idea = _idea()

    rationale, invalidation, main_risk, source = resolve_explanation(idea)

    assert source == "template"
    assert rationale == idea.rationale
    assert invalidation == idea.invalidation
    assert main_risk == idea.main_risk


def test_resolve_explanation_uses_llm_output_on_success(monkeypatch):
    canned = TradeExplanation(
        rationale="Explication détaillée générée par le modèle.",
        invalidation="Ce qui invaliderait la thèse, en détail.",
        main_risk="Le risque principal, en détail.",
    )

    class _Response:
        parsed_output = canned

    monkeypatch.setattr(explain_module, "_client", lambda: _FakeClient(_Response()))
    idea = _idea()

    rationale, invalidation, main_risk, source = resolve_explanation(idea)

    assert source == "llm"
    assert rationale == canned.rationale
    assert invalidation == canned.invalidation
    assert main_risk == canned.main_risk


def test_resolve_explanation_falls_back_when_the_llm_call_fails(monkeypatch):
    monkeypatch.setattr(explain_module, "_client", lambda: _FakeClient(RuntimeError("boom")))
    idea = _idea()

    rationale, invalidation, main_risk, source = resolve_explanation(idea)

    assert source == "template"
    assert rationale == idea.rationale
    assert invalidation == idea.invalidation
    assert main_risk == idea.main_risk
