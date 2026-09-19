"""Validated application settings loaded from environment variables."""

from functools import lru_cache

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Orca Router settings.

    Values are read from environment variables prefixed with ``ORCAROUTER_`` and
    optionally from a local ``.env`` file. Secret values are represented by
    ``SecretStr`` so they are redacted from repr and validation output.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="ORCAROUTER_",
        extra="ignore",
    )

    api_key: SecretStr = Field(min_length=1)
    base_url: AnyHttpUrl = "https://api.orcarouter.ai/v1"
    model: str = Field(default="qwen/qwen3.7-flash", min_length=1, max_length=200)
    timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    max_retries: int = Field(default=2, ge=0, le=5)
    max_tokens: int = Field(default=500, ge=1, le=8192)
    max_prompt_chars: int = Field(default=20_000, ge=1, le=200_000)

    @field_validator("base_url")
    @classmethod
    def require_https(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.scheme != "https":
            raise ValueError("ORCAROUTER_BASE_URL must use HTTPS")
        return value


@lru_cache
def get_settings() -> Settings:
    """Load and cache validated settings without exposing secret values."""

    return Settings()  # type: ignore[call-arg]
