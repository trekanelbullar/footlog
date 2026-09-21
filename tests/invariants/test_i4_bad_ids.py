"""I4：実在しない番号の記述は根拠ありとして出ない（設計書 §5.3 (4)(10)、C10）。

偽の LLM が抽出で存在しない区切り番号（``S99-99``）を、組み立てで存在しない
出来事番号を返しても、その脚注が無く「（根拠なし）」が付くことを確かめる。
"""

import json
from uuid import UUID

import psycopg

import ai_hackathon_team_a.run as run_module
from ai_hackathon_team_a.db import ConnectionPool
from ai_hackathon_team_a.llm import LlmResult

# 抽出は S99-99（存在しない区切り）も返す。番号の検査で捨てられ、出来事は0件になる
# ため、根拠のある出来事も1件用意して、組み立て側の「存在しない出来事番号」を試す。
_FAKE_EXTRACT = json.dumps(
    {
        "events": [
            {
                "kind": "decision",
                "summary": "Xを採用",
                "reason": "安いから",
                "occurred_at": "2026-09-21T10:00:00+09:00",
                "segment_ids": ["S1-1", "S99-99"],
                "origin": "human_originated",
                "supersedes_event_no": None,
                "conflicts_with_event_no": None,
            }
        ],
        "suspected_injection_segment_ids": [],
    }
)
_FAKE_SUPPORT = json.dumps({"results": ["supported"]})
# 組み立ては、存在する出来事(1)と、存在しない出来事番号(999)の両方を根拠にする文を返す。
# purpose は「プロジェクトの目的」用で根拠0件でも「（根拠なし）」を付けない例外
# 見出しのため（設計書 §5.3 (10)）、この I4 の検査は他の見出し（current_state）で行う。
_FAKE_ASSEMBLE = json.dumps(
    {
        "sections": {
            "purpose": [],
            "current_state": [
                {"text": "実在する根拠の文", "event_nos": [1]},
                {"text": "でっち上げの根拠の文", "event_nos": [999]},
            ],
            "direction": [],
        },
        "summary_for_mail": "要約",
    }
)
_FAKE_JUDGE = json.dumps({"status": "pass", "notes": []})


def _fake_llm(stage: str, messages, *, run, json_mode=False, tools=None) -> LlmResult:
    text = {
        "EXTRACT": _FAKE_EXTRACT,
        "SUPPORT": _FAKE_SUPPORT,
        "ASSEMBLE": _FAKE_ASSEMBLE,
        "JUDGE": _FAKE_JUDGE,
    }[stage]
    return LlmResult(
        text=text, tool_calls=None, input_tokens=10, output_tokens=10, model="fake", estimated=True
    )


def _setup_and_run(migrated_database_url: str, worker_settings) -> tuple[UUID, UUID, int]:
    with psycopg.connect(migrated_database_url) as conn:
        project_id = conn.execute(
            "INSERT INTO projects (name, goal_description) VALUES ('P', 'G') RETURNING id"
        ).fetchone()[0]
        manager_id = conn.execute("SELECT gen_random_uuid()").fetchone()[0]
        conn.execute(
            "INSERT INTO project_members (project_id, user_id, email, role) "
            "VALUES (%s, %s, 'm@example.test', 'manager')",
            (project_id, manager_id),
        )
        source_id = conn.execute(
            "INSERT INTO project_sources (project_id, source_no, type, uploaded_by, "
            "recorded_at, visibility, next_seq) VALUES (%s, 1, 'conversation', %s, now(), "
            "'all', 1) RETURNING id",
            (project_id, manager_id),
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

    pool = ConnectionPool(migrated_database_url)
    worker_settings = worker_settings.model_copy(update={"daily_cost_limit_usd": 500.0})
    outcome = run_module.execute_run(
        run_id, worker_settings=worker_settings, pool=pool, llm_call=_fake_llm
    )
    assert outcome.outcome == "report_created"
    return project_id, manager_id, outcome.version_no


def test_extract_drops_unknown_segment_ids(migrated_database_url: str, worker_settings) -> None:
    project_id, _manager_id, _version_no = _setup_and_run(migrated_database_url, worker_settings)

    with psycopg.connect(migrated_database_url) as conn:
        rows = conn.execute(
            "SELECT segment_ids FROM events WHERE project_id = %s", (project_id,)
        ).fetchall()
    assert rows[0][0] == ["S1-1"]  # S99-99 は捨てられている


def test_assemble_marks_bad_event_no_as_no_evidence(
    migrated_database_url: str, worker_settings
) -> None:
    project_id, _manager_id, version_no = _setup_and_run(migrated_database_url, worker_settings)

    with psycopg.connect(migrated_database_url) as conn:
        body_markdown, evidence_catalog = conn.execute(
            "SELECT body_markdown, evidence_catalog FROM reports "
            "WHERE project_id = %s AND version_no = %s AND audience = 'all'",
            (project_id, version_no),
        ).fetchone()

    lines = {line.strip() for line in body_markdown.splitlines()}
    real_line = next(line for line in lines if "実在する根拠の文" in line)
    fake_line = next(line for line in lines if "でっち上げの根拠の文" in line)

    assert "[^" in real_line  # 実在する出来事の番号には脚注が付く
    assert "[^" not in fake_line  # 存在しない出来事番号(999)からは区切りを引けない
    assert "（根拠なし）" in fake_line
    assert evidence_catalog  # 実在する根拠の文の脚注は evidence_catalog にある
