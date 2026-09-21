"""マイグレーション適用の CLI 入口。実体は ``ai_hackathon_team_a.db_migrate``。

使い方::

    uv run python db/migrate.py

``DATABASE_URL`` は ``WorkerSettings`` 経由で ``.env`` から読み込む。
"""

import sys

from ai_hackathon_team_a.db_migrate import migrate
from ai_hackathon_team_a.worker_settings import get_worker_settings


def main() -> int:
    settings = get_worker_settings()
    applied = migrate(settings.database_url)
    if applied:
        print(f"適用したマイグレーション: {', '.join(applied)}")
    else:
        print("適用するマイグレーションはありません。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
