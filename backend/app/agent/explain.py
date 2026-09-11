"""Phase 5: agentic trade explanations. The rule engines (app/strategies/*)
already decided everything — symbol, structure, score, factors; this module
only rewrites that already-final decision into richer prose. It never
influences what gets proposed or executed, and a failure here (no key,
network, rate limit, timeout) must never block a trade — callers always get
usable rationale/invalidation/main_risk text via `resolve_explanation`,
falling back to the engine's own template strings exactly as before this
phase existed.

Model: Haiku 4.5 — this is a single-call rewrite of already-computed data,
not a reasoning task, and the tournament cycle calls it synchronously across
up to 14 variants, so latency matters more than the (negligible either way)
per-call cost. Plain Messages API, no tools: the engine did the deciding."""
from __future__ import annotations

import json
from functools import lru_cache

from pydantic import BaseModel

from app.core import settings_store
from app.core.config import get_settings
from app.strategies.base import TradeIdea

MODEL = "claude-haiku-4-5"

SYSTEM_PROMPT = """Tu expliques une décision de trading déjà prise par un moteur de règles \
déterministe dans une application de paper trading (simulation, aucun ordre réel). Tu ne \
décides de rien et tu ne recommandes rien — le score, le symbole et la structure sont déjà \
figés en amont ; ton seul rôle est de reformuler le raisonnement du moteur en français clair \
et bien tourné, à partir des facteurs fournis.

Règles strictes :
- N'invente aucun chiffre ou fait qui n'est pas dans les données fournies.
- Distingue toujours explicitement les facteurs marqués "réel" (dérivés de prix/volumes) des \
facteurs marqués "proxy" (estimations en attendant un flux de données réel) — ne les présente \
jamais comme équivalents.
- N'utilise jamais de formulation impérative ou de conseil ("achetez X", "il faut vendre") — \
décris ce que le moteur a fait et pourquoi, pas ce que l'utilisateur devrait faire.
- Reste concis : 2 à 4 phrases par champ.

Réponds avec trois champs :
- rationale : pourquoi le moteur a proposé ce trade, en citant les facteurs réels et proxy \
déterminants et le score.
- invalidation : quel changement de données invaliderait cette thèse.
- main_risk : le risque principal de cette position, tel qu'identifié par le moteur."""


class TradeExplanation(BaseModel):
    rationale: str
    invalidation: str
    main_risk: str


def _api_key() -> str | None:
    """The key the Administration panel is showing, which may be a database
    override of the `.env` value. Resolved per call (a SQLite read) rather than
    cached at import, so saving a key in the panel takes effect immediately
    instead of at the next restart. If the settings table is not reachable for
    any reason, fall back to the environment — a missing key already has a
    defined behaviour here (templated text), and a settings lookup must never
    be the thing that breaks explanations."""
    try:
        from app.core.db import SessionLocal

        db = SessionLocal()
        try:
            return settings_store.resolve(db, "connections.anthropic_api_key")
        finally:
            db.close()
    except Exception:
        return get_settings().anthropic_api_key


@lru_cache
def _client_for(api_key: str | None):
    """Cached per key so a client is not rebuilt on every explanation, and so
    changing the key in the panel builds a new one rather than reusing the old."""
    if not api_key:
        return None
    import anthropic

    return anthropic.Anthropic(api_key=api_key)


def _client():
    return _client_for(_api_key())


def _idea_context(idea: TradeIdea) -> str:
    return json.dumps({
        "symbol": idea.symbol,
        "engine": idea.engine,
        "score": idea.score,
        "confidence": idea.confidence,
        "structure": idea.structure,
        "direction": idea.direction,
        "size_pct_of_equity": idea.size_pct_of_equity,
        "predicted_move_pct": idea.predicted_move_pct,
        "expected_vol_pct": idea.expected_vol_pct,
        "factors": idea.factor_breakdown(),
        "engine_rationale_draft": idea.rationale,
        "engine_invalidation_draft": idea.invalidation,
        "engine_main_risk_draft": idea.main_risk,
    }, ensure_ascii=False, default=str)


def explain_trade(idea: TradeIdea) -> TradeExplanation | None:
    client = _client()
    if client is None:
        return None
    try:
        response = client.messages.parse(
            model=MODEL,
            max_tokens=800,
            timeout=15.0,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": _idea_context(idea)}],
            output_format=TradeExplanation,
        )
        return response.parsed_output
    except Exception:
        return None


def resolve_explanation(idea: TradeIdea) -> tuple[str, str, str, str]:
    """Never raises. Returns (rationale, invalidation, main_risk, source) —
    source is "llm" on success, "template" whenever the LLM path didn't run
    (no key, or any failure) — the engine's own draft text either way."""
    explanation = explain_trade(idea)
    if explanation is not None:
        return explanation.rationale, explanation.invalidation, explanation.main_risk, "llm"
    return idea.rationale, idea.invalidation, idea.main_risk, "template"
