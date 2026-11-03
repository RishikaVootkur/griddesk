import pytest

from griddesk.config import Settings


@pytest.fixture
def clean_env(monkeypatch):
    """Remove every Settings field from the environment so tests see true defaults."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    return monkeypatch


@pytest.fixture(scope="session")
def engine():
    """A migrated, seeded Postgres. Skips the test when no database is reachable."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from griddesk.config import REPO_ROOT
    from griddesk.db.seed import generate, load
    from griddesk.db.session import get_engine

    eng = get_engine()
    try:
        with eng.connect() as c:
            c.execute(text("select 1"))
    except Exception:
        pytest.skip("Postgres not reachable; start it with `docker compose up -d postgres`")
    command.upgrade(Config(str(REPO_ROOT / "alembic.ini")), "head")
    load(eng, generate())
    return eng


@pytest.fixture
def sessions(engine):
    """Session factory whose writes roll back after the test."""
    from griddesk.db.session import trial_scope

    with trial_scope(engine) as factory:
        yield factory
