"""Validated application settings loaded from environment variables."""

import logging
from functools import lru_cache
from typing import Literal, NamedTuple

from pydantic import AnyHttpUrl, Field, PrivateAttr, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# 段階ごとの設定（設計書 §6.2）。
Stage = Literal["EXTRACT", "SUPPORT", "AGENT", "ASSEMBLE", "JUDGE"]
_STAGES: tuple[Stage, ...] = ("EXTRACT", "SUPPORT", "AGENT", "ASSEMBLE", "JUDGE")
ReasoningEffort = Literal["off", "low", "medium", "high"]

# 出力上限の既定値：抽出・組み立ては 4000、それ以外は Settings.max_tokens。
_STAGE_DEFAULT_MAX_TOKENS: dict[Stage, int] = {"EXTRACT": 4000, "ASSEMBLE": 4000}


class StageConfig(NamedTuple):
    """段階名から引いた (model, reasoning, max_tokens) の組。"""

    model: str
    reasoning: ReasoningEffort
    max_tokens: int


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

    # 段階ごとのモデル・思考モード・出力上限（設計書 §6.2、spec U12 の解決）。
    # 未設定ならモデルは model を、出力上限は _STAGE_DEFAULT_MAX_TOKENS または max_tokens を使う。
    model_extract: str | None = Field(default=None, max_length=200)
    model_support: str | None = Field(default=None, max_length=200)
    model_agent: str | None = Field(default=None, max_length=200)
    model_assemble: str | None = Field(default=None, max_length=200)
    model_judge: str | None = Field(default=None, max_length=200)

    reasoning_extract: ReasoningEffort = "off"
    reasoning_support: ReasoningEffort = "off"
    reasoning_agent: ReasoningEffort = "off"
    reasoning_assemble: ReasoningEffort = "off"
    reasoning_judge: ReasoningEffort = "off"

    # 段階ごとの上限は、思考モードによる引き上げ（AD-2）が既存の max_tokens（le=8192）を
    # 超えうるため、別の上限（le=64000）を持つ。
    max_tokens_extract: int | None = Field(default=None, ge=1, le=64_000)
    max_tokens_support: int | None = Field(default=None, ge=1, le=64_000)
    max_tokens_agent: int | None = Field(default=None, ge=1, le=64_000)
    max_tokens_assemble: int | None = Field(default=None, ge=1, le=64_000)
    max_tokens_judge: int | None = Field(default=None, ge=1, le=64_000)

    # 思考モードが off 以外の段階で、出力上限がこの値未満なら引き上げる（AD-2）。
    reasoning_min_max_tokens: int = Field(default=16_000, ge=1, le=64_000)

    _effective_max_tokens: dict[Stage, int] = PrivateAttr(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def require_https(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.scheme != "https":
            raise ValueError("ORCAROUTER_BASE_URL must use HTTPS")
        return value

    @model_validator(mode="after")
    def _apply_reasoning_min_max_tokens(self) -> "Settings":
        """思考モードが off 以外の段階の出力上限を、起動時に検査・引き上げる（AD-2）。

        引き上げた場合は ``logging.warning`` で段階名・設定値・実際に使う値を出す。
        """

        for stage in _STAGES:
            key = stage.lower()
            reasoning: ReasoningEffort = getattr(self, f"reasoning_{key}")
            configured_max_tokens: int | None = getattr(self, f"max_tokens_{key}")
            configured = configured_max_tokens or _STAGE_DEFAULT_MAX_TOKENS.get(
                stage, self.max_tokens
            )

            effective = configured
            if reasoning != "off" and configured < self.reasoning_min_max_tokens:
                effective = self.reasoning_min_max_tokens
                logger.warning(
                    "stage=%s reasoning=%s: max_tokens %d is below "
                    "ORCAROUTER_REASONING_MIN_MAX_TOKENS=%d; raising it to %d",
                    stage,
                    reasoning,
                    configured,
                    self.reasoning_min_max_tokens,
                    effective,
                )
            self._effective_max_tokens[stage] = effective

        return self

    def stage_config(self, stage: Stage) -> StageConfig:
        """段階名から (model, reasoning, max_tokens) を引く。

        モデル未設定なら ``model``、出力上限未設定なら段階ごとの既定値
        （EXTRACT・ASSEMBLE は4000、それ以外は ``max_tokens``）を使う。思考モードが
        off 以外で出力上限が ``reasoning_min_max_tokens`` 未満なら、引き上げた後の値
        （起動時検査で計算済み）を返す。
        """

        key = stage.lower()
        model: str | None = getattr(self, f"model_{key}")
        reasoning: ReasoningEffort = getattr(self, f"reasoning_{key}")

        return StageConfig(
            model=model or self.model,
            reasoning=reasoning,
            max_tokens=self._effective_max_tokens[stage],
        )


@lru_cache
def get_settings() -> Settings:
    """Load and cache validated settings without exposing secret values."""

    return Settings()  # type: ignore[call-arg]
