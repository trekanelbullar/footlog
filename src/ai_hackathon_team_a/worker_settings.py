"""worker 専用の設定（DB・Supabase・Resend・シークレット・コスト上限）。

既存の ``ORCAROUTER_`` 接頭辞の :class:`~ai_hackathon_team_a.config.Settings` とは別に、
接頭辞なしで同じ ``.env`` から読み込む（設計書 §6.2）。
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# repo 直下の prompts/ ディレクトリ（このファイルは src/ai_hackathon_team_a/ 配下にある）。
_DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

JwtAlg = Literal["ES256", "RS256", "HS256"]

_MIN_SECRET_LENGTH = 32


class WorkerSettings(BaseSettings):
    """DB・Supabase・Resend・シークレット・コスト上限など worker 固有の設定。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )

    database_url: str = Field(min_length=1)
    supabase_url: AnyHttpUrl
    supabase_service_role_key: SecretStr
    supabase_jwt_alg: JwtAlg
    supabase_jwt_secret: SecretStr | None = None
    resend_api_key: SecretStr
    mail_from: str = Field(min_length=1)
    app_base_url: AnyHttpUrl
    worker_shared_secret: SecretStr
    cron_secret: SecretStr
    daily_cost_limit_usd: float = Field(gt=0)
    system_alert_email: str = Field(min_length=1)
    prompts_dir: Path = _DEFAULT_PROMPTS_DIR

    @model_validator(mode="after")
    def _validate_startup_invariants(self) -> "WorkerSettings":
        """起動時検査（設計書 §1.2 A-X4）。

        ``WORKER_SHARED_SECRET`` と ``CRON_SECRET`` は必須・32文字以上・互いに異なる
        値であることを確かめる。満たさなければ worker を起動させない。
        """

        shared = self.worker_shared_secret.get_secret_value()
        cron = self.cron_secret.get_secret_value()
        if len(shared) < _MIN_SECRET_LENGTH:
            raise ValueError(
                f"WORKER_SHARED_SECRET must be at least {_MIN_SECRET_LENGTH} characters long"
            )
        if len(cron) < _MIN_SECRET_LENGTH:
            raise ValueError(f"CRON_SECRET must be at least {_MIN_SECRET_LENGTH} characters long")
        if shared == cron:
            raise ValueError("WORKER_SHARED_SECRET and CRON_SECRET must not be the same value")

        if self.supabase_jwt_alg == "HS256" and self.supabase_jwt_secret is None:
            raise ValueError("SUPABASE_JWT_SECRET is required when SUPABASE_JWT_ALG=HS256")

        return self


@lru_cache
def get_worker_settings() -> WorkerSettings:
    """WorkerSettings を読み込み、検証結果をキャッシュする。"""

    return WorkerSettings()  # type: ignore[call-arg]
