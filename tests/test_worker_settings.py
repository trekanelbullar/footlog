import pytest
from pydantic import ValidationError

from ai_hackathon_team_a.worker_settings import WorkerSettings

_WORKER_SECRET = "w" * 32
_CRON_SECRET = "c" * 32


def _base_kwargs(**overrides: object) -> dict:
    kwargs: dict = {
        "_env_file": None,
        "database_url": "postgresql://localhost/decision_trace",
        "supabase_url": "https://example.supabase.co",
        "supabase_service_role_key": "service-role-key",
        "supabase_jwt_alg": "ES256",
        "resend_api_key": "resend-key",
        "mail_from": "noreply@example.test",
        "app_base_url": "https://app.example.test",
        "worker_shared_secret": _WORKER_SECRET,
        "cron_secret": _CRON_SECRET,
        "daily_cost_limit_usd": 5.0,
        "system_alert_email": "alert@example.test",
    }
    kwargs.update(overrides)
    return kwargs


def test_valid_settings_construct() -> None:
    settings = WorkerSettings(**_base_kwargs())  # type: ignore[arg-type]

    assert settings.worker_shared_secret.get_secret_value() == _WORKER_SECRET
    assert settings.supabase_jwt_secret is None


def test_worker_shared_secret_is_required() -> None:
    kwargs = _base_kwargs()
    del kwargs["worker_shared_secret"]
    with pytest.raises(ValidationError):
        WorkerSettings(**kwargs)  # type: ignore[arg-type]


def test_cron_secret_is_required() -> None:
    kwargs = _base_kwargs()
    del kwargs["cron_secret"]
    with pytest.raises(ValidationError):
        WorkerSettings(**kwargs)  # type: ignore[arg-type]


def test_worker_shared_secret_too_short_is_rejected() -> None:
    with pytest.raises(ValidationError, match="at least 32 characters"):
        WorkerSettings(**_base_kwargs(worker_shared_secret="short"))  # type: ignore[arg-type]


def test_cron_secret_too_short_is_rejected() -> None:
    with pytest.raises(ValidationError, match="at least 32 characters"):
        WorkerSettings(**_base_kwargs(cron_secret="short"))  # type: ignore[arg-type]


def test_equal_secrets_are_rejected() -> None:
    with pytest.raises(ValidationError, match="must not be the same value"):
        WorkerSettings(  # type: ignore[arg-type]
            **_base_kwargs(worker_shared_secret=_WORKER_SECRET, cron_secret=_WORKER_SECRET)
        )


def test_hs256_requires_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="SUPABASE_JWT_SECRET is required"):
        WorkerSettings(**_base_kwargs(supabase_jwt_alg="HS256"))  # type: ignore[arg-type]


def test_hs256_with_secret_is_valid() -> None:
    settings = WorkerSettings(
        **_base_kwargs(supabase_jwt_alg="HS256", supabase_jwt_secret="a-shared-secret")
    )  # type: ignore[arg-type]

    assert settings.supabase_jwt_alg == "HS256"
    assert settings.supabase_jwt_secret is not None
    assert settings.supabase_jwt_secret.get_secret_value() == "a-shared-secret"


def test_es256_does_not_require_jwt_secret() -> None:
    settings = WorkerSettings(**_base_kwargs(supabase_jwt_alg="ES256"))  # type: ignore[arg-type]

    assert settings.supabase_jwt_secret is None


def test_secrets_are_redacted() -> None:
    settings = WorkerSettings(**_base_kwargs())  # type: ignore[arg-type]

    assert _WORKER_SECRET not in repr(settings)
    assert _CRON_SECRET not in repr(settings)
