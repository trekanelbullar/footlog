"""Supabase Auth 管理API（W6、S10）のテスト。httpx.MockTransport で差し替える。"""

import httpx
import pytest

from ai_hackathon_team_a.auth_admin import AuthAdminError, find_user_id_by_email
from ai_hackathon_team_a.worker_settings import WorkerSettings


@pytest.fixture
def settings() -> WorkerSettings:
    return WorkerSettings(
        _env_file=None,
        database_url="postgresql://localhost/decision_trace",
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="service-role-key",
        supabase_jwt_alg="ES256",
        resend_api_key="resend-key",
        mail_from="noreply@example.test",
        app_base_url="https://app.example.test",
        worker_shared_secret="w" * 32,
        cron_secret="c" * 32,
        daily_cost_limit_usd=5.0,
        system_alert_email="alert@example.test",
    )  # type: ignore[arg-type]


def test_find_user_id_by_email_returns_matching_user(settings: WorkerSettings) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "users": [{"id": "11111111-1111-1111-1111-111111111111", "email": "a@example.test"}]
            },
        )
    )

    user_id = find_user_id_by_email("a@example.test", settings=settings, transport=transport)

    assert user_id == "11111111-1111-1111-1111-111111111111"


def test_find_user_id_by_email_is_case_insensitive(settings: WorkerSettings) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json={"users": [{"id": "1", "email": "Someone@Example.test"}]}
        )
    )

    user_id = find_user_id_by_email("someone@example.test", settings=settings, transport=transport)

    assert user_id == "1"


def test_find_user_id_by_email_returns_none_when_not_found(settings: WorkerSettings) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"users": []}))

    user_id = find_user_id_by_email("nobody@example.test", settings=settings, transport=transport)

    assert user_id is None


def test_find_user_id_by_email_raises_on_error_response(settings: WorkerSettings) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(500, text="boom"))

    with pytest.raises(AuthAdminError):
        find_user_id_by_email("a@example.test", settings=settings, transport=transport)
