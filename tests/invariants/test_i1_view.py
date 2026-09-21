"""I1：管理者限定の決定が member の W19 のどこにも出ないこと（設計書 §5.6、C4相当）。

manager の W19 には見える管理者限定の決定が、member の W19 の本文
（``body_markdown``）・図（``mermaid_dsl``）・脚注（``footnotes``）・
``node_evidence``・``timeline`` のどこにも含まれないことを、偽の LLM で確かめる。
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

_SECRET_SUMMARY = "レイオフ計画を承認"
_PUBLIC_SUMMARY = "配信基盤はResendを採用"
_FAKE_SUPPORT = json.dumps({"results": ["supported"]})


def _fake_llm(stage: str, messages, *, run, json_mode=False, tools=None) -> LlmResult:
    all_text = json.dumps(messages, ensure_ascii=False)
    if stage == "EXTRACT":
        if "S2-1" in all_text:  # managers_only の区切りを渡された呼び出し
            events = [
                {
                    "kind": "decision",
                    "summary": _SECRET_SUMMARY,
                    "reason": "非公開の事情",
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
        event_nos = [2] if has_secret else [1]
        text = json.dumps(
            {
                "sections": {
                    "purpose": [{"text": "目的の要約", "event_nos": event_nos}],
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
        version_all = conn.execute(
            "INSERT INTO source_versions (source_id, version_no, content_hash, extracted_text) "
            "VALUES (%s, 1, 'h', 't') RETURNING id",
            (source_all,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE project_sources SET current_version_id = %s WHERE id = %s",
            (version_all, source_all),
        )
        conn.execute(
            "INSERT INTO source_segments (project_id, source_version_id, label, seq, speaker, "
            "text, is_new, consumed) VALUES (%s, %s, 'S1-1', 1, 'user', %s, true, false)",
            (project_id, version_all, "配信基盤について相談"),
        )

        source_mgr = conn.execute(
            "INSERT INTO project_sources (project_id, source_no, type, uploaded_by, "
            "recorded_at, visibility, next_seq) VALUES (%s, 2, 'conversation', %s, now(), "
            "'managers_only', 1) RETURNING id",
            (project_id, manager_id),
        ).fetchone()[0]
        version_mgr = conn.execute(
            "INSERT INTO source_versions (source_id, version_no, content_hash, extracted_text) "
            "VALUES (%s, 1, 'h', 't') RETURNING id",
            (source_mgr,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE project_sources SET current_version_id = %s WHERE id = %s",
            (version_mgr, source_mgr),
        )
        conn.execute(
            "INSERT INTO source_segments (project_id, source_version_id, label, seq, speaker, "
            "text, is_new, consumed) VALUES (%s, %s, 'S2-1', 1, 'user', %s, true, false)",
            (project_id, version_mgr, "極秘のレイオフ計画について話し合った"),
        )
        conn.commit()
    return project_id, manager_id, member_id


def test_managers_only_decision_absent_from_every_field_of_member_view(
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

    manager_resp = api_client.get(
        f"/internal/projects/{project_id}/reports/{outcome.version_no}",
        headers=auth_headers(manager_id),
    )
    assert manager_resp.status_code == 200
    manager_body = manager_resp.json()
    # manager には管理者限定の決定が見える（どこかに現れる）。
    assert manager_body["audience"] == "managers"
    manager_text = json.dumps(manager_body, ensure_ascii=False)
    assert _SECRET_SUMMARY in manager_text

    member_resp = api_client.get(
        f"/internal/projects/{project_id}/reports/{outcome.version_no}",
        headers=auth_headers(member_id),
    )
    assert member_resp.status_code == 200
    member_body = member_resp.json()
    assert member_body["audience"] == "all"
    assert member_body["withheld"] is False

    assert _SECRET_SUMMARY not in member_body["body_markdown"]
    assert _SECRET_SUMMARY not in (member_body["mermaid_dsl"] or "")
    footnote_texts = [seg["text"] for segs in member_body["footnotes"].values() for seg in segs]
    assert all(_SECRET_SUMMARY not in text for text in footnote_texts)
    node_evidence_texts = [
        seg["text"] for segs in member_body["node_evidence"].values() for seg in segs
    ]
    assert all(_SECRET_SUMMARY not in text for text in node_evidence_texts)
    timeline_summaries = [item["summary"] for item in member_body["timeline"]]
    assert _SECRET_SUMMARY not in timeline_summaries
