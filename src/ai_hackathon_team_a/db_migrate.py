"""未適用のマイグレーション SQL をファイル名順に適用する最小限の仕組み。

適用済みのファイル名は ``schema_migrations`` 表に記録する。``db/migrate.py`` の
運用スクリプトと、DB を使うテストの両方からこのモジュールを呼ぶ。
"""

from pathlib import Path

import psycopg
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# repo 直下の db/migrations/（このファイルは src/ai_hackathon_team_a/ 配下にある）。
DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"


class MigrationSettings(BaseSettings):
    """マイグレーション専用の接続設定（追加指示 AD-5）。

    アプリの ``DATABASE_URL``（``app_worker``）とは別に、Supabase の ``postgres``
    ユーザーなど権限を持つ接続で流すための ``MIGRATION_DATABASE_URL`` だけを読む。
    ``db/migrate.py`` の CLI からだけ使う（``migrate()`` 自体は引数の URL をそのまま
    使うだけで、環境変数は読まない）。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )

    migration_database_url: str = Field(min_length=1)


def _applied_migrations(conn: psycopg.Connection) -> set[str]:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename text PRIMARY KEY,
            applied_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # Supabase は public スキーマの表を anon から REST で読める既定のため、RLS を
    # 有効にしておく（ポリシーは無し＝app_worker からも読めない。マイグレーション
    # 自体は所有者（postgres）で流すため、この表を読めなくても支障はない）。
    conn.execute("ALTER TABLE schema_migrations ENABLE ROW LEVEL SECURITY")
    rows = conn.execute("SELECT filename FROM schema_migrations").fetchall()
    return {row[0] for row in rows}


def migrate(database_url: str, migrations_dir: Path | None = None) -> list[str]:
    """未適用の ``*.sql`` を適用し、適用したファイル名の一覧を返す。"""

    directory = migrations_dir or DEFAULT_MIGRATIONS_DIR
    applied_now: list[str] = []

    with psycopg.connect(database_url, autocommit=False) as conn:
        already_applied = _applied_migrations(conn)
        conn.commit()

        for path in sorted(directory.glob("*.sql")):
            if path.name in already_applied:
                continue
            sql = path.read_text(encoding="utf-8")
            with conn.transaction():
                conn.execute(sql)  # type: ignore[arg-type]
                conn.execute("INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,))
            applied_now.append(path.name)

    return applied_now
