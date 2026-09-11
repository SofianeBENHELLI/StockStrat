from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.data import macro as macro_module
from app.data.provider import MockProvider
from app.ml.features import compute_factors
from app.signals import proxy


@pytest.fixture(autouse=True)
def reset_macro_cache():
    macro_module._cache = None
    yield
    macro_module._cache = None


def _synthetic_raw(n: int = 300, seed: int = 3) -> dict[str, pd.Series]:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=n)

    def drifting(base: float, vol: float) -> pd.Series:
        return pd.Series(base + rng.normal(0, vol, n).cumsum(), index=dates)

    return {
        "vix": pd.Series(np.abs(rng.normal(18, 4, n)), index=dates),
        "rate10y": drifting(4.0, 0.01),
        "gold": drifting(1900, 2.0),
        "oil": drifting(75, 1.0),
        "dollar": drifting(100, 0.3),
    }


def test_macro_backdrop_uses_real_source_when_fetch_succeeds(monkeypatch):
    raw = _synthetic_raw()
    monkeypatch.setattr(macro_module, "_fetch_raw", lambda days=macro_module.FETCH_DAYS: raw)

    as_of = raw["vix"].index[150].date()
    result = macro_module.macro_backdrop(as_of)

    assert result["source"] == "real"
    assert 0.0 <= result["regime_score"] <= 1.0


def test_macro_backdrop_has_no_lookahead(monkeypatch):
    raw = _synthetic_raw()
    monkeypatch.setattr(macro_module, "_fetch_raw", lambda days=macro_module.FETCH_DAYS: raw)
    as_of = raw["vix"].index[150].date()
    result_before = macro_module.macro_backdrop(as_of)

    # mutate everything strictly AFTER as_of — the result for `as_of` must not change,
    # since rolling/pct_change stats only ever look backward.
    mutated = {name: s.copy() for name, s in raw.items()}
    for s in mutated.values():
        s.iloc[151:] = s.iloc[151:] * 5 + 1000
    macro_module._cache = None
    monkeypatch.setattr(macro_module, "_fetch_raw", lambda days=macro_module.FETCH_DAYS: mutated)

    result_after = macro_module.macro_backdrop(as_of)
    assert result_after == result_before


def test_macro_backdrop_falls_back_to_proxy_when_fetch_fails(monkeypatch):
    monkeypatch.setattr(macro_module, "_fetch_raw", lambda days=macro_module.FETCH_DAYS: None)

    as_of = date(2024, 3, 15)
    result = macro_module.macro_backdrop(as_of)

    assert result == proxy.macro_backdrop(as_of)
    assert result["source"] == "proxy"


def test_compute_factors_macro_is_proxy_matches_source(monkeypatch):
    monkeypatch.setattr(macro_module, "_fetch_raw", lambda days=macro_module.FETCH_DAYS: None)

    df = MockProvider().history(["TEST"], 260)["TEST"].df
    factors = compute_factors("TEST", df, df.index[-1].date())

    macro_factor = next(f for f in factors if f.name == "macro")
    assert macro_factor.is_proxy is True  # fetch failed -> proxy fallback, correctly tagged
