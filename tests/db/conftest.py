"""DB を使うテストの共通フィクスチャ（AD-3）。

接続先は ``TEST_DATABASE_URL`` だけから取り、``DATABASE_URL``（本番・開発用）は
絶対に使わない。ホスト名が ``localhost``・``127.0.0.1``・``postgres``（CI の
サービスコンテナ）のどれでもなければ、共有DBを誤って壊す事故を防ぐため、
テストを始めずに明示的に失敗させる。``TEST_DATABASE_URL`` が未設定のときだけ、
DB テスト一式を skip する。
"""

import os
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse
from uuid import UUID, uuid4

import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient

from ai_hackathon_team_a.api import app
from ai_hackathon_team_a.db_migrate import migrate
from ai_hackathon_team_a.worker_settings import WorkerSettings, get_worker_settings

_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "postgres"}

# API テスト共通の合言葉・JWT の作法（tests/test_api.py・tests/test_auth.py と同じ）。
WORKER_SECRET = "w" * 32
CRON_SECRET = "c" * 32
JWT_SECRET = "hs256-secret"
JWT_ISSUER = "https://example.supabase.co/auth/v1"


@pytest.fixture
def database_url() -> str:
    """TEST_DATABASE_URL を返す。未設定なら skip、許可されないホストなら失敗させる。"""

    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL が設定されていないため DB テストを飛ばします。")

    host = urlparse(url).hostname
    if host not in _ALLOWED_HOSTS:
        pytest.fail(
            f"TEST_DATABASE_URL のホスト名 {host!r} は許可されていません"
            f"（{sorted(_ALLOWED_HOSTS)} のいずれかのみ）。"
            "共有DBを誤って初期化しないための検査です。",
            pytrace=False,
        )
    return url


@pytest.fixture
def migrated_database_url(database_url: str) -> str:
    """テストごとに public スキーマを作り直してから、全マイグレーションを適用する。"""

    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        conn.execute("CREATE SCHEMA public")

    applied = migrate(database_url)
    assert applied, "空の DB にマイグレーションが1件も適用されなかった"
    return database_url


@pytest.fixture
def worker_settings(migrated_database_url: str) -> WorkerSettings:
    """API テスト用の WorkerSettings（DATABASE_URL だけ実DBに向ける）。"""

    return WorkerSettings(
        _env_file=None,
        database_url=migrated_database_url,
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="service-role-key",
        supabase_jwt_alg="HS256",
        supabase_jwt_secret=JWT_SECRET,
        resend_api_key="resend-key",
        mail_from="noreply@example.test",
        app_base_url="https://app.example.test",
        worker_shared_secret=WORKER_SECRET,
        cron_secret=CRON_SECRET,
        daily_cost_limit_usd=5.0,
        system_alert_email="alert@example.test",
        app_env="test",
    )  # type: ignore[arg-type]


@pytest.fixture
def api_client(worker_settings: WorkerSettings) -> Iterator[TestClient]:
    """DB につながった WorkerSettings で FastAPI アプリを叩く TestClient。"""

    app.dependency_overrides[get_worker_settings] = lambda: worker_settings
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_worker_settings, None)


def bearer_token(user_id: UUID, *, email: str | None = None) -> str:
    """1段目の作法（テスト内で鍵を生成）に合わせた HS256 の JWT。"""

    now = datetime.now(tz=UTC)
    payload: dict[str, object] = {
        "iss": JWT_ISSUER,
        "aud": "authenticated",
        "role": "authenticated",
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    if email is not None:
        payload["email"] = email
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


@pytest.fixture
def auth_headers() -> Callable[..., dict[str, str]]:
    """テスト関数の引数として使うフィクスチャ（``tests`` は import 可能なパッケージで
    はないため、ヘルパーは import ではなくフィクスチャとして渡す）。

    呼び出し方は ``auth_headers(user_id, email="...")``。``user_id`` を省略すると
    毎回新しい UUID を使う。
    """

    def _make(user_id: UUID | None = None, *, email: str | None = None) -> dict[str, str]:
        return {
            "X-Worker-Secret": WORKER_SECRET,
            "Authorization": f"Bearer {bearer_token(user_id or uuid4(), email=email)}",
        }

    return _make
