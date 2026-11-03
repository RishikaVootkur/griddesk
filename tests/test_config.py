from griddesk.config import Settings


def test_secrets_are_masked_in_repr(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-value")
    s = Settings(_env_file=None)
    assert s.gemini_api_key is not None
    assert "super-secret-value" not in repr(s)
    assert s.gemini_api_key.get_secret_value() == "super-secret-value"


def test_defaults_work_without_keys(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    s = Settings(_env_file=None)
    assert s.gemini_api_key is None
    assert s.cache_mode == "read_write"
    assert s.simulated_now.year == 2026
