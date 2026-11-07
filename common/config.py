"""Runtime settings, read from environment variables (prefix ``CATEGORY_``) or an optional ``.env`` file.

Secrets are never given defaults. Anything that looks like a credential must come from the environment
or a Docker secret file.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CATEGORY_", env_file=".env", env_file_encoding="utf-8", extra="ignore", secrets_dir=None
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    # storage
    database_url: str = "postgresql+psycopg://category:category@localhost:5432/category"
    redis_url: str = "redis://localhost:6379/0"
    s3_endpoint_url: str | None = None
    s3_bucket: str = "category-artifacts"
    s3_access_key: SecretStr | None = None
    s3_secret_key: SecretStr | None = None

    # AI provider: "mock" until a real provider is configured and verified
    ai_provider: Literal["mock", "evren"] = "mock"
    evren_base_url: str = "https://evren-llmapi.ssyz.org.tr"
    evren_api_key: SecretStr | None = Field(default=None, description="Bearer token for the EVREN LLM gateway")
    evren_model: str = "qwen3.8-flash-next"
    evren_timeout_seconds: float = 120.0
    evren_json_mode: bool = Field(
        default=False,
        description="Send response_format={'type':'json_object'}. Off until the gateway is verified to support it.",
    )

    # classification
    taxonomy_path: str | None = None  # defaults to the bundled classification/taxonomy.yaml
    max_text_chars: int = 12_000
    max_html_chars: int = 6_000

    # public API & rate limits (requests per minute)
    public_rate_limit_per_minute: int = 60
    anonymous_write_limit_per_minute: int = 10
    api_key_rate_limit_per_minute: int = 600
    rate_limit_fail_open: bool = Field(
        default=True, description="If Redis is unreachable, allow requests (logged) instead of rejecting them."
    )
    cors_origins: list[str] = []

    # imports
    max_import_rows: int = 10_000
    max_upload_bytes: int = 5 * 1024 * 1024

    # keyed hash for client fingerprints (IP addresses are never stored in clear). Unset = no fingerprints.
    fingerprint_secret: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
