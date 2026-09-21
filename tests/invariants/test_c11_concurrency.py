"""C11：2スレッドから同時に実行しても、レポートが1版しかできない（設計書 §5.2）。"""

import json
import threading
import time
from uuid import UUID

import psycopg

import ai_hackathon_team_a.run as run_module
from ai_hackathon_team_a.db import ConnectionPool
from ai_hackathon_team_a.llm import LlmResult

_FAKE_EXTRACT = json.dumps(
    {
        "events": [
            {
                "kind": "decision",
                "summary": "Xを採用",
                "reason": "安いから",
                "occurred_at": "2026-09-21T10:00:00+09:00",
                "segment_ids": ["S1-1"],
                "origin": "human_originated",
                "supersedes_event_no": None,
                "conflicts_with_event_no": None,
            }
        ],
        "suspected_injection_segment_ids": [],
    }
)
_FAKE_SUPPORT = json.dumps({"results": ["supported"]})
_FAKE_ASSEMBLE = json.dumps(
    {
        "sections": {
            "purpose": [{"text": "目的はG", "event_nos": [1]}],
            "current_state": [],
            "direction": [],
        },
        "summary_for_mail": "要約",
    }
)
_FAKE_JUDGE = json.dumps({"status": "pass", "notes": []})


def _slow_fake_llm(stage: str, messages, *, run, json_mode=False, tools=None) -> LlmResult:
    # 実行時間を延ばし、2スレッドの実行区間が確実に重なるようにする。
    time.sleep(0.2)
    text = {
        "EXTRACT": _FAKE_EXTRACT,
        "SUPPORT": _FAKE_SUPPORT,
        "ASSEMBLE": _FAKE_ASSEMBLE,
        "JUDGE": _FAKE_JUDGE,
    }[stage]
    return LlmResult(
        text=text, tool_calls=None, input_tokens=10, output_tokens=10, model="fake", estimated=True
    )


def _setup_project_with_run(database_url: str) -> tuple[UUID, UUID]:
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


def test_concurrent_execute_run_on_same_run_id_produces_one_report_version(
    migrated_database_url: str, worker_settings
) -> None:
    project_id, run_id = _setup_project_with_run(migrated_database_url)
    pool = ConnectionPool(migrated_database_url)
    worker_settings = worker_settings.model_copy(update={"daily_cost_limit_usd": 500.0})

    outcomes: list[str] = []

    def _worker() -> None:
        outcome = run_module.execute_run(
            run_id, worker_settings=worker_settings, pool=pool, llm_call=_slow_fake_llm
        )
        outcomes.append(outcome.outcome)

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(outcomes) == ["locked", "report_created"]

    with psycopg.connect(migrated_database_url) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM reports WHERE project_id = %s", (project_id,)
        ).fetchone()[0]
    assert count == 1
