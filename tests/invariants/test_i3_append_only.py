"""I3：出来事・履歴は書き換えも削除もできない（設計書 §3.2、【C-X4】）。

§3.2 の対象の全部の表について、``app_worker`` として UPDATE・DELETE・TRUNCATE を
それぞれ試し、すべて拒否されること、試した後の行数と中身が変わっていないことを
確かめる。
"""

from uuid import UUID

import psycopg
import pytest

# 完全な追記専用（UPDATE・DELETE・TRUNCATE のすべてが REVOKE + トリガーで拒否される）。
_FULLY_APPEND_ONLY_TABLES = (
    "events",
    "source_audit_log",
    "reports",
    "source_versions",
    "notifications_log",
    "model_calls_log",
)

# 決めた列だけ更新可（UPDATE 自体は許可されているが、対象外の列を変えるとトリガーが
# 拒否する。DELETE・TRUNCATE は REVOKE で拒否される）。
_GUARDED_TABLES = ("source_segments", "agent_questions")


def _seed(conn: psycopg.Connection) -> dict[str, UUID]:
    """全表 FK を満たす最小限の行を1件ずつ作る。"""

    project_id = conn.execute(
        "INSERT INTO projects (name, goal_description) VALUES ('P', 'G') RETURNING id"
    ).fetchone()[0]
    user_id = conn.execute("SELECT gen_random_uuid()").fetchone()[0]
    run_id = conn.execute(
        "INSERT INTO runs (project_id, trigger, status) VALUES (%s, 'manual', 'running') "
        "RETURNING id",
        (project_id,),
    ).fetchone()[0]
    source_id = conn.execute(
        "INSERT INTO project_sources (project_id, source_no, type, uploaded_by, recorded_at, "
        "visibility, next_seq) VALUES (%s, 1, 'file', %s, now(), 'all', 1) RETURNING id",
        (project_id, user_id),
    ).fetchone()[0]
    version_id = conn.execute(
        "INSERT INTO source_versions (source_id, version_no, content_hash, extracted_text, "
        "storage_path) VALUES (%s, 1, 'h', 't', 'p') RETURNING id",
        (source_id,),
    ).fetchone()[0]
    conn.execute(
        "UPDATE project_sources SET current_version_id = %s WHERE id = %s",
        (version_id, source_id),
    )
    conn.execute(
        "INSERT INTO source_segments (project_id, source_version_id, label, seq, speaker, "
        "text, is_new, consumed) VALUES (%s, %s, 'S1-1', 1, 'user', 'hello', true, false)",
        (project_id, version_id),
    )
    event_id = conn.execute(
        "INSERT INTO events (project_id, run_id, event_no, kind, summary, occurred_at, "
        "segment_ids, visibility_at_creation, partition) VALUES "
        "(%s, %s, 1, 'decision', 'X', now(), '{S1-1}', 'all', 'all') RETURNING id",
        (project_id, run_id),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO reports (project_id, run_id, version_no, audience, kind, judge_status, "
        "body_markdown, evidence_catalog, event_ids, cited_segment_ids, input_segment_ids, "
        "flags, summary_for_mail) VALUES (%s, %s, 1, 'all', 'baseline', 'pass', 'body', "
        "'{}'::jsonb, '{1}', '{S1-1}', '{S1-1}', '{}'::jsonb, 'summary')",
        (project_id, run_id),
    )
    conn.execute(
        "INSERT INTO agent_questions (project_id, run_id, asked_to, question, "
        "related_event_id, partition) VALUES (%s, %s, %s, 'Q?', %s, 'all')",
        (project_id, run_id, user_id, event_id),
    )
    conn.execute(
        "INSERT INTO source_audit_log (source_id, changed_by, field, old_value, new_value, "
        "reason) VALUES (%s, %s, 'visibility', 'all', 'managers_only', 'private')",
        (source_id, user_id),
    )
    conn.execute(
        "INSERT INTO notifications_log (project_id, kind, recipients) "
        "VALUES (%s, 'report', '{a@example.test}')",
        (project_id,),
    )
    conn.execute(
        "INSERT INTO model_calls_log (project_id, run_id, stage, model, input_tokens, "
        "output_tokens, estimated_cost_usd, latency_ms, ok) VALUES "
        "(%s, %s, 'EXTRACT', 'fake', 1, 1, 0.0, 1, true)",
        (project_id, run_id),
    )
    conn.commit()
    return {"project_id": project_id, "run_id": run_id}


@pytest.fixture
def seeded_database_url(migrated_database_url: str) -> str:
    with psycopg.connect(migrated_database_url) as conn:
        _seed(conn)
    return migrated_database_url


def _snapshot(conn: psycopg.Connection, table: str) -> tuple[int, str]:
    row_count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    checksum = conn.execute(
        f"SELECT md5(COALESCE(string_agg(t.*::text, ','), '')) FROM {table} t"
    ).fetchone()[0]
    return row_count, checksum


def _reject(conn: psycopg.Connection, sql: str) -> None:
    """REVOKE（``InsufficientPrivilege``）・トリガー（``RaiseException``）のどちらでも拒否を認める。

    ``autocommit`` 接続なので、失敗した文だけが独立してロールバックされる。
    """

    with pytest.raises((psycopg.errors.InsufficientPrivilege, psycopg.errors.RaiseException)):
        conn.execute(sql)


@pytest.mark.parametrize("table", _FULLY_APPEND_ONLY_TABLES)
def test_fully_append_only_tables_reject_update_delete_truncate(
    seeded_database_url: str, table: str
) -> None:
    with psycopg.connect(seeded_database_url, autocommit=True) as conn:
        before = _snapshot(conn, table)
        conn.execute("SET ROLE app_worker")
        try:
            _reject(conn, f"UPDATE {table} SET id = id WHERE false")
            _reject(conn, f"DELETE FROM {table} WHERE false")
            _reject(conn, f"TRUNCATE {table}")
        finally:
            conn.execute("RESET ROLE")

        after = _snapshot(conn, table)
    assert before == after


@pytest.mark.parametrize("table", _GUARDED_TABLES)
def test_guarded_tables_reject_delete_and_truncate(seeded_database_url: str, table: str) -> None:
    with psycopg.connect(seeded_database_url, autocommit=True) as conn:
        before = _snapshot(conn, table)
        conn.execute("SET ROLE app_worker")
        try:
            _reject(conn, f"DELETE FROM {table} WHERE false")
            _reject(conn, f"TRUNCATE {table}")
        finally:
            conn.execute("RESET ROLE")

        after = _snapshot(conn, table)
    assert before == after


def test_source_segments_rejects_update_to_protected_columns(seeded_database_url: str) -> None:
    with psycopg.connect(seeded_database_url, autocommit=True) as conn:
        before = _snapshot(conn, "source_segments")
        conn.execute("SET ROLE app_worker")
        try:
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute("UPDATE source_segments SET text = 'tampered' WHERE label = 'S1-1'")
        finally:
            conn.execute("RESET ROLE")
        after = _snapshot(conn, "source_segments")
    assert before == after


def test_source_segments_allows_update_to_consumed_and_carry_count(
    seeded_database_url: str,
) -> None:
    with psycopg.connect(seeded_database_url, autocommit=True) as conn:
        conn.execute("SET ROLE app_worker")
        try:
            conn.execute(
                "UPDATE source_segments SET consumed = true, carry_count = 1 WHERE label = 'S1-1'"
            )
        finally:
            conn.execute("RESET ROLE")
        row = conn.execute(
            "SELECT consumed, carry_count FROM source_segments WHERE label = 'S1-1'"
        ).fetchone()
    assert row == (True, 1)


def test_agent_questions_rejects_update_to_protected_columns(seeded_database_url: str) -> None:
    with psycopg.connect(seeded_database_url, autocommit=True) as conn:
        before = _snapshot(conn, "agent_questions")
        conn.execute("SET ROLE app_worker")
        try:
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute("UPDATE agent_questions SET question = 'tampered' WHERE true")
        finally:
            conn.execute("RESET ROLE")
        after = _snapshot(conn, "agent_questions")
    assert before == after


def test_agent_questions_allows_update_to_status_and_answer_source_id(
    seeded_database_url: str,
) -> None:
    with psycopg.connect(seeded_database_url, autocommit=True) as conn:
        conn.execute("SET ROLE app_worker")
        try:
            conn.execute("UPDATE agent_questions SET status = 'answered' WHERE true")
        finally:
            conn.execute("RESET ROLE")
        row = conn.execute("SELECT status FROM agent_questions LIMIT 1").fetchone()
    assert row == ("answered",)
