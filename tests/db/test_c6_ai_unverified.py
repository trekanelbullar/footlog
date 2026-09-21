"""C6相当：AI の発言だけを根拠にした決定に「AIの提案のまま（未確認）」の印が付く

（設計書 §5.3 (4)(9)、spec E9）。
"""

import json
from uuid import UUID

import psycopg

import ai_hackathon_team_a.run as run_module
from ai_hackathon_team_a.db import ConnectionPool
from ai_hackathon_team_a.llm import LlmResult

_AI_TEXT = "次はReactを採用しましょう。理由はエコシステムが大きいからです。"


def _fake_llm(stage: str, messages, *, run, json_mode=False, tools=None) -> LlmResult:
    if stage == "EXTRACT":
        # LLM は（誤って）ai_verified を返すが、根拠の区切りが全部 speaker=ai なので
        # コード（correct_origin）が ai_unverified に補正するはず。
        text = json.dumps(
            {
                "events": [
                    {
                        "kind": "decision",
                        "summary": "Reactを採用",
                        "reason": "エコシステムが大きいから",
                        "occurred_at": "2026-09-21T10:00:00+09:00",
                        "segment_ids": ["S1-1"],
                        "origin": "ai_verified",
                        "supersedes_event_no": None,
                        "conflicts_with_event_no": None,
                    }
                ],
                "suspected_injection_segment_ids": [],
            }
        )
    elif stage == "SUPPORT":
        text = json.dumps({"results": ["supported"]})
    elif stage == "ASSEMBLE":
        text = json.dumps(
            {
                "sections": {
                    "decisions": [{"text": "Reactを採用", "event_nos": [1]}],
                    "reasons": [{"text": "エコシステムが大きいから", "event_nos": [1]}],
                    "rejected_options": [],
                    "next_steps": [],
                    "current_status": [],
                },
                "summary_for_mail": "要約",
            }
        )
    elif stage == "JUDGE":
        text = json.dumps({"status": "pass", "notes": []})
    else:
        raise AssertionError(f"unexpected stage: {stage}")
    return LlmResult(
        text=text, tool_calls=None, input_tokens=10, output_tokens=10, model="fake", estimated=True
    )


def _setup_project_with_ai_only_segment(database_url: str) -> UUID:
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
            "VALUES (%s, 1, 'h', %s) RETURNING id",
            (source_id, _AI_TEXT),
        ).fetchone()[0]
        conn.execute(
            "UPDATE project_sources SET current_version_id = %s WHERE id = %s",
            (version_id, source_id),
        )
        # 根拠の区切りの話者を 'ai' にする（人の確認は入っていない）。
        conn.execute(
            "INSERT INTO source_segments (project_id, source_version_id, label, seq, "
            "speaker, text, is_new, consumed) VALUES (%s, %s, 'S1-1', 1, 'ai', %s, true, false)",
            (project_id, version_id, _AI_TEXT),
        )
        # 出どころの印は「差分」モードの見出し（decisions）にしか付かないため、
        # 先に1件ダミーの baseline 版があることにして、この実行を diff モードにする。
        prior_run_id = conn.execute(
            "INSERT INTO runs (project_id, trigger, status) VALUES (%s, 'manual', 'done') "
            "RETURNING id",
            (project_id,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO reports (project_id, run_id, version_no, audience, kind, "
            "judge_status, body_markdown, summary_for_mail) "
            "VALUES (%s, %s, 0, 'all', 'baseline', 'pass', '(baseline)', '(baseline)')",
            (project_id, prior_run_id),
        )
        run_id = conn.execute(
            "INSERT INTO runs (project_id, trigger, status) VALUES (%s, 'manual', 'queued') "
            "RETURNING id",
            (project_id,),
        ).fetchone()[0]
        conn.commit()
    return project_id, run_id


def test_ai_only_decision_is_marked_unverified(migrated_database_url: str, worker_settings) -> None:
    project_id, run_id = _setup_project_with_ai_only_segment(migrated_database_url)
    pool = ConnectionPool(migrated_database_url)
    worker_settings = worker_settings.model_copy(update={"daily_cost_limit_usd": 500.0})

    outcome = run_module.execute_run(
        run_id, worker_settings=worker_settings, pool=pool, llm_call=_fake_llm
    )
    assert outcome.outcome == "report_created"

    with psycopg.connect(migrated_database_url) as conn:
        origin, reason = conn.execute(
            "SELECT origin, reason FROM events WHERE project_id = %s AND event_no = 1",
            (project_id,),
        ).fetchone()
        body_markdown, flags = conn.execute(
            "SELECT body_markdown, flags FROM reports "
            "WHERE project_id = %s AND version_no = %s AND audience = 'all'",
            (project_id, outcome.version_no),
        ).fetchone()

    # コードの補正（pipeline.extract.correct_origin）が ai_verified を ai_unverified に直す。
    assert origin == "ai_unverified"
    assert reason == "エコシステムが大きいから"
    assert "〔AIの提案のまま（未確認）〕" in body_markdown
    assert flags["unverified_ai_count"] == 1
