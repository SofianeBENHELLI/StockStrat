"""Secrets encrypted at rest, and backups that are safe to keep."""
from __future__ import annotations

import sqlite3

import pytest
from cryptography.fernet import Fernet

from app.core import backup, secrets_box, settings_store
from app.models import AppSetting


def test_a_secret_is_unreadable_in_the_database_but_usable_by_the_server(db):
    settings_store.apply_updates(db, {"connections.alpaca_secret_key": "super-secret-value"})
    raw = db.get(AppSetting, "connections.alpaca_secret_key").value["v"]
    assert raw.startswith("enc:v1:") and "super-secret" not in raw
    assert settings_store.resolve(db, "connections.alpaca_secret_key") == "super-secret-value"
    assert "super-secret" not in str(settings_store.public_view(db))


def test_secrets_stored_in_clear_before_encryption_are_migrated(db):
    db.add(AppSetting(key="connections.alpaca_api_key", value={"v": "PKLEGACY123"}))
    db.commit()
    assert settings_store.encrypt_plaintext_secrets(db) == 1
    assert db.get(AppSetting, "connections.alpaca_api_key").value["v"].startswith("enc:v1:")
    assert settings_store.resolve(db, "connections.alpaca_api_key") == "PKLEGACY123"
    assert settings_store.encrypt_plaintext_secrets(db) == 0        # idempotent


def test_non_secret_settings_stay_plain(db):
    settings_store.apply_updates(db, {"execution.max_total_allocation": 50_000})
    assert db.get(AppSetting, "execution.max_total_allocation").value["v"] == 50_000


def test_a_secret_from_another_key_reads_as_unset_instead_of_crashing(db, monkeypatch):
    settings_store.apply_updates(db, {"connections.alpaca_secret_key": "abc"})
    monkeypatch.setenv("STOCKSTRAT_SECRET_KEY", Fernet.generate_key().decode())
    secrets_box._fernet.cache_clear()
    try:
        assert settings_store.resolve(db, "connections.alpaca_secret_key") is None
    finally:
        monkeypatch.undo()
        secrets_box._fernet.cache_clear()


def test_backups_copy_the_database_and_keep_only_the_most_recent(tmp_path, monkeypatch):
    src = tmp_path / "live.db"
    with sqlite3.connect(src) as c:
        c.execute("create table t (x int)")
        c.execute("insert into t values (42)")
    monkeypatch.setattr(backup, "_db_path", lambda: src)
    made = []
    for i in range(4):
        monkeypatch.setattr(backup, "datetime", _Clock(i))
        made.append(backup.backup_now(tmp_path / "b", keep=2))
    kept = sorted(p.name for p in (tmp_path / "b").glob("*.db"))
    assert kept == sorted(p.name for p in made[-2:])
    with sqlite3.connect(made[-1]) as c:
        assert c.execute("select x from t").fetchone() == (42,)
    assert oct(made[-1].stat().st_mode)[-3:] == "600"


class _Clock:
    """Distinct timestamps for successive backups within one test."""
    def __init__(self, i):
        self.i = i

    def now(self, tz=None):
        from datetime import datetime
        return datetime(2026, 9, 25, 12, 0, self.i, tzinfo=tz)

    def fromtimestamp(self, *a, **k):
        from datetime import datetime
        return datetime.fromtimestamp(*a, **k)
