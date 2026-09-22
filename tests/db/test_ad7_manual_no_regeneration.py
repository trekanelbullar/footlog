"""AD-7：手動実行では Judge が不合格でもやり直さず flagged で保存する。

定期実行（``trigger = schedule``）ではこれまでどおり1回だけやり直す。機械の
検査（``pipeline/checks.py``）はどちらでも必ずかかる。偽の LLM で段階の呼び出し
順を記録し、ASSEMBLE の呼ばれた回数で確かめる。
"""

import json
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
            "decisions": [{"text": "Xを採用", "event_nos": [1]}],
            "reasons": [{"text": "安いから", "event_nos": [1]}],
            "rejected_options": [],
            "next_steps": [],
            "current_status": [],
        },
        "summary_for_mail": "要約",
    }
)
_FAKE_JUDGE_FAIL = json.dumps({"status": "fail", "notes": ["理由が薄い"]})


def _fake_llm_always_failing_judge(calls: list[str]):
    def _call(stage: str, messages, *, run, json_mode=False, tools=None) -> LlmResult:
        calls.append(stage)
        text = {
            "EXTRACT": _FAKE_EXTRACT,
            "SUPPORT": _FAKE_SUPPORT,
            "ASSEMBLE": _FAKE_ASSEMBLE,
            "JUDGE": _FAKE_JUDGE_FAIL,
        }[stage]
        return LlmResult(
            text=text,
            tool_calls=None,
            input_tokens=10,
            output_tokens=10,
            model="fake",
            estimated=True,
        )

    return _call


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
        conn.commit()
    return project_id


def _insert_queued_run(database_url: str, project_id: UUID, *, trigger: str) -> UUID:
    with psycopg.connect(database_url) as conn:
        run_id = conn.execute(
            "INSERT INTO runs (project_id, trigger, status) VALUES (%s, %s, 'queued') RETURNING id",
            (project_id, trigger),
        ).fetchone()[0]
        conn.commit()
    return run_id


def test_manual_run_assembles_once_and_flags_when_judge_fails(
    migrated_database_url: str, worker_settings
) -> None:
    project_id = _setup_project_with_segment(migrated_database_url)
    run_id = _insert_queued_run(migrated_database_url, project_id, trigger="manual")
    pool = ConnectionPool(migrated_database_url)
    worker_settings = worker_settings.model_copy(update={"daily_cost_limit_usd": 500.0})
    calls: list[str] = []

    outcome = run_module.execute_run(
        run_id,
        worker_settings=worker_settings,
        pool=pool,
        llm_call=_fake_llm_always_failing_judge(calls),
    )

    assert outcome.outcome == "report_created"
    assert calls.count("ASSEMBLE") == 1
    assert calls.count("JUDGE") == 1
    with psycopg.connect(migrated_database_url) as conn:
        judge_status = conn.execute(
            "SELECT judge_status FROM reports WHERE run_id = %s AND audience = 'all'", (run_id,)
        ).fetchone()[0]
    assert judge_status == "flagged"


def test_scheduled_run_assembles_twice_when_judge_fails(
    migrated_database_url: str, worker_settings
) -> None:
    project_id = _setup_project_with_segment(migrated_database_url)
    run_id = _insert_queued_run(migrated_database_url, project_id, trigger="schedule")
    pool = ConnectionPool(migrated_database_url)
    worker_settings = worker_settings.model_copy(update={"daily_cost_limit_usd": 500.0})
    calls: list[str] = []

    outcome = run_module.execute_run(
        run_id,
        worker_settings=worker_settings,
        pool=pool,
        llm_call=_fake_llm_always_failing_judge(calls),
    )

    assert outcome.outcome == "report_created"
    assert calls.count("ASSEMBLE") == 2
    assert calls.count("JUDGE") == 2
    with psycopg.connect(migrated_database_url) as conn:
        judge_status = conn.execute(
            "SELECT judge_status FROM reports WHERE run_id = %s AND audience = 'all'", (run_id,)
        ).fetchone()[0]
    assert judge_status == "flagged"
