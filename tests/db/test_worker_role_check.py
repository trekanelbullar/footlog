"""起動時の接続ユーザー確認（追加指示 AD-5）：db.check_connected_as_app_worker のテスト。"""

import psycopg
import pytest

from ai_hackathon_team_a.db import WrongDatabaseRoleError, check_connected_as_app_worker


def test_superuser_or_wrong_name_connection_is_rejected(migrated_database_url: str) -> None:
    """マイグレーションを流した接続（app_worker ではない）は拒否される。"""

    with psycopg.connect(migrated_database_url) as conn, pytest.raises(WrongDatabaseRoleError):
        check_connected_as_app_worker(conn)


def test_app_worker_role_is_accepted(migrated_database_url: str) -> None:
    """SET ROLE app_worker で見えている接続は通る（BYPASSRLS を持たないため）。"""

    with psycopg.connect(migrated_database_url, autocommit=True) as conn:
        conn.execute("SET ROLE app_worker")
        try:
            check_connected_as_app_worker(conn)
        finally:
            conn.execute("RESET ROLE")
