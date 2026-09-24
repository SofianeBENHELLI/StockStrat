"""A brand-new database must start. The local one had grown with the code and
masked that it did not — found by building the Docker image from scratch."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from cryptography.fernet import Fernet

BACKEND = Path(__file__).resolve().parents[1]


def test_init_db_on_an_empty_file(tmp_path):
    env = {
        "DATABASE_URL": f"sqlite:///{tmp_path / 'fresh.db'}",
        "STOCKSTRAT_DATA_DIR": str(tmp_path),
        "STOCKSTRAT_SECRET_KEY": Fernet.generate_key().decode(),
        "PATH": "/usr/bin:/bin",
    }
    code = ("from app.core.db import init_db, SessionLocal; init_db(); "
            "from app.lab import service; db = SessionLocal(); print(len(service.seed_defaults(db)))")
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "9"
    assert oct((tmp_path / "fresh.db").stat().st_mode)[-3:] == "600"
