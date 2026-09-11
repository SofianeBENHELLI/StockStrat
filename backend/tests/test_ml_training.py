from __future__ import annotations

import numpy as np
import pandas as pd

from app.data.provider import MockProvider, PriceHistory
from app.ml import dataset as dataset_module
from app.ml import inference
from app.ml import train as train_module
from app.ml.features import FACTOR_NAMES, compute_factors
from app.strategies import ml_baseline


def _mock_df(days: int) -> pd.DataFrame:
    return MockProvider().history(["TEST"], days)["TEST"].df


def test_compute_factors_returns_none_below_history_floor():
    df = _mock_df(20)
    assert compute_factors("TEST", df, df.index[-1].date()) is None


def test_compute_factors_returns_all_factors_above_floor():
    df = _mock_df(260)
    factors = compute_factors("TEST", df, df.index[-1].date())
    assert factors is not None
    assert sorted(f.name for f in factors) == sorted(FACTOR_NAMES)
    assert all(0.0 <= f.value <= 1.0 for f in factors)


def test_build_training_examples_has_no_lookahead(monkeypatch):
    df = _mock_df(400)
    history = PriceHistory(symbol="TEST", df=df, source="mock")
    monkeypatch.setattr(dataset_module, "price_history", lambda universe, days: {"TEST": history})

    seen: list[tuple] = []
    original = dataset_module.compute_factors

    def spy(symbol, hist_df, as_of):
        seen.append((as_of, hist_df.index[-1].date()))
        return original(symbol, hist_df, as_of)

    monkeypatch.setattr(dataset_module, "compute_factors", spy)

    examples = dataset_module.build_training_examples(["TEST"], years=1, sample_every=10, horizon_days=20)

    assert len(examples) > 0
    assert len(seen) > 0
    # the history slice handed to compute_factors never extends past its own as_of date
    for as_of, hist_last_date in seen:
        assert hist_last_date == as_of


def _synthetic_dataset(n: int = 200, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=n)
    price_trend = rng.uniform(0, 1, n)
    noise = rng.uniform(0, 1, size=(n, 4))
    label = price_trend * 20 - 10 + rng.normal(0, 0.5, n)  # strong real signal, rest is noise
    return pd.DataFrame({
        "symbol": ["TEST"] * n,
        "as_of": dates.date,
        "price_trend": price_trend,
        "fundamentals": noise[:, 0],
        "options": noise[:, 1],
        "news": noise[:, 2],
        "macro": noise[:, 3],
        "label": label,
    })


def test_split_walk_forward_is_chronological():
    shuffled = _synthetic_dataset(n=50).sample(frac=1, random_state=1).reset_index(drop=True)
    train_df, test_df = train_module.split_walk_forward(shuffled, train_frac=0.7)
    assert len(train_df) + len(test_df) == len(shuffled)
    assert train_df["as_of"].max() <= test_df["as_of"].min()


def test_train_learns_dominant_factor_and_saves_artifact(monkeypatch, tmp_path):
    synthetic = _synthetic_dataset()
    monkeypatch.setattr(train_module, "build_training_examples", lambda *a, **kw: synthetic)
    artifact_path = tmp_path / "ranking_model.json"
    monkeypatch.setattr(train_module, "ARTIFACT_PATH", artifact_path)

    artifact = train_module.train(["TEST"], years=1, sample_every=1, horizon_days=20)

    assert artifact_path.exists()
    weights = artifact["weights"]
    assert set(weights) == set(FACTOR_NAMES)
    assert all(w >= 0 for w in weights.values())
    assert abs(sum(weights.values()) - 1.0) < 0.01
    assert weights["price_trend"] == max(weights.values())
    assert artifact["metrics"]["directional_accuracy"] is not None
    assert artifact["n_samples"] == len(synthetic)


def test_ml_baseline_falls_back_without_trained_model(monkeypatch):
    monkeypatch.setattr(ml_baseline, "price_history",
                         lambda universe, days: MockProvider().history(universe, days))
    monkeypatch.setattr(ml_baseline, "SCORE_THRESHOLD", -100.0)  # guarantee non-empty ideas
    monkeypatch.setattr(inference, "model_metadata", lambda: None)

    ideas = ml_baseline.generate_ideas(limit=50)
    assert len(ideas) > 0
    for idea in ideas:
        assert "pas encore entraîné" in idea.rationale
        assert idea.predicted_move_pct is not None


def test_ml_baseline_uses_trained_model_when_present(monkeypatch):
    monkeypatch.setattr(ml_baseline, "price_history",
                         lambda universe, days: MockProvider().history(universe, days))
    monkeypatch.setattr(ml_baseline, "SCORE_THRESHOLD", -100.0)  # guarantee non-empty ideas
    fake_meta = {
        "weights": {"price_trend": 1.0, "fundamentals": 0.0, "options": 0.0, "news": 0.0, "macro": 0.0},
        "intercept": 0.0,
        "n_samples": 500,
        "metrics": {"directional_accuracy": 0.62, "spearman": 0.3, "mae": 4.1},
    }
    monkeypatch.setattr(inference, "model_metadata", lambda: fake_meta)
    monkeypatch.setattr(inference, "predict_forward_move", lambda factors: 3.5)

    ideas = ml_baseline.generate_ideas(limit=50)
    assert len(ideas) > 0
    for idea in ideas:
        assert "Modèle Ridge entraîné" in idea.rationale
        assert idea.predicted_move_pct == 3.5
