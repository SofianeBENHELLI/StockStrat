from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

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


BASELINE = "0001"


def _alembic_config():
    from pathlib import Path

    from alembic.config import Config

    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    return cfg


def _legacy_columns() -> None:
    """Databases created before Alembic grew by hand-added columns. Bring such
    a database up to the baseline shape before stamping it; on a database that
    already has them, or lacks the table, each step is a no-op."""
    _ensure_column("decision_log", "explanation_source", "TEXT DEFAULT 'template'")
    _ensure_column("variants", "scaled", "BOOLEAN DEFAULT 0")
    _ensure_column("paper_portfolios", "broker", "TEXT DEFAULT 'sim'")
    _ensure_column("paper_orders", "broker", "TEXT DEFAULT 'sim'")
    _ensure_column("paper_orders", "broker_order_id", "TEXT")
    _ensure_column("paper_orders", "exit_reason", "TEXT")
    # The lab. Rows from the old tournament default to 'archived': kept on
    # disk, out of every screen.
    _ensure_column("variants", "params", "JSON DEFAULT '{}'")
    _ensure_column("variants", "budget", "FLOAT DEFAULT 10000")
    _ensure_column("variants", "stage", "TEXT DEFAULT 'archived'")
    _ensure_column("variants", "promoted_at", "DATETIME")
    _ensure_column("variants", "last_decision_on", "TEXT")
    _ensure_column("variants", "last_rebalance_on", "TEXT")
    _ensure_column("paper_orders", "stop_price", "FLOAT")
    _ensure_column("paper_orders", "time_in_force", "TEXT DEFAULT 'day'")
    _ensure_column("paper_orders", "purpose", "TEXT DEFAULT 'trade'")


def init_db() -> None:
    """Bring the database to the current schema, whatever state it is in.

    - managed by Alembic already: upgrade to head;
    - created before Alembic (tables but no version): complete the hand-added
      columns, stamp it at the baseline — nothing is recreated — then upgrade;
    - empty: let the migrations create everything.
    Future schema changes are Alembic revisions (`alembic revision
    --autogenerate`), never edits here."""
    from alembic import command
    from sqlalchemy import inspect

    from app import models  # noqa: F401  (register tables on Base.metadata)

    cfg = _alembic_config()
    # The worker and the API start together and both call this. Without a lock
    # both saw "no version yet" and both stamped it; the second crashed on the
    # unique version row and the API never came up. A file lock serialises
    # them: the second waits, then finds the database already current.
    with _migration_lock():
        tables = set(inspect(engine).get_table_names())
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            if "alembic_version" not in tables and "variants" in tables:
                _legacy_columns()
                command.stamp(cfg, BASELINE)
            command.upgrade(cfg, "head")
        _secure_storage()


@contextmanager
def _migration_lock():
    import fcntl

    from app.core.paths import DATA_DIR

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DATA_DIR / ".migrate.lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


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
