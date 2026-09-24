"""Schema migrations: a fresh database and a pre-Alembic one both end up at
the schema the models describe, and nothing is recreated or dropped."""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

from cryptography.fernet import Fernet

BACKEND = Path(__file__).resolve().parents[1]


def _run(db_path: Path, code: str) -> subprocess.CompletedProcess:
    env = {"DATABASE_URL": f"sqlite:///{db_path}", "STOCKSTRAT_DATA_DIR": str(db_path.parent),
           "STOCKSTRAT_SECRET_KEY": Fernet.generate_key().decode(), "PATH": "/usr/bin:/bin"}
    return subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env,
                          capture_output=True, text=True, timeout=120)


CHECK_NO_DRIFT = """
from app.core.db import init_db, engine, Base
from app import models
init_db()
from alembic.migration import MigrationContext
from alembic.autogenerate import compare_metadata
with engine.connect() as c:
    ctx = MigrationContext.configure(c)
    diff = [d for d in compare_metadata(ctx, Base.metadata)
            if not (d[0] == 'remove_table')]   # legacy tables are kept on purpose
    print('DIFF', diff)
    print('VERSION', ctx.get_current_revision())
"""


def test_a_fresh_database_is_created_by_the_migrations_and_matches_the_models(tmp_path):
    out = _run(tmp_path / "fresh.db", CHECK_NO_DRIFT)
    assert out.returncode == 0, out.stderr[-2000:]
    assert "DIFF []" in out.stdout
    assert "VERSION 0001" in out.stdout


def test_a_database_from_before_alembic_is_stamped_not_recreated(tmp_path):
    db = tmp_path / "legacy.db"
    # A database as the old app left it: tables, a legacy one, data, no version.
    seed = f"""
from app.core.db import Base, engine
from app import models
Base.metadata.create_all(engine)
import sqlite3
c = sqlite3.connect({str(db)!r})
c.execute("create table decision_log (id integer primary key, note text)")
c.execute("insert into decision_log (note) values ('historique')")
c.execute("insert into variants (name, engine, stage, variant_key, description, status, generation, scaled, "
          "params, budget, created_at) values ('ancien', 'casino', 'archived', '', '', 'active', 0, 0, '{{}}', "
          "10000, '2026-09-01 00:00:00')")
c.commit()
"""
    assert _run(db, seed).returncode == 0
    out = _run(db, CHECK_NO_DRIFT)
    assert out.returncode == 0, out.stderr[-2000:]
    assert "DIFF []" in out.stdout and "VERSION 0001" in out.stdout
    with sqlite3.connect(db) as c:
        assert c.execute("select count(*) from variants").fetchone() == (1,)          # data kept
        assert c.execute("select note from decision_log").fetchone() == ("historique",)  # legacy table kept


def test_running_init_twice_is_harmless(tmp_path):
    db = tmp_path / "twice.db"
    assert _run(db, "from app.core.db import init_db; init_db()").returncode == 0
    out = _run(db, CHECK_NO_DRIFT)
    assert out.returncode == 0 and "DIFF []" in out.stdout


def test_two_processes_starting_together_both_succeed(tmp_path):
    """The worker and the API start at the same moment and both migrate."""
    import threading

    db = tmp_path / "race.db"
    seed = f"""
from app.core.db import Base, engine
from app import models
Base.metadata.create_all(engine)
"""
    assert _run(db, seed).returncode == 0          # a pre-Alembic database: the stamping path
    results = []
    threads = [threading.Thread(target=lambda: results.append(_run(db, "from app.core.db import init_db; init_db()")))
               for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [r.returncode for r in results] == [0, 0, 0], [r.stderr[-500:] for r in results]
