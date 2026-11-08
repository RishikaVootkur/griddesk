"""Engine and session helpers.

`SessionFactory` hides where sessions come from. Live servers get a factory over the shared
engine, so each tool call commits. An eval trial gets a factory bound to one connection inside an
outer transaction (`trial_scope`): tool commits only release savepoints, and the whole trial rolls
back at the end, so every trial starts from the same seeded state without re-seeding.

Trials running at once can block each other: ids are deterministic, so two trials that file an outage
report insert the same primary key and the second waits for the first to roll back. Tool bodies are
synchronous, so a wait blocks the whole event loop, and when both trials share a process the first can
never finish. `trial_scope` therefore sets a lock timeout; the runner turns a timeout into
`TrialConflict` and retries the trial later instead of scoring it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from griddesk.config import get_settings

SessionFactory = Callable[[], Session]


@lru_cache(maxsize=4)
def get_engine(url: str | None = None) -> Engine:
    dsn = url or get_settings().database_url.get_secret_value()
    return create_engine(dsn, pool_pre_ping=True, pool_size=10, max_overflow=20)


def live_session_factory(engine: Engine | None = None) -> SessionFactory:
    return sessionmaker(bind=engine or get_engine(), expire_on_commit=False)


class TrialConflict(Exception):
    """An eval trial waited too long on a row another trial holds. Retry it; do not score it."""


# What an MCP server sends instead of a lock-timeout crash. The client matches this exact text, so
# nothing a model types into a tool argument can be mistaken for it.
LOCK_TIMEOUT_MARKER = "GRIDDESK_LOCK_TIMEOUT"
_LOCK_ERRORS = ("lock timeout", "LockNotAvailable", "deadlock detected", "DeadlockDetected")


def is_lock_timeout(error: BaseException) -> bool:
    """A database error that means another transaction holds the rows: a lock timeout, or a deadlock
    Postgres detected between two trials. Only for exceptions, never for text a model could write."""
    text = f"{type(error).__name__} {error} {type(getattr(error, 'orig', None)).__name__}"
    return any(k in text for k in _LOCK_ERRORS)


def raise_if_lock_timeout(error: BaseException) -> None:
    if isinstance(error, OperationalError) and is_lock_timeout(error):
        raise TrialConflict(str(error)[:200]) from error


@contextmanager
def trial_scope(engine: Engine | None = None, lock_timeout_ms: int = 20_000) -> Iterator[SessionFactory]:
    """Yield a session factory whose writes are rolled back when the block exits."""
    conn = (engine or get_engine()).connect()
    outer = conn.begin()
    if lock_timeout_ms > 0:
        conn.exec_driver_sql(f"SET LOCAL lock_timeout = {int(lock_timeout_ms)}")
    try:
        yield sessionmaker(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    finally:
        outer.rollback()
        conn.close()
