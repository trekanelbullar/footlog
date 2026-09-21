"""DB を使うテストの共通フィクスチャ（AD-3）。

接続先は ``TEST_DATABASE_URL`` だけから取り、``DATABASE_URL``（本番・開発用）は
絶対に使わない。ホスト名が ``localhost``・``127.0.0.1``・``postgres``（CI の
サービスコンテナ）のどれでもなければ、共有DBを誤って壊す事故を防ぐため、
テストを始めずに明示的に失敗させる。``TEST_DATABASE_URL`` が未設定のときだけ、
DB テスト一式を skip する。
"""

import os
from urllib.parse import urlparse

import psycopg
import pytest

from ai_hackathon_team_a.db_migrate import migrate

_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "postgres"}


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
