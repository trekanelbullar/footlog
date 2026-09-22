"""psycopg 3 の同期接続プール（設計書 §3.3、最大5接続）。

worker は Supabase の直接接続またはセッションモードのプーラー（5432）に繋ぐ。
トランザクションモードのプーラー（6543）ではアドバイザリロックが効かないため使わない。
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from ai_hackathon_team_a.worker_settings import WorkerSettings, get_worker_settings

_DEFAULT_MAX_SIZE = 5
_REQUIRED_ROLE_NAME = "app_worker"


class WrongDatabaseRoleError(RuntimeError):
    """DATABASE_URL の接続ユーザーが app_worker ではないことを表す（追加指示 AD-5）。"""


class ConnectionPool:
    """DATABASE_URL への接続を最大 ``max_size`` 個まで使い回す最小限のプール。"""

    def __init__(self, dsn: str, *, max_size: int = _DEFAULT_MAX_SIZE) -> None:
        self._dsn = dsn
        self._semaphore = threading.BoundedSemaphore(max_size)
        self._idle: list[psycopg.Connection] = []
        self._idle_lock = threading.Lock()

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        """接続を1つ借りる。正常終了でコミット、例外時はロールバックする。"""

        self._semaphore.acquire()
        conn = self._checkout()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._checkin(conn)
            self._semaphore.release()

    def _checkout(self) -> psycopg.Connection:
        with self._idle_lock:
            while self._idle:
                conn = self._idle.pop()
                if not conn.closed:
                    return conn
        return psycopg.connect(self._dsn)

    def _checkin(self, conn: psycopg.Connection) -> None:
        if conn.closed:
            return
        with self._idle_lock:
            self._idle.append(conn)

    def close(self) -> None:
        """アイドル中の接続をすべて閉じる。"""

        with self._idle_lock:
            while self._idle:
                self._idle.pop().close()


def check_connected_as_app_worker(conn: psycopg.Connection) -> None:
    """DATABASE_URL の接続ユーザーが ``app_worker``（非スーパーユーザー・非BYPASSRLS）か確かめる。

    起動時（``APP_ENV != "test"`` のとき）に呼ぶ（追加指示 AD-5・AD-4）。管理者の
    接続文字列でうっかり起動していないかをここで止める。接続文字列やパスワードは
    メッセージにもログにも出さない。
    """

    row = conn.execute(
        "SELECT current_user, r.rolsuper, r.rolbypassrls "
        "FROM pg_roles r WHERE r.rolname = current_user"
    ).fetchone()
    current_user_name = row[0] if row else None
    is_superuser = bool(row[1]) if row else True
    bypasses_rls = bool(row[2]) if row else True

    if current_user_name != _REQUIRED_ROLE_NAME or is_superuser or bypasses_rls:
        raise WrongDatabaseRoleError(
            "DATABASE_URL のユーザーが app_worker ではありません"
            "（管理者の接続文字列で起動していないか確認してください）。"
        )


_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()


def get_pool(settings: WorkerSettings | None = None) -> ConnectionPool:
    """プロセス内で共有する接続プールを返す（無ければ作る）。"""

    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                resolved = settings or get_worker_settings()
                _pool = ConnectionPool(resolved.database_url)
    return _pool
