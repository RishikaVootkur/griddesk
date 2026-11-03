"""Typed application settings, loaded from the environment and the repo's `.env`.

Secrets (API keys and the database URL, which may carry a password) are `SecretStr`, so they
never appear in reprs, logs or tracebacks.
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Editable install (the only supported mode): src/griddesk/config.py -> repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
CacheMode = Literal["off", "read_write", "replay_only"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    # Infrastructure
    database_url: SecretStr = SecretStr("postgresql+psycopg://griddesk:griddesk@localhost:5433/griddesk")
    phoenix_collector_endpoint: str = "http://localhost:6006/v1/traces"
    tracing_enabled: bool = False

    # LLM providers. Keys are optional so offline replay (CI) works without them.
    gemini_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    ollama_base_url: str = "http://localhost:11434"

    # Gateway
    cache_dir: Path = REPO_ROOT / ".griddesk" / "cache"
    cache_mode: CacheMode = "read_write"
    conversation_token_budget: int = 400_000
    max_retries: int = 6

    # The simulated utility's clock. Fixed so seeds, tasks and cached LLM requests stay reproducible.
    simulated_now: datetime = datetime(2026, 10, 15, 15, 0, tzinfo=UTC)

    log_level: LogLevel = "INFO"
    log_json: bool = False

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_level(cls, v: object) -> object:
        return v.upper() if isinstance(v, str) else v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
