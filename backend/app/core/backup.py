"""Daily database backups, kept for two weeks.

The paper-trading record — every order, fill, snapshot and journal line — is
the one thing this app cannot rebuild: market data can be re-downloaded,
models re-created, but a model's live history exists only here. SQLite's online
backup API copies a consistent snapshot even while the app is writing.

Backups hold secrets only in encrypted form (see secrets_box); the key is not
in them, by design.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.core.secrets_box import restrict

BACKUP_DIR = Path(__file__).resolve().parents[2] / "backups"
KEEP = 14


def _db_path() -> Path | None:
    url = get_settings().database_url
    if not url.startswith("sqlite:///") or ":memory:" in url:
        return None
    raw = url.removeprefix("sqlite:///")
    path = Path(raw)
    return path if path.is_absolute() else (Path(__file__).resolve().parents[2] / raw).resolve()


def backup_now(dest: Path = BACKUP_DIR, keep: int = KEEP) -> Path | None:
    src = _db_path()
    if src is None or not src.exists():
        return None
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / f"stockstrat-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.db"
    with sqlite3.connect(src) as source, sqlite3.connect(target) as copy:
        source.backup(copy)
    restrict(target)
    for old in sorted(dest.glob("stockstrat-*.db"))[:-keep]:
        old.unlink()
    return target


def list_backups(dest: Path = BACKUP_DIR) -> list[dict]:
    return [{"name": p.name, "size_kb": round(p.stat().st_size / 1024, 1),
             "at": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()}
            for p in sorted(dest.glob("stockstrat-*.db"), reverse=True)]
