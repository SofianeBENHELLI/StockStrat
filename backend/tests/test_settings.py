"""The Administration panel's settings store.

Two properties matter more than the rest: the environment keeps working when
the database is empty, and a stored secret cannot be read back out.
"""
from __future__ import annotations

import pytest

from app.core import settings_store


def _field(view: dict, key: str) -> dict:
    for group in view["groups"]:
        for f in group["fields"]:
            if f["key"] == key:
                return f
    raise AssertionError(f"{key} not in the public view")


def test_an_empty_database_falls_back_to_declared_defaults(db):
    assert settings_store.resolve(db, "trading.exit_stop_loss_pct") == -8.0
    assert settings_store.resolve(db, "monitor.enabled") is True
    assert _field(settings_store.public_view(db), "trading.exit_stop_loss_pct")["source"] == "environment"


def test_an_override_wins_and_is_reported_as_such(db):
    settings_store.apply_updates(db, {"trading.exit_stop_loss_pct": -12.5})
    assert settings_store.resolve(db, "trading.exit_stop_loss_pct") == -12.5
    field = _field(settings_store.public_view(db), "trading.exit_stop_loss_pct")
    assert field["value"] == -12.5
    assert field["source"] == "database" and field["overridden"] is True


def test_values_are_coerced_and_bounded(db):
    settings_store.apply_updates(db, {"monitor.interval_seconds": "90"})
    assert settings_store.resolve(db, "monitor.interval_seconds") == 90

    with pytest.raises(ValueError, match="at least"):
        settings_store.apply_updates(db, {"monitor.interval_seconds": 1})
    with pytest.raises(ValueError, match="expected a number"):
        settings_store.apply_updates(db, {"trading.exit_take_profit_pct": "soon"})
    with pytest.raises(ValueError, match="must be one of"):
        settings_store.apply_updates(db, {"data.market_data_provider": "bloomberg"})


def test_a_secret_never_comes_back_out(db):
    settings_store.apply_updates(db, {"connections.anthropic_api_key": "sk-ant-secretvalue1234"})

    field = _field(settings_store.public_view(db), "connections.anthropic_api_key")
    assert field["value"] is None
    assert field["configured"] is True
    assert field["masked"] == "••••1234"

    # The clear text must not appear anywhere in what the browser receives.
    assert "secretvalue" not in str(settings_store.public_view(db))
    # ...but a server-side caller can still use it.
    assert settings_store.resolve(db, "connections.anthropic_api_key") == "sk-ant-secretvalue1234"


def test_clearing_a_secret_drops_the_override(db):
    settings_store.apply_updates(db, {"connections.anthropic_api_key": "sk-ant-abc12345"})
    settings_store.apply_updates(db, {"connections.anthropic_api_key": ""})
    field = _field(settings_store.public_view(db), "connections.anthropic_api_key")
    assert field["overridden"] is False


def test_unknown_and_unavailable_keys_are_refused_rather_than_ignored(db):
    with pytest.raises(ValueError, match="unknown setting"):
        settings_store.apply_updates(db, {"trading.not_a_real_setting": 1})
    with pytest.raises(ValueError, match="not available yet"):
        settings_store.apply_updates(db, {"connections.alpaca_api_key": "PK123"})
