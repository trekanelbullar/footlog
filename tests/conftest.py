"""テスト全体の共通設定。

``APP_ENV=test`` を既定にし、``WorkerSettings`` を明示的な ``app_env`` 指定なしで
組み立てるテストでも、起動時の接続ユーザー確認（``db.check_connected_as_app_worker``、
追加指示 AD-5）が働かないようにする。
"""

import os

os.environ.setdefault("APP_ENV", "test")
