"""I3：events への UPDATE・DELETE・TRUNCATE が app_worker として拒否されることを確かめる。"""

import psycopg
import pytest


def test_app_worker_cannot_update_delete_truncate_events(migrated_database_url: str) -> None:
    with psycopg.connect(migrated_database_url, autocommit=True) as conn:
        conn.execute("SET ROLE app_worker")
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("UPDATE events SET summary = 'x' WHERE false")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("DELETE FROM events WHERE false")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("TRUNCATE events")
        finally:
            conn.execute("RESET ROLE")
