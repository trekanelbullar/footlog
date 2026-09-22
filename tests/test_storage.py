"""Supabase Storage の操作（設計書 §4 の2、S10）のテスト。httpx.MockTransport で差し替える。"""

import httpx
import pytest

from ai_hackathon_team_a.storage import StorageError, create_signed_url, upload_object
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


def test_upload_object_sends_bearer_and_apikey_headers(settings: WorkerSettings) -> None:
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"Key": "sources/x"})

    upload_object(
        "projects/p/sources/1/v1/f.txt",
        b"hello",
        content_type="text/plain",
        settings=settings,
        transport=httpx.MockTransport(handler),
    )

    request = captured["request"]
    assert request.headers["Authorization"] == "Bearer service-role-key"
    assert request.headers["apikey"] == "service-role-key"
    assert request.url.path == "/storage/v1/object/sources/projects/p/sources/1/v1/f.txt"


def test_upload_object_raises_storage_error_on_failure(settings: WorkerSettings) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(403, text="forbidden"))

    with pytest.raises(StorageError):
        upload_object(
            "projects/p/sources/1/v1/f.txt",
            b"hello",
            content_type="text/plain",
            settings=settings,
            transport=transport,
        )


def test_create_signed_url_builds_full_url_and_expiry(settings: WorkerSettings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/storage/v1/object/sign/sources/projects/p/sources/1/v1/f.txt"
        return httpx.Response(200, json={"signedURL": "/object/sign/sources/p/f.txt?token=abc"})

    url, expires_at = create_signed_url(
        "projects/p/sources/1/v1/f.txt", settings=settings, transport=httpx.MockTransport(handler)
    )

    assert url == "https://example.supabase.co/storage/v1/object/sign/sources/p/f.txt?token=abc"
    assert expires_at is not None


def test_create_signed_url_raises_storage_error_on_failure(settings: WorkerSettings) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(404, text="not found"))

    with pytest.raises(StorageError):
        create_signed_url("missing.txt", settings=settings, transport=transport)
