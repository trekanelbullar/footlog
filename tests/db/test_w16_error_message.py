"""W16 の ``error_message``：例外の文字列をそのまま返さない（設計書 §8 I1）。"""

from uuid import UUID

import psycopg
import pytest

import ai_hackathon_team_a.run as run_module
from ai_hackathon_team_a.db import ConnectionPool

_SECRET_LOOKING_MESSAGE = (
    "connection to server failed while authenticating to db.internal.example (see server logs)"
)


def _setup_project_with_segment(database_url: str) -> UUID:
    with psycopg.connect(database_url) as conn:
        project_id = conn.execute(
            "INSERT INTO projects (name, goal_description) VALUES ('P', 'G') RETURNING id"
        ).fetchone()[0]
        user_id = conn.execute("SELECT gen_random_uuid()").fetchone()[0]
        conn.execute(
            "INSERT INTO project_members (project_id, user_id, email, role) "
            "VALUES (%s, %s, 'm@example.test', 'manager')",
            (project_id, user_id),
        )
        source_id = conn.execute(
            "INSERT INTO project_sources (project_id, source_no, type, uploaded_by, "
            "recorded_at, visibility, next_seq) VALUES (%s, 1, 'conversation', %s, now(), "
            "'all', 1) RETURNING id",
            (project_id, user_id),
        ).fetchone()[0]
        version_id = conn.execute(
            "INSERT INTO source_versions (source_id, version_no, content_hash, extracted_text) "
            "VALUES (%s, 1, 'h', 't') RETURNING id",
            (source_id,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE project_sources SET current_version_id = %s WHERE id = %s",
            (version_id, source_id),
        )
        conn.execute(
            "INSERT INTO source_segments (project_id, source_version_id, label, seq, speaker, "
            "text, is_new, consumed) VALUES (%s, %s, 'S1-1', 1, 'user', 'Xを採用する', "
            "true, false)",
            (project_id, version_id),
        )
        run_id = conn.execute(
            "INSERT INTO runs (project_id, trigger, status) VALUES (%s, 'manual', 'queued') "
            "RETURNING id",
            (project_id,),
        ).fetchone()[0]
        conn.commit()
    return project_id, run_id


def _raising_llm(stage, messages, *, run, json_mode=False, tools=None):
    raise RuntimeError(_SECRET_LOOKING_MESSAGE)


def test_generic_exception_does_not_leak_details_in_error_message(
    migrated_database_url: str, worker_settings
) -> None:
    project_id, run_id = _setup_project_with_segment(migrated_database_url)
    pool = ConnectionPool(migrated_database_url)

    outcome = run_module.execute_run(
        run_id, worker_settings=worker_settings, pool=pool, llm_call=_raising_llm
    )

    assert outcome.outcome == "failed"
    assert outcome.error_message == "実行中にエラーが起きました。"
    assert "db.internal.example" not in outcome.error_message

    with psycopg.connect(migrated_database_url) as conn:
        status, db_outcome, error_message = conn.execute(
            "SELECT status, outcome, error_message FROM runs WHERE id = %s", (run_id,)
        ).fetchone()
    assert (status, db_outcome) == ("failed", "failed")
    assert error_message == "実行中にエラーが起きました。"
    assert "db.internal.example" not in error_message
    del project_id


def test_timeout_reports_a_friendly_japanese_message(
    migrated_database_url: str, worker_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id, run_id = _setup_project_with_segment(migrated_database_url)
    pool = ConnectionPool(migrated_database_url)

    # 実際に240秒待たず、期限をすぐ過ぎた状態にする（時間の差し替え）。
    monkeypatch.setattr(run_module, "_RUN_TIMEOUT_SECONDS", -1)

    def _slow_llm(stage, messages, *, run, json_mode=False, tools=None):
        raise AssertionError("deadline は最初の _check_deadline で切れているはず")

    outcome = run_module.execute_run(
        run_id, worker_settings=worker_settings, pool=pool, llm_call=_slow_llm
    )

    assert outcome.outcome == "failed"
    assert outcome.error_message == "時間内に終わりませんでした。"
    del project_id
