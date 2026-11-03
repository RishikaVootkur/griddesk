import pytest

from griddesk.config import Settings


@pytest.fixture
def clean_env(monkeypatch):
    """Remove every Settings field from the environment so tests see true defaults."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    return monkeypatch
