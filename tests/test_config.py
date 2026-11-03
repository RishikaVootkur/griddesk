import pytest
from pydantic import ValidationError

from griddesk.config import Settings


def test_secrets_are_masked_in_repr(clean_env):
    clean_env.setenv("GEMINI_API_KEY", "super-secret-value")
    clean_env.setenv("DATABASE_URL", "postgresql+psycopg://u:hunter2@db/x")
    s = Settings(_env_file=None)
    assert "super-secret-value" not in repr(s)
    assert "hunter2" not in repr(s)
    assert s.gemini_api_key is not None
    assert s.gemini_api_key.get_secret_value() == "super-secret-value"


def test_defaults_work_without_keys(clean_env):
    s = Settings(_env_file=None)
    assert s.gemini_api_key is None
    assert s.cache_mode == "read_write"
    assert s.simulated_now.year == 2026


def test_lowercase_log_level_is_accepted(clean_env):
    clean_env.setenv("LOG_LEVEL", "info")
    assert Settings(_env_file=None).log_level == "INFO"


def test_invalid_cache_mode_is_rejected(clean_env):
    clean_env.setenv("CACHE_MODE", "sometimes")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
