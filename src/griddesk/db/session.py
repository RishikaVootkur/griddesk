"""Engine and session helpers.

`SessionFactory` hides where sessions come from. Live servers get a factory over the shared
engine, so each tool call commits. An eval trial gets a factory bound to one connection inside an
outer transaction (`trial_scope`): tool commits only release savepoints, and the whole trial rolls
back at the end, so every trial starts from the same seeded state without re-seeding.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from griddesk.config import get_settings

SessionFactory = Callable[[], Session]


@lru_cache(maxsize=4)
def get_engine(url: str | None = None) -> Engine:
    dsn = url or get_settings().database_url.get_secret_value()
    return create_engine(dsn, pool_pre_ping=True, pool_size=10, max_overflow=20)


def live_session_factory(engine: Engine | None = None) -> SessionFactory:
    return sessionmaker(bind=engine or get_engine(), expire_on_commit=False)


@contextmanager
def trial_scope(engine: Engine | None = None) -> Iterator[SessionFactory]:
    """Yield a session factory whose writes are rolled back when the block exits."""
    conn = (engine or get_engine()).connect()
    outer = conn.begin()
    try:
        yield sessionmaker(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    finally:
        outer.rollback()
        conn.close()
