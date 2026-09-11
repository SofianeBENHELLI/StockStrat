from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, text
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


def _ensure_column(table: str, column: str, ddl_type: str) -> None:
    """No Alembic in this project — `create_all()` only creates missing
    tables, never adds columns to ones that already exist. This is a tiny
    hand-rolled add-if-missing for SQLite dev DBs so schema changes don't
    require deleting existing paper-trading test data."""
    if not engine.dialect.name == "sqlite":
        return
    with engine.connect() as conn:
        existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
        if column not in existing:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
            conn.commit()


def init_db() -> None:
    from app import models  # noqa: F401  (register tables on Base.metadata)

    Base.metadata.create_all(bind=engine)
    _ensure_column("decision_log", "explanation_source", "TEXT DEFAULT 'template'")
    _ensure_column("variants", "scaled", "BOOLEAN DEFAULT 0")


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
