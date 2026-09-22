"""W17 定期実行のテスト（設計書 §2.3・§5.7）。

``X-Cron-Secret`` 以外を拒否すること、ロック中のプロジェクトを飛ばすこと、
240秒相当の打ち切り（``budget_seconds`` を差し替えて確かめる）。
"""

import json
from uuid import UUID

import psycopg
from fastapi.testclient import TestClient

from ai_hackathon_team_a.cron import run_cron_tick
from ai_hackathon_team_a.db import ConnectionPool
from ai_hackathon_team_a.llm import LlmResult

# tests/conftest.py と同じ合言葉（``tests`` は import 可能なパッケージではないため、
# conftest.py のコメントにあるとおりフィクスチャ経由にできない定数はここで複製する）。
_WORKER_SECRET = "w" * 32
_CRON_SECRET = "c" * 32

_FAKE_EMPTY_EXTRACT = json.dumps({"events": [], "suspected_injection_segment_ids": []})


def _fake_llm(stage: str, messages, *, run, json_mode=False, tools=None) -> LlmResult:
    text = {"EXTRACT": _FAKE_EMPTY_EXTRACT}.get(stage, "{}")
    return LlmResult(
        text=text, tool_calls=None, input_tokens=1, output_tokens=1, model="fake", estimated=True
    )


def _insert_active_project(database_url: str, *, name: str = "P") -> UUID:
    with psycopg.connect(database_url) as conn:
        project_id = conn.execute(
            "INSERT INTO projects (name, goal_description, status) "
            "VALUES (%s, 'G', 'active') RETURNING id",
            (name,),
        ).fetchone()[0]
        conn.commit()
    return project_id


# ---- ヘッダー ---------------------------------------------------------------


def test_cron_tick_requires_cron_secret_and_rejects_worker_secret(api_client: TestClient) -> None:
    no_header = api_client.post("/internal/cron/tick")
    assert no_header.status_code == 401

    wrong_header = api_client.post(
        "/internal/cron/tick", headers={"X-Worker-Secret": _WORKER_SECRET}
    )
    assert wrong_header.status_code == 401

    ok = api_client.post("/internal/cron/tick", headers={"X-Cron-Secret": _CRON_SECRET})
    assert ok.status_code == 200
    body = ok.json()
    assert set(body) == {
        "projects_checked",
        "runs_executed",
        "runs_skipped_locked",
        "no_progress_alerts_sent",
    }


# ---- ロック中のプロジェクトを飛ばす -----------------------------------------


def test_cron_tick_skips_locked_project_and_runs_the_other(
    migrated_database_url: str, worker_settings
) -> None:
    locked_project = _insert_active_project(migrated_database_url, name="locked")
    free_project = _insert_active_project(migrated_database_url, name="free")
    pool = ConnectionPool(migrated_database_url)

    lock_conn = psycopg.connect(migrated_database_url, autocommit=True)
    lock_conn.execute(
        "SELECT pg_advisory_lock(hashtextextended(%s, 0))", (f"decision-trace:{locked_project}",)
    )
    try:
        result = run_cron_tick(worker_settings=worker_settings, pool=pool, llm_call=_fake_llm)
    finally:
        lock_conn.execute(
            "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
            (f"decision-trace:{locked_project}",),
        )
        lock_conn.close()

    assert result.projects_checked == 2
    assert result.runs_skipped_locked == 1
    assert result.runs_executed == 1

    with psycopg.connect(migrated_database_url) as conn:
        locked_run_outcome = conn.execute(
            "SELECT outcome FROM runs WHERE project_id = %s", (locked_project,)
        ).fetchone()[0]
        free_run_outcome = conn.execute(
            "SELECT outcome FROM runs WHERE project_id = %s", (free_project,)
        ).fetchone()[0]
    assert locked_run_outcome == "locked"
    assert free_run_outcome == "no_new_events"


# ---- 打ち切り ---------------------------------------------------------------


def test_cron_tick_stops_before_budget_and_leaves_rest_for_next_time(
    migrated_database_url: str, worker_settings
) -> None:
    _insert_active_project(migrated_database_url, name="p1")
    _insert_active_project(migrated_database_url, name="p2")
    pool = ConnectionPool(migrated_database_url)

    # 既に時間切れの予算を渡し、1件も処理せずに終わることを確かめる（時間の差し替え）。
    result = run_cron_tick(
        worker_settings=worker_settings, pool=pool, llm_call=_fake_llm, budget_seconds=-1.0
    )

    assert result.projects_checked == 0
    assert result.runs_executed == 0
    assert result.runs_skipped_locked == 0
    assert result.no_progress_alerts_sent == 0

    with psycopg.connect(migrated_database_url) as conn:
        run_count = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    assert run_count == 0
