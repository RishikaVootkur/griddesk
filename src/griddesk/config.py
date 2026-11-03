"""Typed application settings, loaded from the environment and `.env`.

Secrets are `SecretStr`, so they never appear in reprs, logs or tracebacks.
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )

    # Infrastructure
    database_url: str = "postgresql+psycopg://griddesk:griddesk@localhost:5433/griddesk"
    phoenix_collector_endpoint: str = "http://localhost:6006/v1/traces"
    tracing_enabled: bool = False

    # LLM providers. Keys are optional so offline replay (CI) works without them.
    gemini_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    ollama_base_url: str = "http://localhost:11434"

    # Gateway
    cache_dir: Path = REPO_ROOT / ".griddesk" / "cache"
    cache_mode: str = Field(default="read_write", pattern="^(off|read_write|replay_only)$")
    conversation_token_budget: int = 400_000
    max_retries: int = 6

    # The simulated utility's clock. Fixed so seeds, tasks and cached LLM requests stay reproducible.
    simulated_now: datetime = datetime(2026, 10, 15, 15, 0, tzinfo=UTC)

    log_level: str = "INFO"
    log_json: bool = False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
