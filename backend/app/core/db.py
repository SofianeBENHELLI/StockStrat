from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()
engine = create_engine(
    _settings.database_url,
    connect_args={"check_same_thread": False} if _settings.database_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


if engine.dialect.name == "sqlite":
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record) -> None:
        """Two processes share this file — the API and the decision worker.
        WAL lets readers proceed while one writes, and busy_timeout makes a
        writer wait for the lock instead of failing with "database is locked"."""
        cur = dbapi_conn.cursor()
        if ":memory:" not in _settings.database_url:
            cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.close()


def _ensure_column(table: str, column: str, ddl_type: str) -> None:
    """No Alembic in this project — `create_all()` only creates missing
    tables, never adds columns to ones that already exist. This is a tiny
    hand-rolled add-if-missing for SQLite dev DBs so schema changes don't
    require deleting existing paper-trading test data."""
    if not engine.dialect.name == "sqlite":
        return
    with engine.connect() as conn:
        existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
        # A table that no longer exists (legacy tables of the old tournament, on
        # a fresh install) has nothing to migrate. Without this check a brand
        # new database could not even start.
        if existing and column not in existing:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
            conn.commit()


def init_db() -> None:
    from app import models  # noqa: F401  (register tables on Base.metadata)

    Base.metadata.create_all(bind=engine)
    _ensure_column("decision_log", "explanation_source", "TEXT DEFAULT 'template'")
    _ensure_column("variants", "scaled", "BOOLEAN DEFAULT 0")
    _ensure_column("paper_portfolios", "broker", "TEXT DEFAULT 'sim'")
    _ensure_column("paper_orders", "broker", "TEXT DEFAULT 'sim'")
    _ensure_column("paper_orders", "broker_order_id", "TEXT")
    _ensure_column("paper_orders", "exit_reason", "TEXT")
    # The lab. Existing rows predate it (the old tournament), so the new stage
    # column defaults them to 'archived': kept on disk, out of every screen.
    _ensure_column("variants", "params", "JSON DEFAULT '{}'")
    _ensure_column("variants", "budget", "FLOAT DEFAULT 10000")
    _ensure_column("variants", "stage", "TEXT DEFAULT 'archived'")
    _ensure_column("variants", "promoted_at", "DATETIME")
    _ensure_column("variants", "last_decision_on", "TEXT")
    _ensure_column("variants", "last_rebalance_on", "TEXT")
    _ensure_column("paper_orders", "stop_price", "FLOAT")
    _ensure_column("paper_orders", "time_in_force", "TEXT DEFAULT 'day'")
    _ensure_column("paper_orders", "purpose", "TEXT DEFAULT 'trade'")
    _secure_storage()


def _secure_storage() -> None:
    """Encrypt any secret still stored in clear, and make the database file
    readable by its owner only."""
    from pathlib import Path

    from app.core import secrets_box, settings_store

    db = SessionLocal()
    try:
        settings_store.encrypt_plaintext_secrets(db)
    finally:
        db.close()
    url = _settings.database_url
    if url.startswith("sqlite:///") and ":memory:" not in url:
        path = Path(url.removeprefix("sqlite:///"))
        secrets_box.restrict(path if path.is_absolute() else Path.cwd() / path)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
