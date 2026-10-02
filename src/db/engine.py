"""Engine and session helpers for the PostgreSQL layer of truth."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import Session, sessionmaker

from src.config import settings


def database_url() -> URL:
    """Connection URL built from GHAI_PG_* settings; the password is required."""
    return URL.create(
        "postgresql+psycopg",
        username=settings.GHAI_PG_USER,
        password=settings.require_env("GHAI_PG_PASSWORD"),
        host=settings.GHAI_PG_HOST,
        port=settings.GHAI_PG_PORT,
        database=settings.GHAI_PG_DB,
    )


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(database_url(), pool_pre_ping=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """One transaction: commit on success, roll back on any exception."""
    session = sessionmaker(bind=get_engine())()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
