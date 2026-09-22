"""持ち越しの区切りから、既存の出来事と同じ内容が重複して追記されないこと。

手元の本物の LLM での確認で見つかった不具合の修正（設計書 §5.3 (2)(3)、
``run.py`` の重複検査）。前の実行で出来事になった区切りが、何らかの理由で
まだ ``consumed = false`` のまま残っていると、次の実行がそれを再び抽出して
しまい、図と本文に同じ出来事が2回ずつ載っていた。抽出結果が「今も有効な
出来事」と種類・根拠の区切りの集合の両方で一致するときは、出来事として
追記しない（区切りは既存の出来事に含まれる扱いにして消費済みにする）。
"""

import json
from uuid import UUID

import psycopg

import ai_hackathon_team_a.run as run_module
from ai_hackathon_team_a.db import ConnectionPool
from ai_hackathon_team_a.llm import LlmResult

_FAKE_SUPPORT = json.dumps({"results": ["supported"]})
_FAKE_JUDGE = json.dumps({"status": "pass", "notes": []})
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


def _extract_text(summary: str) -> str:
    return json.dumps(
        {
            "events": [
                {
                    "kind": "decision",
                    "summary": summary,
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


def _fake_llm(extract_text: str):
    def _call(stage: str, messages, *, run, json_mode=False, tools=None) -> LlmResult:
        text = {
            "EXTRACT": extract_text,
            "SUPPORT": _FAKE_SUPPORT,
            "ASSEMBLE": _FAKE_ASSEMBLE,
            "JUDGE": _FAKE_JUDGE,
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
            "VALUES (%s, 1, 'h', 'Xを採用する') RETURNING id",
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


def _insert_queued_run(database_url: str, project_id: UUID) -> UUID:
    with psycopg.connect(database_url) as conn:
        run_id = conn.execute(
            "INSERT INTO runs (project_id, trigger, status) VALUES (%s, 'manual', 'queued') "
            "RETURNING id",
            (project_id,),
        ).fetchone()[0]
        conn.commit()
    return run_id


def test_carried_over_segment_does_not_duplicate_existing_event(
    migrated_database_url: str, worker_settings
) -> None:
    project_id = _setup_project_with_segment(migrated_database_url)
    pool = ConnectionPool(migrated_database_url)
    worker_settings = worker_settings.model_copy(update={"daily_cost_limit_usd": 500.0})

    run_id_1 = _insert_queued_run(migrated_database_url, project_id)
    first = run_module.execute_run(
        run_id_1,
        worker_settings=worker_settings,
        pool=pool,
        llm_call=_fake_llm(_extract_text("Xを採用")),
    )
    assert first.outcome == "report_created"

    # 手元の確認で実際に起きた状況の代わり：何らかの理由で区切りがまだ
    # consumed=false のまま持ち越されている。
    with psycopg.connect(migrated_database_url) as conn:
        conn.execute("UPDATE source_segments SET consumed = false WHERE label = 'S1-1'")
        conn.commit()

    # 偽の LLM が、既存の出来事（kind=decision、根拠=S1-1）と同じ組・種類・
    # 根拠の出来事を再び返す（文言だけ違っても、判定は kind と segment_ids で行う）。
    run_id_2 = _insert_queued_run(migrated_database_url, project_id)
    second = run_module.execute_run(
        run_id_2,
        worker_settings=worker_settings,
        pool=pool,
        llm_call=_fake_llm(_extract_text("Xを採用（再掲）")),
    )

    # 追記する出来事が無いので、この実行は no_new_events で終わる（重複が
    # 捨てられ、新しい出来事が0件のため）。
    assert second.outcome == "no_new_events"

    with psycopg.connect(migrated_database_url) as conn:
        event_count = conn.execute(
            "SELECT COUNT(*) FROM events WHERE project_id = %s", (project_id,)
        ).fetchone()[0]
        report_count = conn.execute(
            "SELECT COUNT(*) FROM reports WHERE project_id = %s", (project_id,)
        ).fetchone()[0]
        consumed = conn.execute(
            "SELECT consumed FROM source_segments WHERE label = 'S1-1'"
        ).fetchone()[0]

    assert event_count == 1  # 重複して追記されていない
    assert report_count == 1  # レポートも増えていない
    assert consumed is True  # 既存の出来事に含まれる扱いで、持ち越しにしない
