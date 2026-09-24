from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("LOKY_MAX_CPU_COUNT", "4")
# Tests encrypt with a throwaway key rather than creating backend/.secret_key.
from cryptography.fernet import Fernet  # noqa: E402
os.environ["STOCKSTRAT_SECRET_KEY"] = Fernet.generate_key().decode()

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.db import Base


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
