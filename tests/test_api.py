from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient

from ai_hackathon_team_a.api import app
from ai_hackathon_team_a.worker_settings import WorkerSettings, get_worker_settings

_WORKER_SECRET = "w" * 32
_CRON_SECRET = "c" * 32
_ISSUER = "https://example.supabase.co/auth/v1"


def _settings() -> WorkerSettings:
    return WorkerSettings(
        _env_file=None,
        database_url="postgresql://localhost/decision_trace",
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="service-role-key",
        supabase_jwt_alg="HS256",
        supabase_jwt_secret="hs256-secret",
        resend_api_key="resend-key",
        mail_from="noreply@example.test",
        app_base_url="https://app.example.test",
        worker_shared_secret=_WORKER_SECRET,
        cron_secret=_CRON_SECRET,
        daily_cost_limit_usd=5.0,
        system_alert_email="alert@example.test",
    )  # type: ignore[arg-type]


def _bearer_token() -> str:
    now = datetime.now(tz=UTC)
    payload = {
        "iss": _ISSUER,
        "aud": "authenticated",
        "role": "authenticated",
        "sub": str(uuid4()),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    return jwt.encode(payload, "hs256-secret", algorithm="HS256")


@pytest.fixture
def client() -> Iterator[TestClient]:
    app.dependency_overrides[get_worker_settings] = _settings
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_worker_settings, None)


def test_healthz_requires_no_auth(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_me_projects_requires_worker_secret(client: TestClient) -> None:
    response = client.get(
        "/internal/me/projects", headers={"Authorization": f"Bearer {_bearer_token()}"}
    )

    assert response.status_code == 401
    body = response.json()
    assert body["error"] == "unauthorized"
    assert "message" in body


def test_me_projects_rejects_wrong_worker_secret(client: TestClient) -> None:
    response = client.get(
        "/internal/me/projects",
        headers={
            "X-Worker-Secret": "wrong-secret",
            "Authorization": f"Bearer {_bearer_token()}",
        },
    )

    assert response.status_code == 401


def test_me_projects_succeeds_with_valid_secret_and_jwt(client: TestClient) -> None:
    response = client.get(
        "/internal/me/projects",
        headers={
            "X-Worker-Secret": _WORKER_SECRET,
            "Authorization": f"Bearer {_bearer_token()}",
        },
    )

    assert response.status_code == 200
    assert response.json() == []


def test_me_projects_rejects_missing_jwt(client: TestClient) -> None:
    response = client.get(
        "/internal/me/projects",
        headers={"X-Worker-Secret": _WORKER_SECRET},
    )

    assert response.status_code == 401


def test_cron_secret_is_not_accepted_on_user_route(client: TestClient) -> None:
    """X-Cron-Secret をユーザー用ルートに付けても、X-Worker-Secret 無しでは通らない。"""

    response = client.get(
        "/internal/me/projects",
        headers={"X-Cron-Secret": _CRON_SECRET},
    )

    assert response.status_code == 401
