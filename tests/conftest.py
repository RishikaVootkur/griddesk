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
    from sqlalchemy import create_engine, make_url, text

    from griddesk.config import REPO_ROOT, get_settings
    from griddesk.db.seed import generate, load
    from griddesk.db.session import get_engine

    # Tests use their own database next to the dev one, so running pytest never wipes dev data.
    dev_url = make_url(get_settings().database_url.get_secret_value())
    test_url = dev_url.set(database=f"{dev_url.database}_test")
    try:
        admin = create_engine(dev_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            exists = c.execute(text("select 1 from pg_database where datname = :n"), {"n": test_url.database})
            if not exists.scalar():
                c.execute(text(f'create database "{test_url.database}"'))
        admin.dispose()
    except Exception:
        pytest.skip("Postgres not reachable; start it with `docker compose up -d postgres`")
    url = test_url.render_as_string(hide_password=False)
    # Rebuild the test schema from scratch so tests never depend on what another branch left behind.
    assert test_url.database and test_url.database.endswith("_test")
    reset = create_engine(url, isolation_level="AUTOCOMMIT")
    with reset.connect() as c:
        c.execute(text("drop schema public cascade"))
        c.execute(text("create schema public"))
    reset.dispose()
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.attributes["url"] = url
    command.upgrade(cfg, "head")
    eng = get_engine(url)
    load(eng, generate())
    return eng


@pytest.fixture
def sessions(engine):
    """Session factory whose writes roll back after the test."""
    from griddesk.db.session import trial_scope

    with trial_scope(engine) as factory:
        yield factory
