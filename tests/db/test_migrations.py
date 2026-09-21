"""マイグレーションが空の DB に適用できることを確かめる。"""

import psycopg


def test_migrations_apply_to_empty_database(migrated_database_url: str) -> None:
    with psycopg.connect(migrated_database_url) as conn:
        for table in ("projects", "project_members", "events", "reports", "source_segments"):
            row = conn.execute("SELECT to_regclass(%s)", (f"public.{table}",)).fetchone()
            assert row is not None
            assert row[0] == table
