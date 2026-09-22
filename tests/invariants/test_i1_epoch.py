"""I1：送る直前の再確認（設計書 §5.3 (12)、【B-X2】）。

実行中に（偽の LLM の呼び出しの中で）``visibility_epoch`` が変わると、
``RunOutcome.notify_allowed`` が ``False`` になり、``projects.needs_rebuild`` が
``true`` のままになる（レポート自体は保存される）。
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
                "reason": None,
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
            "current_state": [{"text": "Xを採用した", "event_nos": [1]}],
            "direction": [],
        },
        "summary_for_mail": "要約",
    }
)
_FAKE_JUDGE = json.dumps({"status": "pass", "notes": []})


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
            "text, is_new, consumed) VALUES (%s, %s, 'S1-1', 1, 'user', %s, true, false)",
            (project_id, version_id, "Xを採用することにした"),
        )
        conn.commit()
    return project_id


def test_visibility_change_during_run_blocks_notification_and_keeps_needs_rebuild(
    migrated_database_url: str, worker_settings
) -> None:
    project_id = _setup_project_with_segment(migrated_database_url)
    run_id_row = None
    with psycopg.connect(migrated_database_url) as conn:
        run_id_row = conn.execute(
            "INSERT INTO runs (project_id, trigger, status) VALUES (%s, 'manual', 'queued') "
            "RETURNING id",
            (project_id,),
        ).fetchone()
        conn.commit()
    run_id = run_id_row[0]

    pool = ConnectionPool(migrated_database_url)
    worker_settings = worker_settings.model_copy(update={"daily_cost_limit_usd": 500.0})

    bumped = {"done": False}

    def _fake_llm(stage: str, messages, *, run, json_mode=False, tools=None) -> LlmResult:
        # EXTRACT の呼び出し中に、W11 相当の可視性の変更が別のリクエストで起きた
        # ことを模す（1回だけ）。
        if stage == "EXTRACT" and not bumped["done"]:
            bumped["done"] = True
            with psycopg.connect(migrated_database_url) as bump_conn:
                bump_conn.execute(
                    "UPDATE projects SET visibility_epoch = visibility_epoch + 1 WHERE id = %s",
                    (project_id,),
                )
                bump_conn.commit()
        text = {
            "EXTRACT": _FAKE_EXTRACT,
            "SUPPORT": _FAKE_SUPPORT,
            "ASSEMBLE": _FAKE_ASSEMBLE,
        }.get(stage, _FAKE_JUDGE)
        return LlmResult(
            text=text,
            tool_calls=None,
            input_tokens=10,
            output_tokens=10,
            model="fake",
            estimated=True,
        )

    outcome = run_module.execute_run(
        run_id, worker_settings=worker_settings, pool=pool, llm_call=_fake_llm
    )

    assert outcome.outcome == "report_created"
    assert outcome.notify_allowed is False

    with psycopg.connect(migrated_database_url) as conn:
        needs_rebuild = conn.execute(
            "SELECT needs_rebuild FROM projects WHERE id = %s", (project_id,)
        ).fetchone()[0]
        report_count = conn.execute(
            "SELECT COUNT(*) FROM reports WHERE project_id = %s AND version_no = %s",
            (project_id, outcome.version_no),
        ).fetchone()[0]
    assert needs_rebuild is True
    assert report_count >= 1


def test_epoch_unchanged_true_when_nothing_changed(
    migrated_database_url: str, worker_settings
) -> None:
    project_id = _setup_project_with_segment(migrated_database_url)
    pool = ConnectionPool(migrated_database_url)
    with pool.connection() as conn:
        epoch = conn.execute(
            "SELECT visibility_epoch FROM projects WHERE id = %s", (project_id,)
        ).fetchone()[0]
        assert run_module.epoch_unchanged(conn, project_id, epoch) is True
        assert run_module.epoch_unchanged(conn, project_id, epoch + 1) is False
