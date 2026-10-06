from __future__ import annotations

import os
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def normalize_database_url(url: str) -> str:
    """Use the installed psycopg v3 driver for provider PostgreSQL URLs."""
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


@lru_cache(maxsize=8)
def _session_factory(database_url: str):
    engine = create_engine(database_url, pool_pre_ping=True)
    return sessionmaker(bind=engine, expire_on_commit=False)


def build_session_factory(url: str | None = None):
    database_url = url or os.getenv(
        "DATABASE_URL", "postgresql+psycopg://riskqueue:riskqueue@localhost:5432/riskqueue"
    )
    return _session_factory(normalize_database_url(database_url))
