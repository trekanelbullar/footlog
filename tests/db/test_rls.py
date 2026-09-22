"""RLS のポリシー方式（追加指示 AD-4）の検査。

app_worker には BYPASSRLS を付けず、各表の app_worker_all ポリシーだけで
読み書きできることと、ポリシーを持たないロールからは行が見えないことを確かめる。
"""

import psycopg


def test_app_worker_has_no_bypassrls(migrated_database_url: str) -> None:
    with psycopg.connect(migrated_database_url) as conn:
        row = conn.execute(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname = 'app_worker'"
        ).fetchone()
        assert row is not None
        assert row[0] is False


def test_app_worker_can_select_and_insert_via_policy(migrated_database_url: str) -> None:
    with psycopg.connect(migrated_database_url, autocommit=True) as conn:
        conn.execute("SET ROLE app_worker")
        try:
            conn.execute(
                "INSERT INTO projects (name, goal_description) VALUES (%s, %s)", ("t", "g")
            )
            rows = conn.execute("SELECT name FROM projects").fetchall()
            assert ("t",) in rows
        finally:
            conn.execute("RESET ROLE")


def test_rls_blocks_role_without_policy(migrated_database_url: str) -> None:
    """ポリシーの無いロールからは（表の GRANT があっても）行が見えない。"""

    with psycopg.connect(migrated_database_url, autocommit=True) as conn:
        conn.execute("SET ROLE app_worker")
        conn.execute(
            "INSERT INTO projects (name, goal_description) VALUES (%s, %s)", ("secret", "g")
        )
        conn.execute("RESET ROLE")

        conn.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'test_rls_anon') THEN
                    CREATE ROLE test_rls_anon NOLOGIN;
                END IF;
            END
            $$;
            """
        )
        conn.execute("GRANT USAGE ON SCHEMA public TO test_rls_anon")
        conn.execute("GRANT SELECT ON projects TO test_rls_anon")
        conn.execute("SET ROLE test_rls_anon")
        try:
            rows = conn.execute("SELECT name FROM projects").fetchall()
            assert rows == []
        finally:
            conn.execute("RESET ROLE")


def test_all_public_tables_have_rls_enabled(migrated_database_url: str) -> None:
    """schema_migrations を含む public スキーマの全表で RLS が有効であること。"""

    with psycopg.connect(migrated_database_url) as conn:
        tables = _table_names(conn, rls_only=False)
        rls_enabled = _table_names(conn, rls_only=True)
        assert tables == rls_enabled, f"RLS が無効な表: {sorted(tables - rls_enabled)}"


def test_rls_tables_except_schema_migrations_have_app_worker_policy(
    migrated_database_url: str,
) -> None:
    """RLS が有効な表のうち schema_migrations 以外には app_worker_all ポリシーがある。"""

    with psycopg.connect(migrated_database_url) as conn:
        rls_tables = _table_names(conn, rls_only=True) - {"schema_migrations"}
        policy_tables = {
            row[0]
            for row in conn.execute(
                "SELECT tablename FROM pg_policies "
                "WHERE schemaname = 'public' AND policyname = 'app_worker_all'"
            ).fetchall()
        }
        assert rls_tables == policy_tables


def _table_names(conn: psycopg.Connection, *, rls_only: bool) -> set[str]:
    condition = "AND c.relrowsecurity" if rls_only else ""
    rows = conn.execute(
        f"""
        SELECT c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r' {condition}
        """
    ).fetchall()
    return {row[0] for row in rows}
