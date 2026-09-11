"""Runtime consumer of app/ml/train.py's artifact. Deliberately dependency-light
— no sklearn import, no unpickling a model object: the artifact is a JSON dict of
weights + an intercept, and prediction is a dot product. Keeps the request path
free of the training-only dependency and lets anyone read the deployed weights
in a text editor. Returns None everywhere when no artifact exists yet, so
app/strategies/ml_baseline.py can fall back to its fixed-weight baseline — same
"clearly labeled, never fabricated" fallback philosophy as the mock price feed."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from app.strategies.base import FactorScore

ARTIFACT_PATH = Path(__file__).parent / "artifacts" / "ranking_model.json"

_lock = threading.Lock()
_cache: dict | None = None
_cache_mtime: float | None = None


def _load() -> dict | None:
    global _cache, _cache_mtime
    with _lock:
        if not ARTIFACT_PATH.exists():
            _cache, _cache_mtime = None, None
            return None
        mtime = ARTIFACT_PATH.stat().st_mtime
        if _cache is None or mtime != _cache_mtime:
            _cache = json.loads(ARTIFACT_PATH.read_text())
            _cache_mtime = mtime
        return _cache


def model_metadata() -> dict | None:
    return _load()


def learned_weights() -> dict[str, float] | None:
    meta = _load()
    return meta["weights"] if meta else None


def predict_forward_move(factors: list[FactorScore]) -> float | None:
    """Uses the model's actual signed coefficients (not the clipped, renormalized
    `weights` used for score ranking) — the regression's predicted move must
    reflect what was really fitted, including any negative relationship."""
    meta = _load()
    if meta is None:
        return None
    coefficients = meta["raw_coefficients"]
    value = meta["intercept"] + sum(f.value * coefficients.get(f.name, 0.0) for f in factors)
    return round(value, 2)
