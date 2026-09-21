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
