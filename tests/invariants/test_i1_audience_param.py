"""I1：W19 は版の種類（``audience``）を入力に取らない（設計書 §2.4、【B-X3】）。

member が ``?audience=managers`` を付けても、全員向けの版しか返らない。
"""

import json
from collections.abc import Callable
from uuid import UUID

import psycopg
from fastapi.testclient import TestClient

import ai_hackathon_team_a.run as run_module
from ai_hackathon_team_a.db import ConnectionPool
from ai_hackathon_team_a.llm import LlmResult

AuthHeaders = Callable[..., dict[str, str]]

_SECRET_SUMMARY = "非公開の決定XYZ"
_PUBLIC_SUMMARY = "公開の決定ABC"
_FAKE_SUPPORT = json.dumps({"results": ["supported"]})


def _fake_llm(stage: str, messages, *, run, json_mode=False, tools=None) -> LlmResult:
    all_text = json.dumps(messages, ensure_ascii=False)
    if stage == "EXTRACT":
        if "S2-1" in all_text:
            events = [
                {
                    "kind": "decision",
                    "summary": _SECRET_SUMMARY,
                    "reason": None,
                    "occurred_at": "2026-09-21T10:00:00+09:00",
                    "segment_ids": ["S2-1"],
                    "origin": "human_originated",
                    "supersedes_event_no": None,
                    "conflicts_with_event_no": None,
                }
            ]
        else:
            events = [
                {
                    "kind": "decision",
                    "summary": _PUBLIC_SUMMARY,
                    "reason": None,
                    "occurred_at": "2026-09-21T09:00:00+09:00",
                    "segment_ids": ["S1-1"],
                    "origin": "human_originated",
                    "supersedes_event_no": None,
                    "conflicts_with_event_no": None,
                }
            ]
        text = json.dumps({"events": events, "suspected_injection_segment_ids": []})
    elif stage == "SUPPORT":
        text = _FAKE_SUPPORT
    elif stage == "ASSEMBLE":
        has_secret = _SECRET_SUMMARY in all_text
        text = json.dumps(
            {
                "sections": {
                    "purpose": [{"text": "要約", "event_nos": [2 if has_secret else 1]}],
                    "current_state": [],
                    "direction": [],
                },
                "summary_for_mail": "要約",
            }
        )
    else:
        text = json.dumps({"status": "pass", "notes": []})
    return LlmResult(
        text=text, tool_calls=None, input_tokens=10, output_tokens=10, model="fake", estimated=True
    )


def _setup_project(migrated_database_url: str) -> tuple[UUID, UUID, UUID]:
    with psycopg.connect(migrated_database_url) as conn:
        project_id = conn.execute(
            "INSERT INTO projects (name, goal_description) VALUES ('P', 'G') RETURNING id"
        ).fetchone()[0]
        manager_id = conn.execute("SELECT gen_random_uuid()").fetchone()[0]
        member_id = conn.execute("SELECT gen_random_uuid()").fetchone()[0]
        conn.execute(
            "INSERT INTO project_members (project_id, user_id, email, role) VALUES "
            "(%s, %s, 'mgr@example.test', 'manager'), (%s, %s, 'mem@example.test', 'member')",
            (project_id, manager_id, project_id, member_id),
        )

        source_all = conn.execute(
            "INSERT INTO project_sources (project_id, source_no, type, uploaded_by, "
            "recorded_at, visibility, next_seq) VALUES (%s, 1, 'conversation', %s, now(), "
            "'all', 1) RETURNING id",
            (project_id, manager_id),
        ).fetchone()[0]
        v1 = conn.execute(
            "INSERT INTO source_versions (source_id, version_no, content_hash, extracted_text) "
            "VALUES (%s, 1, 'h', 't') RETURNING id",
            (source_all,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE project_sources SET current_version_id = %s WHERE id = %s", (v1, source_all)
        )
        conn.execute(
            "INSERT INTO source_segments (project_id, source_version_id, label, seq, speaker, "
            "text, is_new, consumed) VALUES (%s, %s, 'S1-1', 1, 'user', %s, true, false)",
            (project_id, v1, "公開の相談"),
        )

        source_mgr = conn.execute(
            "INSERT INTO project_sources (project_id, source_no, type, uploaded_by, "
            "recorded_at, visibility, next_seq) VALUES (%s, 2, 'conversation', %s, now(), "
            "'managers_only', 1) RETURNING id",
            (project_id, manager_id),
        ).fetchone()[0]
        v2 = conn.execute(
            "INSERT INTO source_versions (source_id, version_no, content_hash, extracted_text) "
            "VALUES (%s, 1, 'h', 't') RETURNING id",
            (source_mgr,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE project_sources SET current_version_id = %s WHERE id = %s", (v2, source_mgr)
        )
        conn.execute(
            "INSERT INTO source_segments (project_id, source_version_id, label, seq, speaker, "
            "text, is_new, consumed) VALUES (%s, %s, 'S2-1', 1, 'user', %s, true, false)",
            (project_id, v2, "非公開の相談"),
        )
        conn.commit()
    return project_id, manager_id, member_id


def test_member_query_param_audience_managers_is_ignored(
    api_client: TestClient, migrated_database_url: str, worker_settings, auth_headers: AuthHeaders
) -> None:
    project_id, manager_id, member_id = _setup_project(migrated_database_url)
    pool = ConnectionPool(migrated_database_url)
    worker_settings = worker_settings.model_copy(update={"daily_cost_limit_usd": 500.0})

    with psycopg.connect(migrated_database_url) as conn:
        run_id = conn.execute(
            "INSERT INTO runs (project_id, trigger, status) VALUES (%s, 'manual', 'queued') "
            "RETURNING id",
            (project_id,),
        ).fetchone()[0]
        conn.commit()

    outcome = run_module.execute_run(
        run_id, worker_settings=worker_settings, pool=pool, llm_call=_fake_llm
    )
    assert outcome.outcome == "report_created"

    # manager が確かに managers 版を持っていることを確かめておく（前提の確認）。
    manager_resp = api_client.get(
        f"/internal/projects/{project_id}/reports/{outcome.version_no}",
        params={"audience": "managers"},
        headers=auth_headers(manager_id),
    )
    assert manager_resp.json()["audience"] == "managers"

    member_resp = api_client.get(
        f"/internal/projects/{project_id}/reports/{outcome.version_no}",
        params={"audience": "managers"},
        headers=auth_headers(member_id),
    )
    assert member_resp.status_code == 200
    body = member_resp.json()
    assert body["audience"] == "all"
    assert _SECRET_SUMMARY not in json.dumps(body, ensure_ascii=False)
