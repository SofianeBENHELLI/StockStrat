"""Phase 4: train app/ml/features.py's factor weights instead of hand-picking
them. Ridge (linear, not a black box) so the fitted coefficients ARE the
"weights" the rest of the app already understands — weighted_score,
apply_weight_overrides, and the 5 named ML tournament variants all keep working
unchanged; see app/ml/inference.py for how a trained artifact is consumed and
app/strategies/ml_baseline.py for the fixed-weight fallback when none exists yet.

Walk-forward, not a random train/test split: shuffling would let the model see
data from AFTER the test window during training — exactly the lookahead a real
deployment could never have. Metrics are reported from the held-out,
chronologically-later split; the artifact actually shipped is then refit on the
full dataset, which is standard practice once validation is done.

Run directly: `uv run python -m app.ml.train`.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.linear_model import Ridge

from app.ml.dataset import build_training_examples
from app.ml.features import DEFAULT_WEIGHTS, FACTOR_NAMES

ARTIFACT_PATH = Path(__file__).parent / "artifacts" / "ranking_model.json"
MIN_TRAINING_EXAMPLES = 30


def split_walk_forward(df: pd.DataFrame, train_frac: float = 0.7) -> tuple[pd.DataFrame, pd.DataFrame]:
    ordered = df.sort_values("as_of").reset_index(drop=True)
    cutoff = int(len(ordered) * train_frac)
    return ordered.iloc[:cutoff], ordered.iloc[cutoff:]


def _fit(examples: pd.DataFrame) -> Ridge:
    model = Ridge(alpha=1.0)
    model.fit(examples[FACTOR_NAMES], examples["label"])
    return model


def _evaluate(model: Ridge, test: pd.DataFrame) -> dict:
    if test.empty:
        return {"directional_accuracy": None, "spearman": None, "mae": None}
    preds = model.predict(test[FACTOR_NAMES])
    actual = test["label"].to_numpy()
    directional_accuracy = float(np.mean(np.sign(preds) == np.sign(actual)))
    corr, _ = spearmanr(preds, actual) if len(preds) >= 2 else (float("nan"), 1.0)
    mae = float(np.mean(np.abs(preds - actual)))
    return {
        "directional_accuracy": round(directional_accuracy, 4),
        "spearman": None if np.isnan(corr) else round(float(corr), 4),
        "mae": round(mae, 4),
    }


def _weights_from_coefficients(model: Ridge) -> dict[str, float]:
    """Negative-coefficient factors get clipped to 0 rather than flipping sign —
    weighted_score assumes non-negative per-factor contributions, same convention
    every hand-picked and variant-override weight already follows."""
    coefs = {name: max(0.0, float(c)) for name, c in zip(FACTOR_NAMES, model.coef_)}
    total = sum(coefs.values())
    if total <= 0:
        return dict(DEFAULT_WEIGHTS)
    return {name: round(c / total, 4) for name, c in coefs.items()}


def train(
    universe: list[str], years: int = 6, sample_every: int = 20, horizon_days: int = 20,
    train_frac: float = 0.7,
) -> dict:
    dataset = build_training_examples(universe, years=years, sample_every=sample_every, horizon_days=horizon_days)
    if len(dataset) < MIN_TRAINING_EXAMPLES:
        raise ValueError(
            f"only {len(dataset)} training examples built (need >= {MIN_TRAINING_EXAMPLES}) "
            "— universe or history window too short"
        )

    train_df, test_df = split_walk_forward(dataset, train_frac)
    holdout_model = _fit(train_df)
    metrics = _evaluate(holdout_model, test_df)

    final_model = _fit(dataset)  # refit on everything once validated, for the deployed artifact
    weights = _weights_from_coefficients(final_model)
    raw_coefficients = {name: round(float(c), 4) for name, c in zip(FACTOR_NAMES, final_model.coef_)}

    artifact = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "universe": universe,
        "feature_names": FACTOR_NAMES,
        # `weights`: clipped to >=0 and renormalized, for the score composite —
        # weighted_score assumes non-negative per-factor contributions, same
        # convention every hand-picked and variant-override weight follows.
        "weights": weights,
        # `raw_coefficients`: the model's actual fitted coefficients, sign and all —
        # used for predicted_move_pct (app/ml/inference.py). Using `weights` there
        # instead would silently misreport the prediction whenever any coefficient
        # is negative (clipped to 0 for scoring, but still real for regression).
        "raw_coefficients": raw_coefficients,
        "intercept": round(float(final_model.intercept_), 4),
        "n_samples": len(dataset),
        "n_train": len(train_df),
        "n_test": len(test_df),
        "horizon_days": horizon_days,
        "metrics": metrics,
    }
    ARTIFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT_PATH.write_text(json.dumps(artifact, indent=2))
    return artifact


def main() -> dict:
    from app.strategies.ml_baseline import UNIVERSE

    artifact = train(UNIVERSE)
    print(json.dumps(artifact, indent=2))
    return artifact


if __name__ == "__main__":
    main()
