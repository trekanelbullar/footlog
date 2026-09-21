"""マイグレーション適用の CLI 入口。実体は ``ai_hackathon_team_a.db_migrate``。

使い方::

    uv run python db/migrate.py

``DATABASE_URL``（app_worker 用、権限が絞られている）は読まない。``MIGRATION_DATABASE_URL``
（Supabase の ``postgres`` ユーザーなど、権限を持つ接続）だけを ``.env`` から読み込む
（追加指示 AD-5）。
"""

import sys

from ai_hackathon_team_a.db_migrate import MigrationSettings, migrate


def main() -> int:
    settings = MigrationSettings()  # type: ignore[call-arg]
    applied = migrate(settings.migration_database_url)
    if applied:
        print(f"適用したマイグレーション: {', '.join(applied)}")
    else:
        print("適用するマイグレーションはありません。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
