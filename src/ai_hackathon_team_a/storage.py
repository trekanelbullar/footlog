"""Supabase Storage の操作（設計書 §4 の2、S10：ここだけに閉じ込める）。

サービスロールキーは ``WorkerSettings`` から取る。テストでは ``transport`` に
``httpx.MockTransport`` を渡して差し替える。
"""

from datetime import UTC, datetime, timedelta

import httpx

from ai_hackathon_team_a.worker_settings import WorkerSettings

_BUCKET = "sources"
SIGNED_URL_EXPIRES_SECONDS = 60


class StorageError(RuntimeError):
    """Supabase Storage への操作が失敗したことを表す。"""


def upload_object(
    path: str,
    data: bytes,
    *,
    content_type: str,
    settings: WorkerSettings,
    transport: httpx.BaseTransport | None = None,
) -> None:
    """非公開バケットにファイルの実体を置く（既存パスは上書き）。"""

    with _client(settings, transport=transport) as client:
        response = client.post(
            f"/object/{_BUCKET}/{path}",
            content=data,
            headers={"Content-Type": content_type, "x-upsert": "true"},
        )
    if response.status_code >= 400:
        raise StorageError(
            f"Supabase Storage upload failed: {response.status_code} {response.text}"
        )


def create_signed_url(
    path: str,
    *,
    settings: WorkerSettings,
    transport: httpx.BaseTransport | None = None,
) -> tuple[str, datetime]:
    """60秒の期限つきURLを発行する。戻り値は (URL, 失効時刻)。"""

    with _client(settings, transport=transport) as client:
        response = client.post(
            f"/object/sign/{_BUCKET}/{path}",
            json={"expiresIn": SIGNED_URL_EXPIRES_SECONDS},
        )
    if response.status_code >= 400:
        raise StorageError(f"Supabase Storage sign failed: {response.status_code} {response.text}")

    signed_path = response.json()["signedURL"]
    base = str(settings.supabase_url).rstrip("/")
    url = f"{base}/storage/v1{signed_path}"
    expires_at = datetime.now(UTC) + timedelta(seconds=SIGNED_URL_EXPIRES_SECONDS)
    return url, expires_at


def _client(settings: WorkerSettings, *, transport: httpx.BaseTransport | None) -> httpx.Client:
    key = settings.supabase_service_role_key.get_secret_value()
    base_url = f"{str(settings.supabase_url).rstrip('/')}/storage/v1"
    return httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {key}", "apikey": key},
        transport=transport,
        timeout=30.0,
    )
